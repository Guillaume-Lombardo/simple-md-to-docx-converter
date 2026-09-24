import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ComposerWorkspace } from "../src/composer/workspace";
import type { ComposerWorkspaceApi } from "../src/composer/workspace-api";

const owner = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const draft = {
  id: "00000000-0000-4000-8000-000000000101",
  title: "Human report",
  content: "# Human reviewed source",
  version: 2,
  current_revision_id: "00000000-0000-4000-8000-000000000201",
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-23T12:00:00Z",
  updated_at: "2026-09-23T12:00:00Z",
  etag: '"draft-2"',
};
const secondDraft = {
  ...draft,
  id: "00000000-0000-4000-8000-000000000102",
  title: "Second human report",
  content: "# Second human source",
  current_revision_id: "00000000-0000-4000-8000-000000000203",
};
const revision = {
  id: draft.current_revision_id,
  draft_id: draft.id,
  number: 1,
  operation: "capture_source",
  provenance: "human:source",
  source_sha256: "a".repeat(64),
  template_reference: null,
  approved_values: JSON.stringify({ content: draft.content }),
  render_options: "{}",
  model_identity: null,
  artifacts: ["preview", "download"].map((kind) => ({
    kind,
    sha256: "a".repeat(64),
    size: 24,
    media_type: "text/markdown",
  })),
  restored_from_revision_id: null,
  created_at: "2026-09-23T12:00:00Z",
};
const secondRevision = {
  ...revision,
  id: secondDraft.current_revision_id,
  draft_id: secondDraft.id,
  approved_values: JSON.stringify({ content: secondDraft.content }),
};
const generatedRevision = {
  ...revision,
  id: "00000000-0000-4000-8000-000000000202",
  number: 2,
  operation: "generate",
};
const generation = {
  id: "00000000-0000-4000-8000-000000000701",
  draft_id: draft.id,
  source_revision_id: revision.id,
  job_id: "00000000-0000-4000-8000-000000000801",
  status: "succeeded",
  output: "docx" as const,
  template_id: null,
  template_version_id: null,
  presentation_dialect: null,
  slide_level: null,
  result_revision_id: null as string | null,
  publishable: true,
  created_at: "2026-09-23T12:00:00Z",
};
const queuedGeneration = {
  ...generation,
  status: "queued",
  publishable: false,
};
const pendingStep = {
  id: "00000000-0000-4000-8000-000000000401",
  draft_id: draft.id,
  connection_id: "00000000-0000-4000-8000-000000000301",
  model_identity: "small-model",
  intent: "proposal",
  base_version: 2,
  status: "pending",
  proposal_id: null as string | null,
  question_id: null,
  answered_question_id: null,
  error_code: null,
  created_at: "2026-09-23T12:00:00Z",
  updated_at: "2026-09-23T12:00:00Z",
};
const generationKey = `composer:generation:${owner.id}:${draft.id}`;
const stepKey = `composer:step:${owner.id}:${draft.id}`;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function setup({
  savedGeneration,
  savedStep,
  generationError,
  generationList,
  generationListError,
  generationResponse,
  stepError,
  revisionBarrier,
  publicationError,
  capabilityError,
  connectionError,
  generationBarrier,
  includeSecondDraft,
  generationResponses,
  generationPollBarrier,
  generationPollError,
  stepResponses,
  stepPollError,
  publicationResult,
  initialGenerated,
  draftsAfterFirstError,
}: {
  savedGeneration?: string;
  savedStep?: string;
  generationError?: ApiError;
  generationList?: object[];
  generationListError?: ApiError;
  generationResponse?: typeof generation;
  stepError?: ApiError;
  revisionBarrier?: Promise<typeof generatedRevision>;
  publicationError?: ApiError;
  capabilityError?: ApiError;
  connectionError?: ApiError;
  generationBarrier?: Promise<typeof generation>;
  includeSecondDraft?: boolean;
  generationResponses?: (typeof generation)[];
  generationPollBarrier?: Promise<typeof generation>;
  generationPollError?: ApiError;
  stepResponses?: (typeof pendingStep)[];
  stepPollError?: ApiError;
  publicationResult?: typeof generatedRevision;
  initialGenerated?: boolean;
  draftsAfterFirstError?: ApiError;
} = {}) {
  sessionStorage.clear();
  if (savedGeneration) sessionStorage.setItem(generationKey, savedGeneration);
  if (savedStep) sessionStorage.setItem(stepKey, savedStep);
  window.history.replaceState(null, "", "/composer");
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  let generationLookupCount = 0;
  let draftListCount = 0;
  let lastStep = pendingStep;
  const currentDraft = initialGenerated
    ? { ...draft, current_revision_id: generatedRevision.id }
    : draft;
  const api = {
    connections: {
      capabilities: vi.fn().mockImplementation(async () => {
        if (capabilityError) throw capabilityError;
        return {
          status: "ready",
          status_message: null,
          instance_connections_manageable: false,
          maximum_upload_bytes: 1000,
          maximum_output_tokens: 1024,
          personal_connections_allowed: true,
        };
      }),
      connections: vi.fn().mockImplementation(async () => {
        if (connectionError) throw connectionError;
        return [];
      }),
    },
    drafts: vi.fn().mockImplementation(async () => {
      draftListCount += 1;
      if (draftListCount > 1 && draftsAfterFirstError)
        throw draftsAfterFirstError;
      return includeSecondDraft ? [currentDraft, secondDraft] : [currentDraft];
    }),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id ? secondDraft : currentDraft,
      ),
    messages: vi.fn().mockResolvedValue([]),
    proposals: vi.fn().mockResolvedValue([]),
    questions: vi.fn().mockResolvedValue([]),
    revisions: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id
          ? [secondRevision]
          : [generatedRevision, revision],
      ),
    revision: vi
      .fn()
      .mockImplementation(async (_id: string, id: string) =>
        id === secondRevision.id
          ? secondRevision
          : id === generatedRevision.id
            ? (revisionBarrier ?? generatedRevision)
            : revision,
      ),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue([]),
    generations: vi.fn().mockImplementation(async () => {
      if (generationListError) throw generationListError;
      return generationList ?? [];
    }),
    generation: vi.fn().mockImplementation(async () => {
      generationLookupCount += 1;
      if (generationError) throw generationError;
      if (generationBarrier) return generationBarrier;
      if (generationLookupCount === 2 && generationPollError)
        throw generationPollError;
      if (generationLookupCount === 2 && generationPollBarrier)
        return generationPollBarrier;
      if (generationResponses)
        return { ...(generationResponses.shift() ?? generation) };
      return { ...(generationResponse ?? generation) };
    }),
    cancelGeneration: vi.fn(),
    publishGeneration: publicationError
      ? vi.fn().mockRejectedValueOnce(publicationError)
      : publicationResult
        ? vi.fn().mockResolvedValue({ data: publicationResult })
        : vi.fn(),
    diff: vi.fn().mockResolvedValue({
      from_revision_id: revision.id,
      to_revision_id: generatedRevision.id,
      status: "unchanged",
      reason: null,
      scope: "approved_markdown",
      metadata_changes: [],
      changes: [],
    }),
    download: vi
      .fn()
      .mockImplementation(async () => new Response(draft.content)),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn(),
    step: vi.fn().mockImplementation(async () => {
      if (stepError) throw stepError;
      if (stepResponses?.length) lastStep = stepResponses.shift()!;
      else if (stepPollError) throw stepPollError;
      return { ...lastStep };
    }),
    cancelStep: vi.fn(),
    decide: vi.fn(),
    answerQuestion: vi.fn(),
    publishProposal: vi.fn(),
    publishDraft: vi.fn(),
    captureSource: vi.fn(),
    restore: vi.fn(),
    startGeneration: vi.fn(),
  };
  render(
    <AuthProvider controller={auth}>
      <ComposerWorkspace api={api as unknown as ComposerWorkspaceApi} />
    </AuthProvider>,
  );
  return api;
}

