import type {
  ComposerDraftResponse,
  ReversionCapabilitiesResponse,
  ReversionResponse,
} from "../api/generated/types.gen";
import {
  vCancelReversionApiV1ReversionsJobIdDeleteResponse,
  vCreateReversionApiV1ReversionsPostResponse,
  vGetReversionApiV1ReversionsJobIdGetResponse,
  vGetReversionCapabilitiesApiV1ReversionsCapabilitiesGetResponse,
  vListReversionsApiV1ReversionsGetResponse,
  vComposerDraftResponse,
} from "../api/generated/valibot.gen";
import { ApiError, ApiTransport, type JsonResult } from "../api/transport";
import type { SavedReversionInputs } from "../conversion/persistence";

const CAPABILITIES_SCHEMA_VERSION = 1;
const POLL_START_MS = 1_000;
const POLL_MAX_MS = 10_000;
const POLL_MAX_FAILURES = 6;
const SAFE_FAILURE = "The request could not be completed. Try again.";
const TERMINAL_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);

export type ReversionSubmissionOptions = {
  extraction: "anydoc" | "slides" | "marp";
  include_images: boolean;
  include_notes: boolean;
};

export type ReversionState = {
  phase: "loading" | "ready" | "unavailable";
  capabilities?: ReversionCapabilitiesResponse;
  extensions: string[];
  options?: ReversionSubmissionOptions;
  source?: File;
  recent: ReversionResponse[];
  active?: ReversionResponse;
  submitting: boolean;
  cancelling: boolean;
  composerPending: boolean;
  error?: string;
  notice?: string;
};

type Listener = (state: ReversionState) => void;
type ExpireSession = () => void;
type Scheduler = (
  callback: () => void,
  delay: number,
) => ReturnType<typeof setTimeout>;

export class ReversionController {
  private state: ReversionState = initialState();
  private readonly listeners = new Set<Listener>();
  private loadGeneration = 0;
  private submissionGeneration = 0;
  private jobGeneration = 0;
  private loadRequest?: AbortController;
  private submissionRequest?: AbortController;
  private jobRequest?: AbortController;
  private cancellationRequest?: AbortController;
  private pollTimer?: ReturnType<typeof setTimeout>;
  private pollDelay = POLL_START_MS;
  private pollFailures = 0;
  private idempotencyKey?: string;
  private submissionOptions?: ReversionSubmissionOptions;
  private inputGeneration = 0;
  private composerGeneration = 0;
  private composerRequest?: AbortController;

  constructor(
    private readonly api: ApiTransport = new ApiTransport(),
    private readonly expireSession: ExpireSession = () => undefined,
    private readonly randomUUID: () => string = () => crypto.randomUUID(),
    private readonly schedule: Scheduler = (callback, delay) =>
      setTimeout(callback, delay),
    private readonly cancelSchedule: (
      timer: ReturnType<typeof setTimeout>,
    ) => void = clearTimeout,
  ) {}

