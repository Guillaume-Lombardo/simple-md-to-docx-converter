import type {
  ConversionOptionsResponse,
  ConversionResponse,
  JobOutput,
  TemplateResponse,
} from "../api/generated/types.gen";
import {
  vCancelConversionApiV1ConversionsJobIdDeleteResponse,
  vCreateConversionApiV1ConversionsPostResponse,
  vGetConversionApiV1ConversionsJobIdGetResponse,
  vGetConversionOptionsApiV1ConversionOptionsGetResponse,
  vListConversionsApiV1ConversionsGetResponse,
  vListTemplatesApiV1TemplatesGetResponse,
} from "../api/generated/valibot.gen";
import { ApiError, ApiTransport } from "../api/transport";

const TERMINAL_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "expired",
]);
const POLL_START_MS = 1_000;
const POLL_MAX_MS = 10_000;
const SAFE_FAILURE = "The request could not be completed. Try again.";

export type TemplateSelection = {
  id: string;
  versionId: string;
  name: string;
  description: string;
  source: "preferred" | "selected" | "system_fallback";
};

export type ConversionState = {
  phase: "loading" | "ready" | "unavailable";
  maximumBytes?: number;
  source?: File;
  output: JobOutput;
  selection?: TemplateSelection;
  templates: TemplateResponse[];
  searching: boolean;
  recent: ConversionResponse[];
  active?: ConversionResponse;
  submitting: boolean;
  cancelling: boolean;
  error?: string;
  notice?: string;
};

type Listener = (state: ConversionState) => void;
type ExpireSession = () => void;
type Scheduler = (
  callback: () => void,
  delay: number,
) => ReturnType<typeof setTimeout>;

export class ConversionController {
  private state: ConversionState = initialState();
  private readonly listeners = new Set<Listener>();
  private loadGeneration = 0;
  private searchGeneration = 0;
  private submissionGeneration = 0;
  private jobGeneration = 0;
  private loadRequest?: AbortController;
  private searchRequest?: AbortController;
  private submissionRequest?: AbortController;
  private jobRequest?: AbortController;
  private cancellationRequest?: AbortController;
  private pollTimer?: ReturnType<typeof setTimeout>;
  private pollDelay = POLL_START_MS;
  private idempotencyKey?: string;

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

