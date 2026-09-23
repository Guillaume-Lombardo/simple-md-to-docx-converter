import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ComposerWorkspace } from "../src/composer/workspace";
import type { ComposerWorkspaceApi } from "../src/composer/workspace-api";

vi.mock("../src/composer/preview/composer-preview", () => ({
  ComposerPreview: ({
    draftId,
    revisionId,
    format,
    downloadUrl,
    onActiveRevisionChange,
  }: {
    draftId: string;
    revisionId: string;
    format: "docx" | "pptx" | "pdf";
    downloadUrl: string;
    onActiveRevisionChange?: (active: {
      draftId: string;
      revisionId: string;
      format: "docx" | "pptx" | "pdf";
      downloadUrl: string;
    }) => void;
  }) => (
    <>
      <p>Native preview for {revisionId}</p>
      <button
        type="button"
        onClick={() =>
          onActiveRevisionChange?.({
            draftId,
            revisionId,
            format,
            downloadUrl,
          })
        }
      >
        Commit native preview
      </button>
    </>
  ),
}));

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
  title: "Current report",
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
  title: "Second report",
  content: "# Second human source",
  current_revision_id: null,
};
const revision = {
  id: draft.current_revision_id,
  draft_id: draft.id,
  number: 1,
  operation: "capture_source",
  provenance: "human:source",
  source_sha256: "a".repeat(64),
  template_reference: null,
  approved_values: '{"content":"# Human reviewed source"}',
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
const capabilities = {
  status: "ready",
  status_message: null,
  instance_connections_manageable: false,
  maximum_upload_bytes: 1_000,
  maximum_output_tokens: 1024,
  personal_connections_allowed: true,
};
const initialDrafts = [
  draft,
  secondDraft,
  ...Array.from({ length: 98 }, (_, index) => ({
    ...draft,
    id: `00000000-0000-4000-8000-${String(1000 + index).padStart(12, "0")}`,
    title: `Recent report ${index}`,
  })),
];
const initialRevisions = [
  revision,
  ...Array.from({ length: 99 }, (_, index) => ({
    ...revision,
    id: `00000000-0000-4000-8000-${String(3000 + index).padStart(12, "0")}`,
    number: index + 2,
  })),
];

function setup({
  drafts = [draft],
  revisions = [revision],
  selectedRevision = revision,
  messages = [],
  restoredGeneration,
}: {
  drafts?: object[];
  revisions?: object[];
  selectedRevision?: typeof revision;
  messages?: object[];
  restoredGeneration?: Record<string, unknown>;
} = {}) {
  sessionStorage.clear();
  if (restoredGeneration)
    sessionStorage.setItem(
      `composer:generation:${owner.id}:${draft.id}`,
      String(restoredGeneration.id),
    );
  window.history.replaceState(null, "", "/composer");
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  const api = {
    connections: {
      capabilities: vi.fn().mockResolvedValue(capabilities),
      connections: vi.fn().mockResolvedValue([]),
    },
    drafts: vi.fn().mockResolvedValue(drafts),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id ? secondDraft : draft,
      ),
    messages: vi.fn().mockResolvedValue(messages),
    proposals: vi.fn().mockResolvedValue([]),
    questions: vi.fn().mockResolvedValue([]),
    revisions: vi.fn().mockResolvedValue(revisions),
    revision: vi.fn().mockResolvedValue(selectedRevision),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue([]),
    generations: vi.fn().mockResolvedValue([]),
    generation: vi.fn().mockResolvedValue(restoredGeneration),
    cancelGeneration: vi.fn(),
    publishGeneration: vi
      .fn()
      .mockRejectedValue(
        new ApiError(503, "UNAVAILABLE", "Publication unavailable."),
      ),
    startGeneration: vi.fn(),
    diff: vi.fn().mockResolvedValue({
      from_revision_id: revision.id,
      to_revision_id: revision.id,
      status: "unchanged",
      reason: null,
      scope: "approved_markdown",
      metadata_changes: [],
      changes: [],
    }),
    download: vi
      .fn()
      .mockResolvedValue(new Response("# Human reviewed source")),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn(),
    step: vi.fn(),
    cancelStep: vi.fn(),
    decide: vi.fn(),
    answerQuestion: vi.fn(),
    publishProposal: vi.fn(),
    publishDraft: vi.fn(),
    captureSource: vi.fn(),
    restore: vi.fn(),
  };
  render(
    <AuthProvider controller={auth}>
      <ComposerWorkspace api={api as unknown as ComposerWorkspaceApi} />
    </AuthProvider>,
  );
  return api;
}

