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
  capabilityError,
  connectionError,
  generationBarrier,
  includeSecondDraft,
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
    drafts: vi
      .fn()
      .mockResolvedValue(includeSecondDraft ? [draft, secondDraft] : [draft]),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id ? secondDraft : draft,
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
      if (generationError) throw generationError;
      if (generationBarrier) return generationBarrier;
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

test("a 403 on model settings retains the owned draft while a 401 closes its private view", async () => {
  const forbidden = setup({
    capabilityError: new ApiError(403, "FORBIDDEN", "Private policy detail"),
    connectionError: new ApiError(
      403,
      "FORBIDDEN",
      "Private connection detail",
    ),
  });
  expect(
    await screen.findByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(
    screen.getByText(/Model connection details could not be loaded/),
  ).toBeVisible();
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(screen.queryByText("Private policy detail")).toBeNull();
  expect(forbidden.startGeneration).not.toHaveBeenCalled();
});

test("a 401 on model settings expires before the owned draft can be rendered", async () => {
  const api = setup({
    capabilityError: new ApiError(
      401,
      "AUTHENTICATION_REQUIRED",
      "Private session detail",
    ),
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(screen.queryByText(draft.content)).toBeNull();
  expect(api.draft).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a stale generation lookup failure cannot replace the newly selected draft", async () => {
  const lookup = deferred<typeof generation>();
  const api = setup({
    savedGeneration: generation.id,
    includeSecondDraft: true,
    generationBarrier: lookup.promise,
  });
  const selection = await screen.findByRole("combobox", {
    name: "Saved drafts",
  });
  await waitFor(() =>
    expect(api.generation).toHaveBeenCalledWith(draft.id, generation.id),
  );
  fireEvent.change(selection, { target: { value: secondDraft.id } });
  expect(
    await screen.findByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  lookup.reject(new ApiError(403, "FORBIDDEN", "Old job detail"));
  await waitFor(() => expect(sessionStorage.getItem(generationKey)).toBeNull());
  expect(selection).toHaveValue(secondDraft.id);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  expect(screen.queryByText("Old job detail")).toBeNull();
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("an active saved generation 401 expires instead of exposing an old result", async () => {
  const api = setup({
    savedGeneration: generation.id,
    generationError: new ApiError(
      401,
      "AUTHENTICATION_REQUIRED",
      "Private job detail",
    ),
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(screen.queryByText(draft.content)).toBeNull();
  expect(api.generations).not.toHaveBeenCalled();
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a late successful generation lookup cannot publish into a different selected draft", async () => {
  const lookup = deferred<typeof generation>();
  const api = setup({
    savedGeneration: generation.id,
    includeSecondDraft: true,
    generationBarrier: lookup.promise,
  });
  const selection = await screen.findByRole("combobox", {
    name: "Saved drafts",
  });
  await waitFor(() =>
    expect(api.generation).toHaveBeenCalledWith(draft.id, generation.id),
  );
  fireEvent.change(selection, { target: { value: secondDraft.id } });
  expect(
    await screen.findByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  lookup.resolve({
    ...generation,
    result_revision_id: generatedRevision.id,
    publishable: false,
  });
  await waitFor(() =>
    expect(api.revision).toHaveBeenCalledWith(
      secondDraft.id,
      secondRevision.id,
    ),
  );
  expect(selection).toHaveValue(secondDraft.id);
  expect(
    screen.getByText(new RegExp(`Selected revision 1 · ${secondRevision.id}`)),
  ).toBeVisible();
  expect(api.revision).not.toHaveBeenCalledWith(draft.id, generatedRevision.id);
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a saved model step 403 clears only its resume handle and leaves the owned draft open", async () => {
  const api = setup({
    savedStep: "00000000-0000-4000-8000-000000000401",
    stepError: new ApiError(403, "FORBIDDEN", "Old step no longer available"),
  });
  await waitFor(() => expect(sessionStorage.getItem(stepKey)).toBeNull());
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(screen.queryByText("Old step no longer available")).toBeNull();
  expect(api.startStep).not.toHaveBeenCalled();
});
