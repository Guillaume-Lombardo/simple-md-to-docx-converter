import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ApiError, type ApiTransport } from "../src/api/transport";
import type { ComposerWorkspaceApi } from "../src/composer/workspace-api";
import { ComposerWorkspace } from "../src/composer/workspace";

vi.mock("../src/composer/preview/composer-preview", () => ({
  ComposerPreview: ({
    revisionId,
    downloadUrl,
  }: {
    revisionId: string;
    downloadUrl: string;
  }) => (
    <>
      <p>Native preview for {revisionId}</p>
      <a href={downloadUrl}>Download this revision</a>
    </>
  ),
}));

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const draft = {
  id: "00000000-0000-4000-8000-000000000101",
  title: "Report",
  content: "Approved source",
  version: 2,
  current_revision_id: "00000000-0000-4000-8000-000000000201",
  source_kind: "upload",
  source_media_type:
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
  approved_values: "{}",
  render_options: "{}",
  model_identity: null,
  artifacts: [
    {
      kind: "preview",
      sha256: "a".repeat(64),
      size: 12,
      media_type: draft.source_media_type,
    },
    {
      kind: "download",
      sha256: "a".repeat(64),
      size: 12,
      media_type: draft.source_media_type,
    },
  ],
  restored_from_revision_id: null,
  created_at: "2026-09-23T12:00:00Z",
};
const ready = {
  status: "ready",
  status_message: null,
  instance_connections_manageable: false,
  maximum_upload_bytes: 1_000_000,
  maximum_output_tokens: 1024,
  personal_connections_allowed: true,
};
const connection = {
  allowed_user_ids: [],
  authorized: true,
  client_certificate_present: false,
  credential_present: true,
  enabled: true,
  endpoint: "https://llm.example/v1",
  etag: '"connection-1"',
  id: "00000000-0000-4000-8000-000000000301",
  identity_mode: "shared",
  internal_ca_present: false,
  name: "Approved model",
  permitted_models: ["small-model"],
  scope: "personal",
  selected_model: "small-model",
  status: "ready",
  status_message: null,
};
const question = {
  id: "00000000-0000-4000-8000-000000000501",
  draft_id: draft.id,
  model_step_id: "00000000-0000-4000-8000-000000000401",
  base_version: 2,
  state: "pending",
  text: "Which date should the report use?",
  answer_message_id: null,
  answer_content: null,
  created_at: "2026-09-23T12:00:00Z",
  answered_at: null,
};
const proposal = {
  id: "00000000-0000-4000-8000-000000000601",
  draft_id: draft.id,
  base_version: 2,
  state: "pending",
  proposed_value: "Model suggestion",
  decided_value: null,
  provenance: "model:small-model",
  created_at: "2026-09-23T12:00:00Z",
  decided_at: null,
  decided_by: null,
};
const queuedGeneration = {
  id: "00000000-0000-4000-8000-000000000701",
  draft_id: draft.id,
  source_revision_id: revision.id,
  job_id: "00000000-0000-4000-8000-000000000801",
  status: "queued",
  output: "docx" as const,
  template_id: null,
  template_version_id: null,
  presentation_dialect: null,
  slide_level: null,
  result_revision_id: null,
  publishable: false,
  created_at: "2026-09-23T12:00:00Z",
};