  snapshot = (): ConversionState => this.state;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  async load(): Promise<void> {
    this.loadRequest?.abort();
    const request = new AbortController();
    this.loadRequest = request;
    const generation = ++this.loadGeneration;
    this.publish({ ...initialState(), phase: "loading" });
    try {
      const [options, recent] = await Promise.all([
        this.api.json(
          "/api/v1/conversion-options",
          vGetConversionOptionsApiV1ConversionOptionsGetResponse,
          { signal: request.signal },
        ),
        this.api.json(
          "/api/v1/conversions?offset=0&limit=10",
          vListConversionsApiV1ConversionsGetResponse,
          { signal: request.signal },
        ),
      ]);
      if (!this.currentLoad(generation)) return;
      this.publish({
        ...initialState(),
        phase: "ready",
        maximumBytes: options.conversion_upload_max_bytes,
        selection: selectionFromOptions(options),
        recent: recent.items,
      });
    } catch (error) {
      if (!this.currentLoad(generation) || isAbort(error)) return;
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
            ? "Choose exactly one Markdown or ZIP file."
            : undefined,
      });
      return;
    }
    const file = files[0];
    if (!file) return;
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      source: file,
      submitting: false,
      error: undefined,
      notice: undefined,
    });
  }

  setOutput(output: JobOutput): void {
    if (this.state.output === output) return;
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      output,
      submitting: false,
      error: undefined,
      notice: undefined,
    });
  }

  chooseTemplate(template: TemplateResponse): void {
    if (!template.current_version_id) {
      this.publish({
        ...this.state,
        error: "This template has no active version.",
      });
      return;
    }
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      selection: {
        id: template.id,
        versionId: template.current_version_id,
        name: template.name,
        description: template.description,
        source: "selected",
      },
      templates: [],
      submitting: false,
      error: undefined,
      notice: undefined,
    });
  }

  choosePandocDefault(): void {
    this.invalidateSubmission();
    this.publish({
      ...this.state,
      selection: undefined,
      templates: [],
      submitting: false,
      error: undefined,
      notice: undefined,
    });
  }

  async searchTemplates(query: string): Promise<void> {
    this.searchRequest?.abort();
    const request = new AbortController();
    this.searchRequest = request;
    const generation = ++this.searchGeneration;
    this.publish({ ...this.state, searching: true, error: undefined });
    const parameters = new URLSearchParams({
      status: "active",
      offset: "0",
      limit: "20",
    });
    if (query.trim()) parameters.set("name", query.trim());
    try {
      const page = await this.api.json(
        `/api/v1/templates?${parameters}`,
        vListTemplatesApiV1TemplatesGetResponse,
        { signal: request.signal },
      );
      if (generation !== this.searchGeneration) return;
      this.publish({ ...this.state, searching: false, templates: page.items });
    } catch (error) {
      if (generation !== this.searchGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      this.publish({
        ...this.state,
        searching: false,
        error: errorMessage(
          error,
          "Templates could not be loaded. Try the search again.",
        ),
      });
    }
  }

  async submit(): Promise<void> {
    if (this.state.phase !== "ready" || this.state.submitting) return;
    const invalid = validateSource(this.state.source, this.state.maximumBytes);
    if (invalid) {
      this.publish({ ...this.state, error: invalid, notice: undefined });
      return;
    }
    const request = new AbortController();
    this.submissionRequest?.abort();
    this.submissionRequest = request;
    const generation = ++this.submissionGeneration;
    const key = (this.idempotencyKey ??= this.randomUUID());
    const form = new FormData();
    form.append("source", this.state.source!);
    form.append("output", this.state.output);
    if (this.state.selection) {
      form.append("template_id", this.state.selection.id);
      form.append("template_version_id", this.state.selection.versionId);
    }
    this.publish({
      ...this.state,
      submitting: true,
      error: undefined,
      notice: "Submitting your conversion…",
    });
    try {
      const accepted = await this.api.multipartWithMetadata(
        "/api/v1/conversions",
        form,
        vCreateConversionApiV1ConversionsPostResponse,
        { csrf: true, idempotencyKey: key, signal: request.signal },
      );
      if (generation !== this.submissionGeneration) return;
      if (accepted.status !== 202)
        throw new ApiError(
          accepted.status,
          "UNEXPECTED_RESPONSE",
          "The service returned an unexpected response.",
        );
      const job = accepted.data;
      if (
        accepted.location !== `/api/v1/conversions/${job.id}` ||
        accepted.retryAfterSeconds === undefined
      )
        throw new ApiError(
          accepted.status,
          "UNEXPECTED_RESPONSE",
          "The service returned an unexpected response.",
        );
      this.idempotencyKey = undefined;
      this.publish({
        ...this.state,
        submitting: false,
        active: job,
        recent: upsertRecent(this.state.recent, job),
        notice: undefined,
      });
      this.activateJob(job.id, accepted.retryAfterSeconds * 1_000);
      if (!isTerminal(job)) {
        this.schedulePoll(job.id, this.jobGeneration);
      }
    } catch (error) {
      if (generation !== this.submissionGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      const clientRejection =
        error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (clientRejection) this.idempotencyKey = undefined;
      this.publish({
        ...this.state,
        submitting: false,
        notice: undefined,
        error: clientRejection
          ? errorMessage(error)
          : `${errorMessage(error, "The conversion could not be submitted.")} Retrying will reuse the same request key.`,
      });
    }
  }

  async openJob(jobId: string): Promise<void> {
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
        `/api/v1/conversions/${active.id}`,
        vCancelConversionApiV1ConversionsJobIdDeleteResponse,
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
    try {
      const response = await this.api.download(
        `/api/v1/conversions/${active.id}/result`,
      );
      const filename = validatedDownloadFilename(response);
      const blob = await response.blob();
      return { blob, filename };
    } catch (error) {
      if (this.authoritativeExpiry(error)) return undefined;
      this.publish({
        ...this.state,
        error: errorMessage(error, "The result could not be downloaded."),
      });
      return undefined;
    }
  }

  dispose(): void {
    this.loadGeneration += 1;
    this.searchGeneration += 1;
    this.submissionGeneration += 1;
    this.jobGeneration += 1;
    this.loadRequest?.abort();
    this.searchRequest?.abort();
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
        `/api/v1/conversions/${jobId}`,
        vGetConversionApiV1ConversionsJobIdGetResponse,
        { signal: request.signal },
      );
      if (generation !== this.jobGeneration) return;
      this.publishJob(job, { error: undefined });
      if (!isTerminal(job)) {
        this.pollDelay = nextPollDelay(this.pollDelay);
        this.schedulePoll(jobId, generation);
      }
    } catch (error) {
      if (generation !== this.jobGeneration || isAbort(error)) return;
      if (this.authoritativeExpiry(error)) return;
      const willRetry =
        !(error instanceof ApiError) || ![401, 404].includes(error.status);
      const detail = errorMessage(error, "Status is temporarily unavailable.");
      this.publish({
        ...this.state,
        error: willRetry
          ? `${detail} Polling will continue.`
          : `${detail} Polling has stopped. Reopen the conversion to try again.`,
      });
      if (willRetry) {
        this.pollDelay = nextPollDelay(this.pollDelay);
        this.schedulePoll(jobId, generation);
      }
    }
  }

  private activateJob(jobId: string, initialPollDelay = POLL_START_MS): void {
    this.jobGeneration += 1;
    this.jobRequest?.abort();
    this.cancellationRequest?.abort();
    this.clearPoll();
    this.pollDelay = initialPollDelay;
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
    if (this.pollTimer !== undefined) this.cancelSchedule(this.pollTimer);
    this.pollTimer = undefined;
  }

  private publishJob(
    job: ConversionResponse,
    extra: Partial<ConversionState>,
  ): void {
    this.publish({
      ...this.state,
      ...extra,
      active: job,
      recent: upsertRecent(this.state.recent, job),
    });
  }

  private invalidateSubmission(): void {
    this.submissionGeneration += 1;
    this.submissionRequest?.abort();
    this.idempotencyKey = undefined;
  }

  private authoritativeExpiry(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.status !== 401) return false;
    this.dispose();
    this.expireSession();
    return true;
  }

  private currentLoad(generation: number): boolean {
    return generation === this.loadGeneration;
  }

  private publish(state: ConversionState): void {
    this.state = state;
    for (const listener of this.listeners) listener(state);
  }
}