  snapshot = (): ReversionState => this.state;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  async load(): Promise<void> {
    this.loadRequest?.abort();
    const request = new AbortController();
    this.loadRequest = request;
    const generation = ++this.loadGeneration;
    this.publish(initialState());
    try {
      const [capabilities, recent] = await Promise.all([
        this.api.json(
          "/api/v1/reversions/capabilities",
          vGetReversionCapabilitiesApiV1ReversionsCapabilitiesGetResponse,
          { signal: request.signal },
        ),
        this.api.json(
          "/api/v1/reversions?offset=0&limit=10",
          vListReversionsApiV1ReversionsGetResponse,
          { signal: request.signal },
        ),
      ]);
      if (generation !== this.loadGeneration) return;
      const extensions = validateCapabilities(capabilities);
      this.publish({
        ...initialState(),
        phase: "ready",
        capabilities,
        extensions,
        options: defaultOptions(capabilities),
        recent: recent.items.filter((job) => job.state !== "expired"),
      });
    } catch (error) {
      if (generation !== this.loadGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      this.publish({ ...initialState(), phase: "unavailable" });
    }
  }

  setSource(files: FileList | File[] | null): void {
    if (!files || files.length !== 1) {
      this.invalidateSubmission();
      this.publish({
        ...this.state,
        source: undefined,
        submitting: false,
        error:
          files && files.length > 1
            ? "Choose exactly one supported document."
            : undefined,
        notice: undefined,
      });
      return;
    }
    const file = files[0];
    if (!file) return;
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      source: file,
      options: this.state.capabilities
        ? defaultOptions(this.state.capabilities)
        : undefined,
      submitting: false,
      error: undefined,
      notice: undefined,
    });
  }

  inputVersion(): number {
    return this.inputGeneration;
  }

  restoreInputs(
    inputs: SavedReversionInputs,
    expectedVersion: number,
  ): boolean {
    if (
      this.state.phase !== "ready" ||
      this.inputGeneration !== expectedVersion
    )
      return false;
    const savedOptions = inputs.options;
    const validOptions =
      savedOptions &&
      !validateOptions(savedOptions, inputs.source, this.state.capabilities);
    this.publish({
      ...this.state,
      source: inputs.source,
      options: validOptions ? savedOptions : this.state.options,
      ...(savedOptions && !validOptions
        ? {
            notice:
              "Saved extraction settings are no longer available. Review the current options.",
          }
        : {}),
    });
    if (inputs.activeJobId) void this.openJob(inputs.activeJobId);
    return true;
  }

  async createComposerDraft(): Promise<string | undefined> {
    if (this.state.phase !== "ready" || this.state.composerPending) return;
    const invalid = validateSource(
      this.state.source,
      this.state.extensions,
      this.state.capabilities?.maximum_upload_bytes,
    );
    if (invalid) {
      this.publish({ ...this.state, error: invalid });
      return;
    }
    if (!/\.(docx|pptx|pdf)$/i.test(this.state.source!.name)) {
      this.publish({
        ...this.state,
        error:
          "Composer accepts selected DOCX, PPTX, or PDF sources. This file remains selected in Revert.",
      });
      return;
    }
    const form = new FormData();
    form.append("source", this.state.source!);
    return this.submitComposerDraft(() =>
      this.api.multipartWithMetadata(
        "/api/v1/composer/drafts",
        form,
        vComposerDraftResponse,
        { csrf: true, signal: this.composerRequest?.signal },
      ),
    );
  }

  async handoffActiveResult(): Promise<string | undefined> {
    const active = this.state.active;
    if (!active || active.state !== "succeeded" || this.state.composerPending)
      return;
    return this.submitComposerDraft(() =>
      this.api.jsonWithMetadata(
        `/api/v1/composer/drafts/from-reversion/${active.id}`,
        vComposerDraftResponse,
        {
          body: JSON.stringify({ title: null }),
          csrf: true,
          method: "POST",
          signal: this.composerRequest?.signal,
        },
      ),
    );
  }

  private async submitComposerDraft(
    submit: () => Promise<JsonResult<ComposerDraftResponse>>,
  ): Promise<string | undefined> {
    this.composerRequest?.abort();
    const request = new AbortController();
    this.composerRequest = request;
    const generation = ++this.composerGeneration;
    this.publish({ ...this.state, composerPending: true, error: undefined });
    try {
      const result = await submit();
      if (generation !== this.composerGeneration) return;
      if (
        result.status !== 201 ||
        result.location !== `/api/v1/composer/drafts/${result.data.id}`
      )
        throw unexpected(result.status);
      this.publish({ ...this.state, composerPending: false });
      return result.data.id;
    } catch (error) {
      if (generation !== this.composerGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      this.publish({
        ...this.state,
        composerPending: false,
        error: errorMessage(error, "The Composer draft could not be created."),
      });
    }
  }

  setExtraction(extraction: ReversionSubmissionOptions["extraction"]): void {
    this.setOption({ extraction });
  }

  setIncludeNotes(include_notes: boolean): void {
    this.setOption({ include_notes });
  }

  setIncludeImages(include_images: boolean): void {
    this.setOption({ include_images });
  }

  async submit(): Promise<void> {
    if (this.state.phase !== "ready" || this.state.submitting) return;
    const invalid = validateSource(
      this.state.source,
      this.state.extensions,
      this.state.capabilities?.maximum_upload_bytes,
    );
    if (invalid) {
      this.publish({ ...this.state, error: invalid, notice: undefined });
      return;
    }
    const optionError = validateOptions(
      this.state.options,
      this.state.source,
      this.state.capabilities,
    );
    if (optionError) {
      this.publish({ ...this.state, error: optionError, notice: undefined });
      return;
    }
    const request = new AbortController();
    this.submissionRequest?.abort();
    this.submissionRequest = request;
    const generation = ++this.submissionGeneration;
    const key = (this.idempotencyKey ??= this.randomUUID());
    const options = (this.submissionOptions ??= { ...this.state.options! });
    const form = new FormData();
    form.append("source", this.state.source!);
    form.append("extraction", options.extraction);
    form.append("include_notes", String(options.include_notes));
    form.append("include_images", String(options.include_images));
    this.publish({
      ...this.state,
      submitting: true,
      error: undefined,
      notice: "Submitting your document…",
    });
    try {
      const accepted = await this.api.multipartWithMetadata(
        "/api/v1/reversions",
        form,
        vCreateReversionApiV1ReversionsPostResponse,
        { csrf: true, idempotencyKey: key, signal: request.signal },
      );
      if (generation !== this.submissionGeneration) return;
      if (
        accepted.status !== 202 ||
        accepted.location !== `/api/v1/reversions/${accepted.data.id}` ||
        accepted.retryAfterSeconds === undefined
      )
        throw unexpected(accepted.status);
      this.idempotencyKey = undefined;
      this.submissionOptions = undefined;
      this.publish({
        ...this.state,
        submitting: false,
        active: accepted.data,
        recent: upsertRecent(this.state.recent, accepted.data),
        notice: undefined,
      });
      this.activateJob(accepted.data.id, accepted.retryAfterSeconds * 1_000);
      if (!isTerminal(accepted.data))
        this.schedulePoll(accepted.data.id, this.jobGeneration);
    } catch (error) {
      if (generation !== this.submissionGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      const definitive = isDefinitiveSubmissionRejection(error);
      if (definitive) {
        this.idempotencyKey = undefined;
        this.submissionOptions = undefined;
      }
      this.publish({
        ...this.state,
        submitting: false,
        notice: undefined,
        error: definitive
          ? errorMessage(error)
          : `${errorMessage(error, "The document could not be submitted.")} The outcome is unknown; retry manually to reuse the same request key.`,
      });
    }
  }

  async openJob(jobId: string): Promise<void> {
    this.inputGeneration += 1;
    this.activateJob(jobId);
    await this.poll(jobId, this.jobGeneration);
  }

  async cancel(): Promise<void> {
    const active = this.state.active;
    if (!active || this.state.cancelling || !isCancellable(active)) return;
    this.clearPoll();
    this.jobRequest?.abort();
    this.cancellationRequest?.abort();
    const request = new AbortController();
    this.cancellationRequest = request;
    const generation = this.jobGeneration;
    this.publish({ ...this.state, cancelling: true, error: undefined });
    try {
      const job = await this.api.cancel(
        `/api/v1/reversions/${active.id}`,
        vCancelReversionApiV1ReversionsJobIdDeleteResponse,
        request.signal,
      );
      if (generation !== this.jobGeneration) return;
      this.publishJob(job, { cancelling: false });
      if (!isTerminal(job)) this.schedulePoll(job.id, generation);
    } catch (error) {
      if (generation !== this.jobGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      this.publish({
        ...this.state,
        cancelling: false,
        error: errorMessage(
          error,
          "Cancellation could not be requested. Try again.",
        ),
      });
      this.schedulePoll(active.id, generation);
    }
  }

  async download(): Promise<{ blob: Blob; filename: string } | undefined> {
    const active = this.state.active;
    if (!active || active.state !== "succeeded") return undefined;
    const generation = this.jobGeneration;
    const stillSelected = () =>
      generation === this.jobGeneration && this.state.active?.id === active.id;
    try {
      const response = await this.api.download(
        `/api/v1/reversions/${active.id}/result`,
      );
      if (!stillSelected()) return undefined;
      const filename = validatedDownloadFilename(response);
      const blob = await response.blob();
      return stillSelected() ? { blob, filename } : undefined;
    } catch (error) {
      if (!stillSelected() || isAbort(error)) return undefined;
      if (this.authoritativeExpiry(error)) return undefined;
      this.publish({
        ...this.state,
        error: errorMessage(error, "The result could not be downloaded."),
      });
      return undefined;
    }
  }

  dispose(): void {
    this.composerGeneration += 1;
    this.composerRequest?.abort();
    this.loadGeneration += 1;
    this.submissionGeneration += 1;
    this.jobGeneration += 1;
    this.loadRequest?.abort();
    this.submissionRequest?.abort();
    this.jobRequest?.abort();
    this.cancellationRequest?.abort();
    this.clearPoll();
  }

  private async poll(jobId: string, generation: number): Promise<void> {
    if (generation !== this.jobGeneration) return;
    this.clearPoll();
    this.jobRequest?.abort();
    const request = new AbortController();
    this.jobRequest = request;
    try {
      const job = await this.api.json(
        `/api/v1/reversions/${jobId}`,
        vGetReversionApiV1ReversionsJobIdGetResponse,
        { signal: request.signal },
      );
      if (generation !== this.jobGeneration) return;
      this.publishJob(job, { error: undefined });
      this.pollFailures = 0;
      if (!isTerminal(job)) {
        this.pollDelay = nextPollDelay(this.pollDelay);
        this.schedulePoll(jobId, generation);
      }
    } catch (error) {
      if (generation !== this.jobGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      this.pollFailures += 1;
      const willRetry =
        this.pollFailures < POLL_MAX_FAILURES &&
        (!(error instanceof ApiError) || ![401, 404].includes(error.status));
      const detail = errorMessage(error, "Status is temporarily unavailable.");
      this.publish({
        ...this.state,
        error: willRetry
          ? `${detail} Polling will continue.`
          : `${detail} Polling has stopped. Reopen the job to try again.`,
      });
      if (willRetry) {
        this.pollDelay = nextPollDelay(this.pollDelay);
        this.schedulePoll(jobId, generation);
      }
    }
  }

  private activateJob(jobId: string, initialDelay = POLL_START_MS): void {
    this.invalidateComposer();
    this.jobGeneration += 1;
    this.jobRequest?.abort();
    this.cancellationRequest?.abort();
    this.clearPoll();
    this.pollDelay = initialDelay;
    this.pollFailures = 0;
    const known = this.state.recent.find((job) => job.id === jobId);
    this.publish({
      ...this.state,
      ...(known ? { active: known, error: undefined } : {}),
      cancelling: false,
    });
  }

  private schedulePoll(jobId: string, generation: number): void {
    if (generation !== this.jobGeneration) return;
    this.clearPoll();
    this.pollTimer = this.schedule(
      () => void this.poll(jobId, generation),
      this.pollDelay,
    );
  }

  private clearPoll(): void {
    const cancelSchedule = this.cancelSchedule;
    if (this.pollTimer !== undefined) cancelSchedule(this.pollTimer);
    this.pollTimer = undefined;
  }

  private publishJob(
    job: ReversionResponse,
    extra: Partial<ReversionState>,
  ): void {
    this.publish({
      ...this.state,
      ...extra,
      active: job,
      recent: upsertRecent(this.state.recent, job),
    });
  }

  private invalidateSubmission(): void {
    this.inputGeneration += 1;
    this.invalidateComposer();
    this.submissionGeneration += 1;
    this.submissionRequest?.abort();
    this.idempotencyKey = undefined;
    this.submissionOptions = undefined;
  }

  private invalidateComposer(): void {
    this.composerGeneration += 1;
    this.composerRequest?.abort();
    this.state = { ...this.state, composerPending: false };
  }

  private setOption(option: Partial<ReversionSubmissionOptions>): void {
    if (!this.state.options || !this.state.capabilities) return;
    const options = { ...this.state.options, ...option };
    const invalid = validateOptions(
      options,
      this.state.source,
      this.state.capabilities,
    );
    if (invalid) return;
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      options,
      error: undefined,
      notice: undefined,
    });
  }

  private authoritativeExpiry(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.status !== 401) return false;
    this.dispose();
    this.expireSession();
    return true;
  }

  private publish(state: ReversionState): void {
    this.state = state;
    for (const listener of this.listeners) listener(state);
  }
}