test("older draft loading fails safely and retries without changing the selected human source", async () => {
  const api = setup({ drafts: initialDrafts });
  const selected = await screen.findByRole("combobox", {
    name: "Saved drafts",
  });
  await waitFor(() => expect(selected).toHaveValue(draft.id));
  const older = {
    ...draft,
    id: "00000000-0000-4000-8000-000000009999",
    title: "Older human report",
  };
  api.drafts
    .mockRejectedValueOnce(
      new ApiError(503, "UNAVAILABLE", "Draft history unavailable."),
    )
    .mockResolvedValueOnce([initialDrafts[0], older]);
  fireEvent.click(screen.getByRole("button", { name: "Load older drafts" }));
  expect(await screen.findByText("Draft history unavailable.")).toBeVisible();
  expect(selected).toHaveValue(draft.id);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  fireEvent.click(screen.getByRole("button", { name: "Load older drafts" }));
  expect(
    await within(selected).findByRole("option", { name: older.title }),
  ).toBeVisible();
  expect(
    within(selected).getAllByRole("option", { name: draft.title }),
  ).toHaveLength(1);
  expect(selected).toHaveValue(draft.id);
  expect(api.drafts).toHaveBeenLastCalledWith(undefined, 100);
});

test("an older revision 403 keeps the owned draft and exact selected revision available for retry", async () => {
  const api = setup({ revisions: initialRevisions });
  const history = await screen.findByRole("combobox", {
    name: "Revision history",
  });
  await waitFor(() => expect(history).toHaveValue(revision.id));
  const older = {
    ...revision,
    id: "00000000-0000-4000-8000-000000008888",
    number: 101,
    operation: "restore",
  };
  api.revisions
    .mockRejectedValueOnce(
      new ApiError(403, "FORBIDDEN", "Older revisions are unavailable."),
    )
    .mockResolvedValueOnce([older]);
  fireEvent.click(screen.getByRole("button", { name: "Load older revisions" }));
  expect(
    await screen.findByText("Older revisions are unavailable."),
  ).toBeVisible();
  expect(history).toHaveValue(revision.id);
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Load older revisions" }));
  expect(
    await within(history).findByRole("option", {
      name: "Revision 101 · restore",
    }),
  ).toBeVisible();
  expect(history).toHaveValue(revision.id);
  expect(api.revisions).toHaveBeenLastCalledWith(draft.id, undefined, 100);
});