function setup(
  options: {
    outage?: boolean;
    unauthorized?: boolean;
    connectionFailure?: boolean;
    question?: boolean;
    preserveStorage?: boolean;
    proposalState?: "pending" | "accepted" | "edited";
    markdown?: boolean;
    unapprovedMarkdownRevision?: boolean;
    semanticDiff?: boolean;
    restoreGeneration?: boolean;
    recoverGenerationFromList?: boolean;
    recoveredPublication?: boolean;
    recoveredPublicationFetchFails?: boolean;
    secondDraft?: boolean;
  } = {},
) {
  if (!options.preserveStorage) sessionStorage.clear();
  if (options.restoreGeneration || options.recoveredPublication)
    sessionStorage.setItem(
      `composer:generation:${user.id}:${draft.id}`,
      queuedGeneration.id,
    );
  window.history.replaceState(null, "", "/composer");
  const authTransport = { json: vi.fn().mockResolvedValue(user) };
  const auth = new AuthController(authTransport as unknown as ApiTransport);
  const currentDraft = options.markdown
    ? { ...draft, content: "# Original", source_media_type: "text/markdown" }
    : draft;
  const currentRevision = options.markdown
    ? {
        ...revision,
        approved_values: options.unapprovedMarkdownRevision
          ? "{}"
          : '{"content":"# Original"}',
        number: options.semanticDiff ? 2 : 1,
        artifacts: revision.artifacts.map((item) => ({
          ...item,
          media_type: "text/markdown",
        })),
      }
    : revision;
  const generatedRevision = {
    ...currentRevision,
    id: "00000000-0000-4000-8000-000000000203",
    number: 2,
    operation: "generate",
    artifacts: currentRevision.artifacts.map((artifact) => ({
      ...artifact,
      media_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    })),
  };
  let generatedRevisionReads = 0;
  const otherDraft = {
    ...currentDraft,
    id: "00000000-0000-4000-8000-000000000102",
    title: "Second report",
    content: "Second owner's selected text",
    current_revision_id: null,
  };
  const api = {
    connections: {
      capabilities: vi
        .fn()
        .mockResolvedValue(
          options.outage ? { ...ready, status: "outage" } : ready,
        ),
      connections: options.connectionFailure
        ? vi.fn().mockRejectedValue(new Error("private key unavailable"))
        : vi
            .fn()
            .mockResolvedValue([
              options.unauthorized
                ? { ...connection, authorized: false }
                : connection,
            ]),
    },
    drafts: vi
      .fn()
      .mockResolvedValue(
        options.secondDraft ? [currentDraft, otherDraft] : [currentDraft],
      ),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === otherDraft.id ? otherDraft : currentDraft,
      ),
    messages: vi.fn().mockResolvedValue([]),
    proposals: vi
      .fn()
      .mockResolvedValue(
        options.proposalState
          ? [{ ...proposal, state: options.proposalState }]
          : [],
      ),
    questions: vi.fn().mockResolvedValue(options.question ? [question] : []),
    revisions: vi
      .fn()
      .mockResolvedValue(
        options.recoveredPublication
          ? [generatedRevision, currentRevision]
          : options.semanticDiff
            ? [
                { ...revision, id: "previous-revision", number: 1 },
                currentRevision,
              ]
            : [currentRevision],
      ),
    revision: vi
      .fn()
      .mockImplementation(async (_draftId: string, id: string) => {
        if (id !== generatedRevision.id) return currentRevision;
        generatedRevisionReads += 1;
        if (
          options.recoveredPublicationFetchFails &&
          generatedRevisionReads === 1
        )
          throw new ApiError(
            503,
            "UNAVAILABLE",
            "Revision temporarily unavailable",
          );
        return generatedRevision;
      }),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue([]),
    startGeneration: vi.fn().mockResolvedValue({ data: queuedGeneration }),
    generation: vi.fn().mockResolvedValue(
      options.recoveredPublication
        ? {
            ...queuedGeneration,
            status: "succeeded",
            publishable: false,
            result_revision_id: generatedRevision.id,
          }
        : options.restoreGeneration
          ? { ...queuedGeneration, status: "succeeded", publishable: true }
          : queuedGeneration,
    ),
    generations: vi
      .fn()
      .mockResolvedValue(
        options.recoverGenerationFromList
          ? [{ ...queuedGeneration, status: "succeeded", publishable: true }]
          : [],
      ),
    cancelGeneration: vi.fn(),
    publishGeneration: vi.fn().mockResolvedValue({ data: currentRevision }),
    diff: vi.fn().mockResolvedValue({
      from_revision_id: "previous-revision",
      to_revision_id: currentRevision.id,
      status: "unchanged",
      reason: null,
      scope: "approved_markdown",
      metadata_changes: ["template_reference"],
      changes: [],
    }),
    download: vi.fn().mockResolvedValue(new Response("# Original")),
    saveDraft: vi.fn(),
    createDraft: vi.fn().mockResolvedValue({ data: draft }),
    addMessage: vi.fn().mockResolvedValue({}),
    startStep: vi.fn().mockResolvedValue({
      id: "00000000-0000-4000-8000-000000000401",
      draft_id: draft.id,
      connection_id: connection.id,
      model_identity: "small-model",
      intent: "proposal",
      base_version: 2,
      status: "pending",
      proposal_id: null,
      question_id: null,
      answered_question_id: null,
      error_code: null,
      created_at: "2026-09-23T12:00:00Z",
      updated_at: "2026-09-23T12:00:00Z",
    }),
    step: vi.fn().mockResolvedValue({
      id: "00000000-0000-4000-8000-000000000401",
      draft_id: draft.id,
      connection_id: connection.id,
      model_identity: "small-model",
      intent: "proposal",
      base_version: 2,
      status: "completed",
      proposal_id: null,
      question_id: null,
      answered_question_id: null,
      error_code: null,
      created_at: "2026-09-23T12:00:00Z",
      updated_at: "2026-09-23T12:00:00Z",
    }),
    cancelStep: vi.fn().mockResolvedValue({
      id: "00000000-0000-4000-8000-000000000401",
      draft_id: draft.id,
      connection_id: connection.id,
      model_identity: "small-model",
      intent: "proposal",
      base_version: 2,
      status: "cancelled",
      proposal_id: null,
      question_id: null,
      answered_question_id: null,
      error_code: null,
      created_at: "2026-09-23T12:00:00Z",
      updated_at: "2026-09-23T12:00:00Z",
    }),
    decide: vi.fn(),
    answerQuestion: vi.fn().mockResolvedValue({ data: question }),
    publishProposal: vi.fn(),
    publishDraft: vi.fn().mockResolvedValue({ data: currentRevision }),
    captureSource: vi.fn().mockResolvedValue({ data: revision }),
    restore: vi.fn().mockResolvedValue({ data: revision }),
  };
  const mounted = render(
    <AuthProvider controller={auth}>
      <ComposerWorkspace api={api as unknown as ComposerWorkspaceApi} />
    </AuthProvider>,
  );
  return Object.assign(api, { auth, authTransport, unmount: mounted.unmount });
}