export function validateCapabilities(
  capabilities: ReversionCapabilitiesResponse,
): string[] {
  if (
    capabilities.schema_version !== CAPABILITIES_SCHEMA_VERSION ||
    !capabilities.execution.local ||
    capabilities.execution.ocr ||
    capabilities.execution.hosted_fallback
  )
    throw new TypeError("Unsupported reverse capabilities");
  const extensions = [
    ...new Set(
      capabilities.format_families.flatMap((format) => format.extensions),
    ),
  ];
  if (
    extensions.length === 0 ||
    extensions.some(
      (extension) =>
        !/^\.[a-z0-9]+$/.test(extension) ||
        extension !== extension.toLowerCase(),
    )
  )
    throw new TypeError("Unsupported reverse capabilities");
  const extraction = capabilities.extraction;
  if (
    !extraction.modes.length ||
    !extraction.modes.every(
      (mode) => mode === "anydoc" || mode === "slides" || mode === "marp",
    ) ||
    !extraction.modes.includes(extraction.default_mode) ||
    !extraction.structured_extensions.length ||
    !extraction.structured_extensions.every((extension) =>
      extensions.includes(extension),
    )
  )
    throw new TypeError("Unsupported reverse capabilities");
  return extensions;
}

function defaultOptions(
  capabilities: ReversionCapabilitiesResponse,
): ReversionSubmissionOptions {
  return {
    extraction: capabilities.extraction.default_mode,
    include_images: capabilities.extraction.include_images_default,
    include_notes: capabilities.extraction.include_notes_default,
  };
}