test("a late older-draft page cannot enter a newly selected draft", async () => {
  const api = setup({ drafts: initialDrafts });
  const selected = await screen.findByRole("combobox", {
    name: "Saved drafts",
  });
  await waitFor(() => expect(selected).toHaveValue(draft.id));
  let release!: (value: object[]) => void;
  api.drafts.mockImplementationOnce(
    () =>
      new Promise<object[]>((resolve) => {
        release = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Load older drafts" }));
  await waitFor(() => expect(release).toBeDefined());
  fireEvent.change(selected, { target: { value: secondDraft.id } });
  expect(await screen.findByDisplayValue(secondDraft.content)).toBeVisible();
  await act(async () =>
    release([
      {
        ...draft,
        id: "00000000-0000-4000-8000-000000007777",
        title: "Another owner's older draft",
      },
    ]),
  );
  expect(
    screen.queryByRole("option", { name: "Another owner's older draft" }),
  ).toBeNull();
  expect(selected).toHaveValue(secondDraft.id);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
  expect(window.location.search).toBe(`?draft=${secondDraft.id}`);
});

test("an expired session during older revision paging removes private draft content", async () => {
  const api = setup({ revisions: initialRevisions });
  await screen.findByText(/Selected revision 1/);
  api.revisions.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Session expired."),
  );
  fireEvent.click(screen.getByRole("button", { name: "Load older revisions" }));
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(screen.queryByDisplayValue(draft.content)).toBeNull();
  expect(screen.queryByText(/Selected revision 1/)).toBeNull();
});

test("conversation labels assistant output separately from human messages", async () => {
  setup({
    messages: [
      {
        id: "00000000-0000-4000-8000-000000000501",
        role: "user",
        content: "My reviewed instruction",
      },
      {
        id: "00000000-0000-4000-8000-000000000502",
        role: "assistant",
        content: "Unverified assistant suggestion",
      },
    ],
  });
  const conversation = await screen.findByRole("list", { name: "Messages" });
  const suggestion = within(conversation)
    .getByText("Unverified assistant suggestion")
    .closest("li")!;
  const human = within(conversation)
    .getByText("My reviewed instruction")
    .closest("li")!;
  expect(within(suggestion).getByText("Assistant")).toBeVisible();
  expect(within(human).getByText("You")).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
});

test("the selected native revision stays distinct from the usable preview and unavailable diff", async () => {
  const nativeMime =
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const firstNative = {
    ...revision,
    artifacts: revision.artifacts.map((artifact) => ({
      ...artifact,
      media_type: nativeMime,
    })),
  };
  const secondNative = {
    ...firstNative,
    id: "00000000-0000-4000-8000-000000000202",
    number: 2,
    operation: "generate",
  };
  const api = setup({
    revisions: [firstNative, secondNative],
    selectedRevision: firstNative,
  });
  const commit = await screen.findByRole("button", {
    name: "Commit native preview",
  });
  fireEvent.click(commit);
  expect(
    await screen.findByText(`Displaying DOCX revision ${firstNative.id}.`),
  ).toBeVisible();

  api.revision.mockImplementation(async (_draftId: string, id: string) =>
    id === secondNative.id ? secondNative : firstNative,
  );
  api.diff.mockResolvedValue({
    from_revision_id: firstNative.id,
    to_revision_id: secondNative.id,
    status: "unavailable",
    reason: null,
    scope: "artifact_text",
    metadata_changes: [],
    changes: [],
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Revision history" }), {
    target: { value: secondNative.id },
  });
  expect(await screen.findByText(/Selected revision 2/)).toBeVisible();
  expect(
    screen.getByText(
      `Displaying DOCX revision ${firstNative.id}. Selected revision ${secondNative.id} is not displayed.`,
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByText("Changes and source details"));
  expect(
    await screen.findByText("Artifact text changes from previous revision"),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "A semantic comparison is unavailable for this file type.",
    ),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Commit native preview" }),
  );
  expect(
    await screen.findByText(`Displaying DOCX revision ${secondNative.id}.`),
  ).toBeVisible();
});

test("failed generation publication can be retried and reports a changed draft without replacing the source", async () => {
  const generation = {
    id: "00000000-0000-4000-8000-000000000701",
    draft_id: draft.id,
    source_revision_id: revision.id,
    job_id: "00000000-0000-4000-8000-000000000801",
    status: "succeeded",
    output: "docx",
    template_id: null,
    template_version_id: null,
    presentation_dialect: null,
    slide_level: null,
    result_revision_id: null,
    publishable: true,
    created_at: "2026-09-23T12:00:00Z",
  };
  const api = setup({ restoredGeneration: generation });
  expect(await screen.findByText("Publication unavailable.")).toBeVisible();
  fireEvent.click(screen.getByText("Generation · DOCX · succeeded"));
  const retry = screen.getByRole("button", { name: "Retry publication" });
  api.generation.mockRejectedValueOnce(
    new ApiError(503, "UNAVAILABLE", "Generation status unavailable."),
  );
  fireEvent.click(retry);
  expect(
    await screen.findByText("Generation status unavailable."),
  ).toBeVisible();
  expect(api.publishGeneration).toHaveBeenCalledOnce();

  api.generation.mockResolvedValueOnce({ ...generation, publishable: false });
  fireEvent.click(retry);
  expect(
    await screen.findByText(/The draft changed during generation/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(draft.content);
  expect(api.publishGeneration).toHaveBeenCalledOnce();
});