test("outage preserves retained exact revision preview and download while preventing model use", async () => {
  const api = setup({ outage: true });
  expect(await screen.findByText(/temporarily unavailable/)).toBeVisible();
  expect(
    await screen.findByText(`Native preview for ${revision.id}`),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    `/api/v1/composer/drafts/${draft.id}/revisions/${revision.id}/artifacts/download`,
  );
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    {
      target: { value: "A prepared request" },
    },
  );
  expect(
    screen.getByRole("button", { name: "Send reviewed text" }),
  ).toBeDisabled();
  expect(api.startStep).not.toHaveBeenCalled();
});

test("connection metadata failure does not hide a retained draft or revision", async () => {
  setup({ connectionFailure: true });
  expect(
    await screen.findByText(/Model connection details could not be loaded/),
  ).toBeVisible();
  expect(
    await screen.findByText(`Native preview for ${revision.id}`),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toBeVisible();
  expect(document.body).not.toHaveTextContent("private key unavailable");
});

test("management metadata without model-use grant does not offer the connection", async () => {
  setup({ unauthorized: true });
  expect(
    await screen.findByRole("heading", { name: "Ask a model" }),
  ).toBeVisible();
  expect(screen.queryByRole("option", { name: "Approved model" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Send reviewed text" }),
  ).toBeDisabled();
});

test("model request sends only reviewed text to an authorized endpoint and model", async () => {
  const api = setup();
  expect(
    await screen.findByRole("option", { name: "Approved model" }),
  ).toBeVisible();
  expect(api.startStep).not.toHaveBeenCalled();
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    {
      target: { value: "Only this approved excerpt" },
    },
  );
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    {
      target: { value: "128" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Send reviewed text" }));
  await waitFor(() =>
    expect(api.startStep).toHaveBeenCalledWith(
      draft,
      {
        connection_id: connection.id,
        approved_endpoint: connection.endpoint,
        approved_model: "small-model",
        content: "Only this approved excerpt",
        max_output_tokens: 128,
        intent: "proposal",
      },
      expect.any(String),
    ),
  );
});

test("requesting missing information starts a bounded question step", async () => {
  const api = setup();
  await screen.findByRole("option", { name: "Approved model" });
  fireEvent.change(
    screen.getByRole("combobox", { name: "Model step purpose" }),
    {
      target: { value: "question" },
    },
  );
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    {
      target: { value: "Ask about one missing date in this approved summary." },
    },
  );
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    { target: { value: "128" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Send reviewed text" }));
  await waitFor(() =>
    expect(api.startStep).toHaveBeenCalledWith(
      draft,
      expect.objectContaining({ intent: "question" }),
      expect.any(String),
    ),
  );
});

test("an unsent question purpose and reviewed text survive a browser remount", async () => {
  const first = setup();
  await screen.findByRole("combobox", { name: "Model step purpose" });
  fireEvent.change(
    screen.getByRole("combobox", { name: "Model step purpose" }),
    {
      target: { value: "question" },
    },
  );
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    { target: { value: "Ask about the ambiguous date." } },
  );
  first.unmount();
  setup({ preserveStorage: true });
  expect(
    await screen.findByRole("combobox", { name: "Model step purpose" }),
  ).toHaveValue("question");
  expect(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
  ).toHaveValue("Ask about the ambiguous date.");
});

test("an assistant question has a durable identity and a human answer resumes only after review", async () => {
  const api = setup({ question: true });
  expect(
    await screen.findByText("Which date should the report use?"),
  ).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), {
    target: { value: "Use the signed date, 23 September." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  await waitFor(() =>
    expect(api.answerQuestion).toHaveBeenCalledWith(
      draft,
      question.id,
      "Use the signed date, 23 September.",
      expect.any(String),
    ),
  );
  expect(
    await screen.findByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
  ).toHaveValue(
    "Question: Which date should the report use?\nAnswer: Use the signed date, 23 September.",
  );
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    { target: { value: "128" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Send reviewed text" }));
  await waitFor(() =>
    expect(api.startStep).toHaveBeenCalledWith(
      draft,
      expect.objectContaining({
        intent: "proposal",
        answered_question_id: question.id,
        content:
          "Question: Which date should the report use?\nAnswer: Use the signed date, 23 September.",
      }),
      expect.any(String),
    ),
  );
});

test("a provider outage keeps a pending human question answerable without starting a model", async () => {
  const api = setup({ outage: true, question: true });
  await screen.findByText("Which date should the report use?");
  fireEvent.change(screen.getByRole("textbox", { name: "Your answer" }), {
    target: { value: "Use the signed date." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  await waitFor(() => expect(api.answerQuestion).toHaveBeenCalledTimes(1));
  expect(api.startStep).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Send reviewed text" }),
  ).toBeDisabled();
});

test("background refresh does not overwrite unsaved human draft text", async () => {
  const api = setup();
  const title = await screen.findByRole("textbox", { name: "Draft title" });
  fireEvent.change(title, { target: { value: "Human title" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Draft notes" }), {
    target: { value: "Human correction in progress" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Message or answer" }), {
    target: { value: "A saved question" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save message" }));
  await waitFor(() => expect(api.addMessage).toHaveBeenCalled());
  await screen.findByText(/Message saved/);
  expect(title).toHaveValue("Human title");
  expect(screen.getByRole("textbox", { name: "Draft notes" })).toHaveValue(
    "Human correction in progress",
  );
  expect(
    sessionStorage.getItem(`composer:content:${user.id}:${draft.id}`),
  ).toBe("Human correction in progress");
});

test("browser storage failure keeps unsent Composer text visible with a clear limit", async () => {
  setup();
  const input = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  const setItem = vi
    .spyOn(Storage.prototype, "setItem")
    .mockImplementation(() => {
      throw new DOMException("Storage quota reached", "QuotaExceededError");
    });
  try {
    fireEvent.change(input, {
      target: { value: "A human answer in progress" },
    });
    expect(input).toHaveValue("A human answer in progress");
    expect(screen.getByText(/Browser storage is unavailable/)).toBeVisible();
    expect(document.body).not.toHaveTextContent("Storage quota reached");
  } finally {
    setItem.mockRestore();
  }
});

test("text and Markdown dropped into chat are appended without replacing a human answer", async () => {
  setup();
  const input = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(input, { target: { value: "Human answer" } });
  fireEvent.drop(input, {
    dataTransfer: { files: [], getData: () => "A pasted Markdown fact" },
  });
  expect(input).toHaveValue("Human answer\n\nA pasted Markdown fact");
  const markdown = new File(["# Dropped source"], "source.md", {
    type: "text/markdown",
  });
  Object.defineProperty(markdown, "text", {
    value: async () => "# Dropped source",
  });
  fireEvent.drop(input, {
    dataTransfer: { files: [markdown], getData: () => "" },
  });
  await waitFor(() =>
    expect(input).toHaveValue(
      "Human answer\n\nA pasted Markdown fact\n\n# Dropped source",
    ),
  );
  expect(
    sessionStorage.getItem(`composer:message:${user.id}:${draft.id}`),
  ).toBe("Human answer\n\nA pasted Markdown fact\n\n# Dropped source");
});

test("a second browser user never sees the previous user's Composer draft while loading", async () => {
  const api = setup();
  expect(
    await screen.findByRole("textbox", { name: "Draft notes" }),
  ).toHaveValue("Approved source");
  let resolveDrafts: ((value: (typeof draft)[]) => void) | undefined;
  api.drafts.mockImplementationOnce(
    () =>
      new Promise<(typeof draft)[]>((resolve) => {
        resolveDrafts = resolve;
      }),
  );
  api.auth.expire();
  api.authTransport.json.mockResolvedValue({ ...user, id: "second-owner" });
  await api.auth.load();
  expect(screen.queryByRole("textbox", { name: "Draft notes" })).toBeNull();
  expect(screen.getByText("Loading Composer…")).toBeVisible();
  resolveDrafts?.([]);
  await screen.findByRole("combobox", { name: "Saved drafts" });
  expect(screen.queryByRole("textbox", { name: "Draft notes" })).toBeNull();
});

test("creating a source draft uses the selected File and opens only its returned draft", async () => {
  const api = setup();
  await screen.findByRole("button", { name: "Create draft" });
  const source = new File(["# Human source"], "source.md", {
    type: "text/markdown",
  });
  fireEvent.change(screen.getByLabelText("Markdown or Office source"), {
    target: { files: [source] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  await waitFor(() => expect(api.createDraft).toHaveBeenCalledWith(source));
  expect(window.location.search).toBe(`?draft=${draft.id}`);
  expect(api.startStep).not.toHaveBeenCalled();
});

test("rejecting a pending suggestion leaves its revision unpublished", async () => {
  const api = setup({ proposalState: "pending" });
  fireEvent.click(await screen.findByText(/Suggestion · pending/));
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() =>
    expect(api.decide).toHaveBeenCalledWith(
      draft,
      proposal.id,
      "rejected",
      undefined,
    ),
  );
  expect(api.publishProposal).not.toHaveBeenCalled();
  expect(api.restore).not.toHaveBeenCalled();
});

test("a stale draft save preserves the human's unsaved text for review", async () => {
  const api = setup();
  api.saveDraft.mockRejectedValueOnce(
    new ApiError(412, "PRECONDITION_FAILED", "Draft changed; review again."),
  );
  const title = await screen.findByRole("textbox", { name: "Draft title" });
  fireEvent.change(title, { target: { value: "Human title" } });
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByText(/Draft changed; review again/)).toBeVisible();
  expect(title).toHaveValue("Human title");
  expect(sessionStorage.getItem(`composer:title:${user.id}:${draft.id}`)).toBe(
    "Human title",
  );
  expect(api.draft).toHaveBeenCalledTimes(2);
});

test("copy-forward restoration references the exact selected revision", async () => {
  const api = setup();
  await screen.findByText(/Selected revision 1/);
  fireEvent.click(
    screen.getByRole("button", { name: "Restore as new revision" }),
  );
  await waitFor(() =>
    expect(api.restore).toHaveBeenCalledWith(
      draft,
      revision.id,
      expect.any(String),
    ),
  );
  expect(api.startStep).not.toHaveBeenCalled();
});

test("Office-source suggestions cannot claim to publish native binary edits", async () => {
  const api = setup({ proposalState: "accepted" });
  expect(
    await screen.findByText(/These notes do not edit the Office file/),
  ).toBeVisible();
  expect(screen.getByRole("link", { name: "open 2md" })).toHaveAttribute(
    "href",
    "/revert",
  );
  expect(screen.queryByText("Publish approved text")).not.toBeInTheDocument();
  expect(api.publishProposal).not.toHaveBeenCalled();
});

test("approved Markdown suggestions retain their publication action", async () => {
  setup({ proposalState: "accepted", markdown: true });
  expect(await screen.findByText("Publish approved text")).toBeInTheDocument();
});

test("a human Markdown edit must be saved before explicit immutable publication", async () => {
  const api = setup({ markdown: true });
  const editor = await screen.findByRole("textbox", {
    name: "Editable Markdown",
  });
  expect(editor).toHaveValue("# Original");
  fireEvent.change(editor, { target: { value: "# Human approved edit" } });
  expect(
    screen.getByRole("button", { name: "Publish saved Markdown" }),
  ).toBeDisabled();
  const savedDraft = {
    ...draft,
    content: "# Human approved edit",
    source_media_type: "text/markdown",
    version: 3,
    etag: '"draft-3"',
  };
  api.saveDraft.mockResolvedValueOnce({ data: savedDraft });
  api.draft.mockResolvedValue(savedDraft);
  fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Publish saved Markdown" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Publish saved Markdown" }),
  );
  await waitFor(() =>
    expect(api.publishDraft).toHaveBeenCalledWith(
      savedDraft,
      expect.any(String),
    ),
  );
  expect(api.startStep).not.toHaveBeenCalled();
});

test("Generate submits the current approved Markdown revision without a model call", async () => {
  const api = setup({ markdown: true });
  await screen.findByRole("button", { name: "Generate DOCX" });
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenCalledWith(
      expect.objectContaining({ id: draft.id, etag: draft.etag }),
      revision.id,
      {
        output: "docx",
        template_id: null,
        template_version_id: null,
        presentation_dialect: null,
        slide_level: null,
      },
      expect.any(String),
    ),
  );
  expect(api.publishDraft).not.toHaveBeenCalled();
  expect(api.startStep).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).not.toHaveAttribute("href");
  expect(await screen.findByText(/Generation · DOCX · queued/)).toBeVisible();
});

test("Generate publishes current content when the Markdown artifact lacks approval metadata", async () => {
  const api = setup({ markdown: true, unapprovedMarkdownRevision: true });
  await screen.findByRole("button", { name: "Generate DOCX" });
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenCalledWith(
      expect.objectContaining({ id: draft.id }),
      revision.id,
      expect.objectContaining({ output: "docx" }),
      expect.any(String),
    ),
  );
  expect(api.publishDraft).toHaveBeenCalledWith(
    expect.objectContaining({ content: "# Original" }),
    expect.any(String),
  );
  expect(api.publishDraft.mock.invocationCallOrder[0]).toBeLessThan(
    api.startGeneration.mock.invocationCallOrder[0]!,
  );
});

test("semantic history distinguishes approved Markdown from template changes", async () => {
  setup({ markdown: true, semanticDiff: true });
  expect(
    await screen.findByText("Approved Markdown changes from previous revision"),
  ).toBeInTheDocument();
  expect(screen.getByText("No semantic text change.")).toBeInTheDocument();
  expect(screen.getByText("template_reference")).toBeInTheDocument();
});

test("template lookup failure does not silently replace the selected style with Pandoc default", async () => {
  const api = setup({ markdown: true });
  const originalGenerate = await screen.findByRole("button", {
    name: "Generate DOCX",
  });
  await waitFor(() => expect(originalGenerate).toBeEnabled());
  api.conversionOptions.mockRejectedValueOnce(
    new ApiError(503, "UNAVAILABLE", "Templates unavailable"),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pdf" },
  });
  expect(
    await screen.findByText(/Templates could not be loaded/),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Generate PDF" })).toBeDisabled();
  expect(api.startGeneration).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
});

test("Generate freezes an unsaved human edit before submitting the conversion", async () => {
  const api = setup({ markdown: true });
  const saved = {
    ...draft,
    content: "# Human edit",
    source_media_type: "text/markdown",
    version: 3,
    etag: '"draft-3"',
  };
  const published = {
    ...revision,
    id: "00000000-0000-4000-8000-000000000202",
    approved_values: '{"content":"# Human edit"}',
  };
  api.saveDraft.mockResolvedValue({ data: saved });
  api.publishDraft.mockResolvedValue({ data: published });
  api.draft.mockResolvedValueOnce({
    ...draft,
    source_media_type: "text/markdown",
    content: "# Original",
  });
  api.draft.mockResolvedValue(saved);
  await screen.findByRole("textbox", { name: "Editable Markdown" });
  fireEvent.change(screen.getByRole("textbox", { name: "Editable Markdown" }), {
    target: { value: "# Human edit" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenCalledWith(
      saved,
      published.id,
      expect.objectContaining({ output: "docx" }),
      expect.any(String),
    ),
  );
  expect(api.saveDraft.mock.invocationCallOrder[0]).toBeLessThan(
    api.publishDraft.mock.invocationCallOrder[0]!,
  );
  expect(api.publishDraft.mock.invocationCallOrder[0]).toBeLessThan(
    api.startGeneration.mock.invocationCallOrder[0]!,
  );
  expect(api.startStep).not.toHaveBeenCalled();
});

test("a completed generation recovered after refresh publishes with a fresh draft precondition", async () => {
  const api = setup({ markdown: true, restoreGeneration: true });
  expect(await screen.findByText(/Generated revision is ready/)).toBeVisible();
  expect(api.generation).toHaveBeenCalledWith(draft.id, queuedGeneration.id);
  expect(api.publishGeneration).toHaveBeenCalledWith(
    expect.objectContaining({ id: draft.id, etag: draft.etag }),
    queuedGeneration.id,
    expect.any(String),
  );
  expect(api.startStep).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).not.toHaveAttribute("href");
  expect(
    sessionStorage.getItem(`composer:generation:${user.id}:${draft.id}`),
  ).toBeNull();
});

test("a lost generation response is recovered by owner list without resubmission", async () => {
  const api = setup({ markdown: true, recoverGenerationFromList: true });
  expect(await screen.findByText(/Generated revision is ready/)).toBeVisible();
  expect(api.generations).toHaveBeenCalledWith(draft.id);
  expect(api.startGeneration).not.toHaveBeenCalled();
  expect(api.startStep).not.toHaveBeenCalled();
  expect(api.publishGeneration).toHaveBeenCalledWith(
    expect.objectContaining({ id: draft.id }),
    queuedGeneration.id,
    expect.any(String),
  );
});

test("a committed publication with a lost response reopens its exact revision and download", async () => {
  const api = setup({ markdown: true, recoveredPublication: true });
  const generatedId = "00000000-0000-4000-8000-000000000203";
  expect(await screen.findByText(/Selected revision 2/)).toBeVisible();
  expect(api.revision).toHaveBeenCalledWith(draft.id, generatedId);
  expect(screen.getByText(`Native preview for ${generatedId}`)).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    `/api/v1/composer/drafts/${draft.id}/revisions/${generatedId}/artifacts/download`,
  );
  expect(api.startGeneration).not.toHaveBeenCalled();
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(
    sessionStorage.getItem(`composer:generation:${user.id}:${draft.id}`),
  ).toBeNull();
});

test("a recovered publication whose first fetch fails retains the old download until retry", async () => {
  const api = setup({
    markdown: true,
    recoveredPublication: true,
    recoveredPublicationFetchFails: true,
  });
  const generatedId = "00000000-0000-4000-8000-000000000203";
  expect(
    await screen.findByText("Revision temporarily unavailable"),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).not.toHaveAttribute("href");
  expect(
    sessionStorage.getItem(`composer:generation:${user.id}:${draft.id}`),
  ).toBe(queuedGeneration.id);
  fireEvent.click(
    screen.getByRole("button", { name: "Open generated revision" }),
  );
  expect(
    await screen.findByText(`Native preview for ${generatedId}`),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    `/api/v1/composer/drafts/${draft.id}/revisions/${generatedId}/artifacts/download`,
  );
  expect(api.startGeneration).not.toHaveBeenCalled();
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a human edit made while conversion runs prevents automatic publication over it", async () => {
  const api = setup({ markdown: true });
  const generate = await screen.findByRole("button", { name: "Generate DOCX" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  await screen.findByText(/Generation · DOCX · queued/);
  fireEvent.change(screen.getByRole("textbox", { name: "Editable Markdown" }), {
    target: { value: "# New human edit during conversion" },
  });
  api.generation.mockResolvedValue({
    ...queuedGeneration,
    status: "succeeded",
    publishable: true,
  });
  expect(
    await screen.findByText(
      /Generation · DOCX · succeeded/,
      {},
      { timeout: 5000 },
    ),
  ).toBeVisible();
  expect(screen.getByText(/this result will not replace them/)).toBeVisible();
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
});

test.each(["success", "stale"])(
  "selecting another draft during a pending %s mutation keeps the new selection",
  async (outcome) => {
    const api = setup({ secondDraft: true });
    await screen.findByRole("textbox", { name: "Draft notes" });
    let resolveMessage: ((value: object) => void) | undefined;
    let rejectMessage: ((reason: unknown) => void) | undefined;
    api.addMessage.mockImplementationOnce(
      () =>
        new Promise((resolve, reject) => {
          resolveMessage = resolve;
          rejectMessage = reject;
        }),
    );
    fireEvent.change(
      screen.getByRole("textbox", { name: "Message or answer" }),
      {
        target: { value: "A message pending on the first report" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save message" }));
    await waitFor(() => expect(api.addMessage).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
      target: { value: "00000000-0000-4000-8000-000000000102" },
    });
    expect(
      await screen.findByDisplayValue("Second owner's selected text"),
    ).toBeVisible();
    fireEvent.change(
      screen.getByRole("textbox", { name: "Message or answer" }),
      {
        target: { value: "Second report's unsent message" },
      },
    );
    if (outcome === "stale")
      rejectMessage?.(
        new ApiError(412, "PRECONDITION_FAILED", "Draft changed"),
      );
    else resolveMessage?.({});
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save message" }),
      ).toBeEnabled(),
    );
    expect(screen.getByRole("combobox", { name: "Saved drafts" })).toHaveValue(
      "00000000-0000-4000-8000-000000000102",
    );
    expect(screen.getByRole("textbox", { name: "Draft notes" })).toHaveValue(
      "Second owner's selected text",
    );
    expect(
      screen.getByRole("textbox", { name: "Message or answer" }),
    ).toHaveValue("Second report's unsent message");
    expect(window.location.search).toBe(
      "?draft=00000000-0000-4000-8000-000000000102",
    );
    expect(api.draft.mock.calls.filter(([id]) => id === draft.id)).toHaveLength(
      1,
    );
  },
);

test.each([
  ["drafts", "Oldest draft"],
  ["messages", "Oldest message"],
  ["questions", "Oldest question"],
  ["proposals", "Oldest suggestion"],
  ["revisions", "Revision 1 · capture_source"],
] as const)("older authorized %s remain reachable", async (kind, expected) => {
  const api = setup({ markdown: true });
  await screen.findByRole("textbox", { name: "Message or answer" });
  const id = (number: number) =>
    `00000000-0000-4000-8000-${number.toString(16).padStart(12, "0")}`;
  if (kind === "drafts")
    api.drafts.mockImplementation(async (_signal: unknown, offset = 0) =>
      offset === 0
        ? [
            draft,
            ...Array.from({ length: 99 }, (_, index) => ({
              ...draft,
              id: id(2000 + index),
              title: `Draft ${index}`,
            })),
          ]
        : [{ ...draft, id: id(2100), title: "Oldest draft" }],
    );
  if (kind === "messages")
    api.messages.mockImplementation(
      async (_draftId: string, _signal: unknown, offset = 0) =>
        offset === 0
          ? Array.from({ length: 100 }, (_, index) => ({
              id: id(3000 + index),
              role: "user",
              content: `Recent message ${index}`,
            }))
          : [{ id: id(3100), role: "user", content: "Oldest message" }],
    );
  if (kind === "questions")
    api.questions.mockImplementation(
      async (_draftId: string, _signal: unknown, offset = 0) =>
        offset === 0
          ? Array.from({ length: 100 }, (_, index) => ({
              ...question,
              id: id(4000 + index),
              state: "answered",
              text: `Recent question ${index}`,
            }))
          : [
              {
                ...question,
                id: id(4100),
                state: "answered",
                text: "Oldest question",
              },
            ],
    );
  if (kind === "proposals")
    api.proposals.mockImplementation(
      async (_draftId: string, _signal: unknown, offset = 0) =>
        offset === 0
          ? Array.from({ length: 100 }, (_, index) => ({
              ...proposal,
              id: id(5000 + index),
              state: "rejected",
              proposed_value: `Recent suggestion ${index}`,
            }))
          : [
              {
                ...proposal,
                id: id(5100),
                state: "rejected",
                proposed_value: "Oldest suggestion",
              },
            ],
    );
  if (kind === "revisions")
    api.revisions.mockImplementation(
      async (_draftId: string, _signal: unknown, offset = 0) =>
        offset === 0
          ? Array.from({ length: 100 }, (_, index) => ({
              ...revision,
              id: index === 0 ? revision.id : id(6000 + index),
              number: 200 - index,
            }))
          : [{ ...revision, id: id(6100), number: 1 }],
    );
  fireEvent.change(screen.getByRole("textbox", { name: "Message or answer" }), {
    target: { value: "Refresh the newest page" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save message" }));
  fireEvent.click(
    await screen.findByRole("button", { name: `Load older ${kind}` }),
  );
  expect(await screen.findByText(expected)).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Saved drafts" })).toHaveValue(
    draft.id,
  );
  expect(
    screen.getByRole("combobox", { name: "Revision history" }),
  ).toHaveValue(revision.id);
});

test("an exactly selected old revision stays in the selector after newest-page refresh", async () => {
  const api = setup({ markdown: true });
  await screen.findByRole("combobox", { name: "Revision history" });
  const newest = Array.from({ length: 100 }, (_, index) => ({
    ...revision,
    id: `00000000-0000-4000-8000-${(7000 + index).toString(16).padStart(12, "0")}`,
    number: 200 - index,
  }));
  api.revisions.mockResolvedValue(newest);
  fireEvent.change(screen.getByRole("textbox", { name: "Message or answer" }), {
    target: { value: "Trigger a safe refresh" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save message" }));
  await waitFor(() => expect(api.revisions).toHaveBeenCalledTimes(2));
  expect(
    screen.getByRole("combobox", { name: "Revision history" }),
  ).toHaveValue(revision.id);
  expect(
    screen.getByRole("option", { name: "Revision 1 · capture_source" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Load older revisions" }),
  ).toBeEnabled();
});