export function validateOptions(
  options: ReversionSubmissionOptions | undefined,
  source: File | undefined,
  capabilities: ReversionCapabilitiesResponse | undefined,
): string | undefined {
  if (!options || !source || !capabilities)
    return "Choose a supported document.";
  if (!capabilities.extraction.modes.includes(options.extraction))
    return "The selected extraction mode is not available.";
  if (
    options.extraction !== "anydoc" &&
    !capabilities.extraction.structured_extensions.some((extension) =>
      source.name.toLowerCase().endsWith(extension),
    )
  )
    return "Slide-oriented extraction is available only for supported PowerPoint files.";
  if (
    options.extraction === "anydoc" &&
    (!options.include_notes || !options.include_images)
  )
    return "Anydoc extraction requires notes and images to remain included.";
  return undefined;
}

export function validateSource(
  file: File | undefined,
  extensions: string[],
  maximumBytes: number | undefined,
): string | undefined {
  if (!file) return "Choose a supported document.";
  const lowerName = file.name.toLowerCase();
  if (!extensions.some((extension) => lowerName.endsWith(extension)))
    return "Choose a document type listed by the service.";
  if (file.size < 1) return "The selected file is empty.";
  if (!maximumBytes || file.size > maximumBytes)
    return "The selected file exceeds the configured upload limit.";
  return undefined;
}