export function validateSource(
  file: File | undefined,
  maximumBytes: number | undefined,
): string | undefined {
  if (!file) return "Choose a Markdown or ZIP file.";
  if (!/\.(md|zip)$/i.test(file.name))
    return "Choose a file ending in .md or .zip.";
  if (file.size < 1) return "The selected file is empty.";
  if (!maximumBytes || file.size > maximumBytes)
    return "The selected file exceeds the configured upload limit.";
  return undefined;
}

export function nextPollDelay(currentMilliseconds: number): number {
  return Math.min(Math.ceil(currentMilliseconds * 1.6), POLL_MAX_MS);
}

export function statusPresentation(job: ConversionResponse): string {
  if (job.state === "failed")
    return job.error_message || "The conversion failed.";
  if (job.state === "cancelled") return "The conversion was cancelled.";
  if (job.state === "expired")
    return "This conversion has expired and its files are no longer available.";
  if (job.state === "succeeded") return "Your conversion is ready to download.";
  if (job.cancel_requested)
    return "Cancellation requested. Waiting for the worker to stop.";
  if (job.state === "queued") return "Your conversion is queued.";
  const steps: Record<string, string> = {
    validating: "Validating the package",
    rendering: "Rendering diagrams and images",
    docx: "Creating the DOCX file",
    pdf: "Creating the PDF file",
    publishing: "Publishing the result",
  };
  return `${steps[job.step] ?? "Processing"} (${job.progress}%).`;
}

function initialState(): ConversionState {
  return {
    phase: "loading",
    output: "docx",
    templates: [],
    searching: false,
    recent: [],
    submitting: false,
    cancelling: false,
  };
}

function selectionFromOptions(
  options: ConversionOptionsResponse,
): TemplateSelection | undefined {
  if (options.selection_source === "pandoc_default") {
    if (
      options.resolved_template !== null ||
      options.template_version_id !== null
    )
      throw new TypeError("Invalid conversion options response");
    return undefined;
  }
  const template = options.resolved_template;
  if (
    !template ||
    !template.current_version_id ||
    template.current_version_id !== options.template_version_id
  )
    throw new TypeError("Invalid conversion options response");
  return {
    id: template.id,
    versionId: template.current_version_id,
    name: template.name,
    description: template.description,
    source: options.selection_source,
  };
}

function isTerminal(job: ConversionResponse): boolean {
  return TERMINAL_STATES.has(job.state);
}

export function isCancellable(job: ConversionResponse): boolean {
  return !isTerminal(job) && !job.cancel_requested;
}

function upsertRecent(
  recent: ConversionResponse[],
  job: ConversionResponse,
): ConversionResponse[] {
  return [job, ...recent.filter((candidate) => candidate.id !== job.id)].slice(
    0,
    10,
  );
}

function errorMessage(error: unknown, fallback = SAFE_FAILURE): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function validatedDownloadFilename(response: Response): string {
  if (
    response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase() !== "application/octet-stream" ||
    response.headers.get("cache-control")?.toLowerCase() !==
      "private, no-store" ||
    response.headers.get("x-content-type-options")?.toLowerCase() !== "nosniff"
  )
    throw new ApiError(
      response.status,
      "UNEXPECTED_RESPONSE",
      "The service returned an unexpected response.",
    );
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
    /[\r\n]/.test(filename)
  )
    throw new ApiError(
      response.status,
      "UNEXPECTED_RESPONSE",
      "The service returned an unexpected response.",
    );
  return filename;
}