async function settleInitial() {
  for (let index = 0; index < 8; index += 1)
    await act(async () => {
      await Promise.resolve();
    });
}

test("a completed model step poll refreshes the owned draft without creating a proposal silently", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedStep: pendingStep.id,
    stepResponses: [
      pendingStep,
      {
        ...pendingStep,
        status: "completed",
        proposal_id: "00000000-0000-4000-8000-000000000601",
      },
    ],
  });
  await settleInitial();
  expect(screen.getByText(/Suggestion step pending/)).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText(/Suggestion step completed/)).toBeVisible();
  expect(
    screen.getByText(/A proposal is ready for human review below/),
  ).toBeInTheDocument();
  expect(api.drafts.mock.calls.length).toBeGreaterThan(1);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(api.publishProposal).not.toHaveBeenCalled();
});

test("a model step poll 403 reports the error but retains the exact human source", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedStep: pendingStep.id,
    stepResponses: [pendingStep],
    stepPollError: new ApiError(403, "FORBIDDEN", "Step access was revoked."),
  });
  await settleInitial();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText("Step access was revoked.")).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(api.publishProposal).not.toHaveBeenCalled();
});

test("a generation poll reaching publishable success protects the old revision on 412", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedGeneration: generation.id,
    generationResponses: [queuedGeneration, generation],
    publicationError: new ApiError(
      412,
      "PRECONDITION_FAILED",
      "Confidential stale detail",
    ),
  });
  await settleInitial();
  expect(screen.getByText(/Generation · DOCX · queued/)).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText(/The draft changed while generating/)).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(api.publishGeneration).toHaveBeenCalledOnce();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a failed generation poll exposes a safe status and keeps the previous revision", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedGeneration: generation.id,
    generationResponses: [
      queuedGeneration,
      { ...queuedGeneration, status: "failed" },
    ],
  });
  await settleInitial();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.getByText(/Generation · DOCX · failed/)).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a successful generation poll publishes and opens only its exact committed revision", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedGeneration: generation.id,
    generationResponses: [queuedGeneration, generation],
    publicationResult: generatedRevision,
  });
  await settleInitial();
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(
    screen.getByText(
      new RegExp(`Selected revision 2 · ${generatedRevision.id}`),
    ),
  ).toBeVisible();
  expect(api.publishGeneration).toHaveBeenCalledWith(
    draft,
    generation.id,
    expect.any(String),
  );
  expect(api.revision).toHaveBeenCalledWith(draft.id, generatedRevision.id);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test.each([
  [403, "FORBIDDEN", "Generation access was revoked."],
  [401, "AUTHENTICATION_REQUIRED", "Private generation detail"],
])(
  "a generation poll %i applies the session boundary without publishing",
  async (status, code, message) => {
    vi.useFakeTimers();
    const api = setup({
      savedGeneration: generation.id,
      generationResponses: [queuedGeneration],
      generationPollError: new ApiError(status, code, message),
    });
    await settleInitial();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    if (status === 401) {
      expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull();
      expect(screen.queryByText(draft.content)).toBeNull();
    } else {
      expect(screen.getByText(message)).toBeVisible();
      expect(
        screen.getByRole("textbox", { name: "Editable Markdown" }),
      ).toHaveValue(draft.content);
      expect(screen.getByText(/Selected revision 1/)).toBeVisible();
    }
    expect(api.publishGeneration).not.toHaveBeenCalled();
  },
);

