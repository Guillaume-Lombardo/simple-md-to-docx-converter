import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const generationKey = `composer:generation:${owner.id}:${draft.id}`;
const stepKey = `composer:step:${owner.id}:${draft.id}`;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

afterEach(() => vi.restoreAllMocks());

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
} = {}) {
  sessionStorage.clear();
  if (savedGeneration) sessionStorage.setItem(generationKey, savedGeneration);
  if (savedStep) sessionStorage.setItem(stepKey, savedStep);
  window.history.replaceState(null, "", "/composer");
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  const api = {
    connections: {
      capabilities: vi.fn().mockResolvedValue({
        status: "ready",
        status_message: null,
        instance_connections_manageable: false,
        maximum_upload_bytes: 1000,
        maximum_output_tokens: 1024,
        personal_connections_allowed: true,
      }),
      connections: vi.fn().mockResolvedValue([]),
    },
    drafts: vi.fn().mockResolvedValue([draft]),
    draft: vi.fn().mockResolvedValue(draft),
    messages: vi.fn().mockResolvedValue([]),
    proposals: vi.fn().mockResolvedValue([]),
    questions: vi.fn().mockResolvedValue([]),
    revisions: vi.fn().mockResolvedValue([generatedRevision, revision]),
    revision: vi
      .fn()
      .mockImplementation(async (_id: string, id: string) =>
        id === generatedRevision.id
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
      if (generationError) throw generationError;
      return { ...(generationResponse ?? generation) };
    }),
    cancelGeneration: vi.fn(),
    publishGeneration: publicationError
      ? vi.fn().mockRejectedValueOnce(publicationError)
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
    download: vi.fn().mockResolvedValue(new Response(draft.content)),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn(),
    step: stepError ? vi.fn().mockRejectedValue(stepError) : vi.fn(),
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

test("a stale saved generation lookup chooses the unfinished owner job, not a committed result", async () => {
  const oldId = "00000000-0000-4000-8000-000000000799";
  const queued = {
    ...generation,
    id: "00000000-0000-4000-8000-000000000702",
    status: "queued",
    publishable: false,
  };
  const api = setup({
    savedGeneration: oldId,
    generationError: new ApiError(503, "UNAVAILABLE", "Saved job unavailable."),
    generationList: [
      { ...generation, result_revision_id: generatedRevision.id },
      queued,
    ],
  });
  expect(await screen.findByText(/Generation · DOCX · queued/)).toBeVisible();
  expect(sessionStorage.getItem(generationKey)).toBe(queued.id);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a failed saved step and an unavailable generation list leave the human source usable", async () => {
  const api = setup({
    savedStep: "missing-step",
    savedGeneration: "missing-generation",
    stepError: new ApiError(503, "UNAVAILABLE", "Step unavailable."),
    generationError: new ApiError(403, "FORBIDDEN", "Job no longer available."),
    generationListError: new ApiError(
      503,
      "UNAVAILABLE",
      "Job list unavailable.",
    ),
  });
  await waitFor(() => expect(sessionStorage.getItem(stepKey)).toBeNull());
  await waitFor(() => expect(sessionStorage.getItem(generationKey)).toBeNull());
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
  expect(api.startStep).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a committed result refresh retains the old revision until the exact new revision loads", async () => {
  const next = deferred<typeof generatedRevision>();
  const api = setup({
    savedGeneration: generation.id,
    generationResponse: {
      ...generation,
      publishable: false,
      result_revision_id: generatedRevision.id,
    },
    revisionBarrier: next.promise,
  });
  await waitFor(() =>
    expect(api.revision).toHaveBeenCalledWith(draft.id, generatedRevision.id),
  );
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  next.resolve(generatedRevision);
  expect(await screen.findByText(/Selected revision 2/)).toBeVisible();
  await waitFor(() => expect(sessionStorage.getItem(generationKey)).toBeNull());
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a publication precondition failure preserves source and reuses its retry identity", async () => {
  const api = setup({
    savedGeneration: generation.id,
    publicationError: new ApiError(412, "PRECONDITION_FAILED", "Stale draft."),
  });
  expect(
    await screen.findByText(/The draft changed while generating/),
  ).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
  const calls = api.publishGeneration.mock.calls;
  const firstKey = calls[0]?.[2];
  expect(firstKey).toEqual(expect.any(String));
  expect(
    sessionStorage.getItem(
      `composer:generation-publish:${owner.id}:${draft.id}:${generation.id}`,
    ),
  ).toBe(firstKey);
  api.publishGeneration.mockResolvedValueOnce({ data: generatedRevision });
  fireEvent.click(screen.getByRole("button", { name: "Retry publication" }));
  await waitFor(() => expect(api.publishGeneration).toHaveBeenCalledTimes(2));
  expect(api.publishGeneration.mock.calls[1]?.[2]).toBe(firstKey);
  expect(await screen.findByText(/Selected revision 2/)).toBeVisible();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("session storage denial does not prevent an owned draft from opening and being edited", async () => {
  const read = Storage.prototype.getItem;
  const write = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
    this: Storage,
    key,
  ) {
    if (key.startsWith("composer:"))
      throw new DOMException("Blocked", "SecurityError");
    return read.call(this, key);
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
    this: Storage,
    key,
    value,
  ) {
    if (key.startsWith("composer:"))
      throw new DOMException("Blocked", "SecurityError");
    return write.call(this, key, value);
  });
  const api = setup();
  const editor = await screen.findByRole("textbox", {
    name: "Editable Markdown",
  });
  expect(editor).toHaveValue(draft.content);
  fireEvent.change(editor, { target: { value: "# Still in memory" } });
  expect(editor).toHaveValue("# Still in memory");
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(api.startGeneration).not.toHaveBeenCalled();
});