export function nextPollDelay(currentMilliseconds: number): number {
  return Math.min(Math.ceil(currentMilliseconds * 1.6), POLL_MAX_MS);
}

export function statusPresentation(job: ReversionResponse): string {
  if (job.state === "failed") {
    if (job.error_code === "needs_ocr")
      return "This document requires OCR, which is not available in local conversion.";
    return job.error_message || "The conversion to Markdown failed.";
  }
  if (job.state === "cancelled") return "The conversion was cancelled.";
  if (job.state === "expired")
    return "This conversion has expired and its result is no longer available.";
  if (job.state === "succeeded") return "Your Markdown result is ready.";
  if (job.cancel_requested)
    return "Cancellation requested. Waiting for the worker to stop.";
  if (job.state === "queued") return "Your document is queued.";
  const steps: Record<string, string> = {
    isolating: "Preparing an isolated local workspace",
    converting: "Converting the document to Markdown",
    validating: "Validating the Markdown package",
    publishing: "Publishing the result",
  };
  return steps[job.step] ?? "Processing the document";
}

export function isCancellable(job: ReversionResponse): boolean {
  return !isTerminal(job) && !job.cancel_requested;
}

function initialState(): ReversionState {
  return {
    phase: "loading",
    extensions: [],
    recent: [],
    submitting: false,
    cancelling: false,
    composerPending: false,
  };
}

function isTerminal(job: ReversionResponse): boolean {
  return TERMINAL_STATES.has(job.state);
}

function upsertRecent(
  recent: ReversionResponse[],
  job: ReversionResponse,
): ReversionResponse[] {
  return [job, ...recent.filter((candidate) => candidate.id !== job.id)]
    .filter((candidate) => candidate.state !== "expired")
    .slice(0, 10);
}

function errorMessage(error: unknown, fallback = SAFE_FAILURE): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isDefinitiveSubmissionRejection(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    (error.status >= 400 && error.status < 500) ||
    (error.status === 503 && error.code === "REVERSION_QUEUE_CAPACITY_EXCEEDED")
  );
}

function unexpected(status: number): ApiError {
  return new ApiError(
    status,
    "UNEXPECTED_RESPONSE",
    "The service returned an unexpected response.",
  );
}

function validatedDownloadFilename(response: Response): string {
  const mediaType = response.headers
    .get("content-type")
    ?.split(";", 1)[0]
    ?.trim()
    .toLowerCase();
  if (
    !["application/zip", "text/markdown"].includes(mediaType ?? "") ||
    response.headers.get("cache-control")?.toLowerCase() !==
      "private, no-store" ||
    response.headers.get("x-content-type-options")?.toLowerCase() !== "nosniff"
  )
    throw unexpected(response.status);
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = /(?:^|;)\s*filename\*=UTF-8''([^;]+)(?:;|$)/i.exec(
    disposition,
  )?.[1];
  const quoted = /(?:^|;)\s*filename="([^"\\\r\n]+)"(?:;|$)/i.exec(
    disposition,
  )?.[1];
  let filename: string | undefined;
  try {
    filename = encoded ? decodeURIComponent(encoded) : quoted;
  } catch {
    filename = undefined;
  }
  if (
    !filename ||
    filename.includes("/") ||
    filename.includes("\\") ||
    /[\r\n]/.test(filename) ||
    !/\.(md|zip)$/i.test(filename)
  )
    throw unexpected(response.status);
  return filename;
}