test("a late generation poll result cannot publish after choosing another draft", async () => {
  vi.useFakeTimers();
  const completion = deferred<typeof generation>();
  const api = setup({
    savedGeneration: generation.id,
    generationResponses: [queuedGeneration],
    generationPollBarrier: completion.promise,
    includeSecondDraft: true,
  });
  await settleInitial();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  // The poll has been sent; hold its response while selection changes.
  expect(api.generation).toHaveBeenCalledTimes(2);
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  await settleInitial();
  completion.resolve(generation);
  await settleInitial();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a completed step whose refresh returns 401 removes the private workspace", async () => {
  vi.useFakeTimers();
  const api = setup({
    savedStep: pendingStep.id,
    stepResponses: [pendingStep, { ...pendingStep, status: "completed" }],
    draftsAfterFirstError: new ApiError(
      401,
      "AUTHENTICATION_REQUIRED",
      "Private draft detail",
    ),
  });
  await settleInitial();
  expect(screen.getByText(/Suggestion step pending/)).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull();
  expect(screen.queryByText(draft.content)).toBeNull();
  expect(api.publishProposal).not.toHaveBeenCalled();
});

test("a saved result already displayed clears its resume handle without republishing", async () => {
  const api = setup({
    initialGenerated: true,
    savedGeneration: generation.id,
    generationResponse: {
      ...generation,
      publishable: false,
      result_revision_id: generatedRevision.id,
    },
  });
  await settleInitial();
  expect(
    screen.getByText(
      new RegExp(`Selected revision 2 · ${generatedRevision.id}`),
    ),
  ).toBeVisible();
  expect(sessionStorage.getItem(generationKey)).toBeNull();
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a publish 403 keeps the old revision and retries with the original request identity", async () => {
  const api = setup({
    savedGeneration: generation.id,
    generationResponse: generation,
    publicationError: new ApiError(
      403,
      "FORBIDDEN",
      "Publication was revoked.",
    ),
  });
  await settleInitial();
  expect(screen.getByText("Publication was revoked.")).toBeVisible();
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(sessionStorage.getItem(generationKey)).toBe(generation.id);
  const firstKey = api.publishGeneration.mock.calls[0]?.[2];
  expect(firstKey).toEqual(expect.any(String));
  api.publishGeneration.mockResolvedValueOnce({ data: generatedRevision });
  fireEvent.click(screen.getByRole("button", { name: "Retry publication" }));
  await settleInitial();
  expect(api.publishGeneration).toHaveBeenCalledTimes(2);
  expect(api.publishGeneration.mock.calls[1]?.[2]).toBe(firstKey);
  expect(
    screen.getByText(
      new RegExp(`Selected revision 2 · ${generatedRevision.id}`),
    ),
  ).toBeVisible();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a delayed exact result fetch cannot displace a draft selected while it was pending", async () => {
  const exact = deferred<typeof generatedRevision>();
  const api = setup({
    savedGeneration: generation.id,
    generationResponse: {
      ...generation,
      publishable: false,
      result_revision_id: generatedRevision.id,
    },
    revisionBarrier: exact.promise,
    includeSecondDraft: true,
  });
  await settleInitial();
  expect(api.revision).toHaveBeenCalledWith(draft.id, generatedRevision.id);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  await settleInitial();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  await act(async () => {
    exact.resolve(generatedRevision);
  });
  await settleInitial();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  expect(
    screen.getByText(new RegExp(`Selected revision 1 · ${secondRevision.id}`)),
  ).toBeVisible();
  expect(api.publishGeneration).not.toHaveBeenCalled();
});
