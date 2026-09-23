import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ComposerWorkspace } from "../src/composer/workspace";
import type { ComposerWorkspaceApi } from "../src/composer/workspace-api";

vi.mock("../src/composer/preview/composer-preview", () => ({
  ComposerPreview: ({ revisionId }: { revisionId: string }) => (
    <p>Native preview for {revisionId}</p>
  ),
}));

const ownerId = "00000000-0000-4000-8000-000000000001";
const draftId = "00000000-0000-4000-8000-000000000101";
const revisionId = "00000000-0000-4000-8000-000000000201";
const templateId = "00000000-0000-4000-8000-000000000301";
const templateVersionId = "00000000-0000-4000-8000-000000000302";
const owner = {
  active: true,
  effective_idle_minutes: 30,
  id: ownerId,
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};
const draft = {
  id: draftId,
  title: "Review draft",
  content: "# Approved source",
  version: 2,
  current_revision_id: revisionId,
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
  content: "# Second owner's reviewed text",
  current_revision_id: null,
};
const revision = {
  id: revisionId,
  draft_id: draftId,
  number: 1,
  operation: "capture_source",
  provenance: "human:source",
  source_sha256: "a".repeat(64),
  template_reference: null,
  approved_values: '{"content":"# Approved source"}',
  render_options: "{}",
  model_identity: null,
  artifacts: [
    {
      kind: "preview",
      sha256: "a".repeat(64),
      size: 17,
      media_type: "text/markdown",
    },
    {
      kind: "download",
      sha256: "a".repeat(64),
      size: 17,
      media_type: "text/markdown",
    },
  ],
  restored_from_revision_id: null,
  created_at: "2026-09-23T12:00:00Z",
};
const connection = {
  allowed_user_ids: [],
  authorized: true,
  client_certificate_present: false,
  credential_present: true,
  enabled: true,
  endpoint: "https://llm.example/v1",
  etag: '"connection-1"',
  id: "00000000-0000-4000-8000-000000000401",
  identity_mode: "shared",
  internal_ca_present: false,
  name: "Approved model",
  permitted_models: ["small-model"],
  scope: "personal",
  selected_model: "small-model",
  status: "ready",
  status_message: null,
};
const alternateConnection = {
  ...connection,
  id: "00000000-0000-4000-8000-000000000402",
  name: "Second approved model",
  endpoint: "https://other-llm.example/v1",
  selected_model: "alternate-small",
  permitted_models: ["alternate-small", "alternate-large"],
};
const template = {
  id: templateId,
  current_version_id: templateVersionId,
  name: "Approved style",
};
const question = {
  id: "00000000-0000-4000-8000-000000000501",
  draft_id: draftId,
  model_step_id: "00000000-0000-4000-8000-000000000601",
  base_version: 2,
  state: "pending",
  text: "Which date is approved?",
  answer_message_id: null,
  answer_content: null,
  created_at: "2026-09-23T12:00:00Z",
  answered_at: null,
};
const proposal = {
  id: "00000000-0000-4000-8000-000000000602",
  draft_id: draftId,
  base_version: 2,
  state: "pending",
  proposed_value: "Unverified model text",
  decided_value: null,
  provenance: "model:small-model",
  created_at: "2026-09-23T12:00:00Z",
  decided_at: null,
  decided_by: null,
};
const pendingStep = {
  id: "00000000-0000-4000-8000-000000000601",
  draft_id: draftId,
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
};
const completedGeneration = {
  id: "00000000-0000-4000-8000-000000000701",
  draft_id: draftId,
  source_revision_id: revisionId,
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function setup({
  preserveSession = false,
  restoreGeneration = false,
  generationState,
  questions = [],
  proposals = [],
  messages = [],
  otherDraft = false,
  connectionList = [connection],
  restoredStep,
  revisionList = [revision],
  selectedRevision = revision,
}: {
  preserveSession?: boolean;
  restoreGeneration?: boolean;
  generationState?: typeof completedGeneration;
  questions?: object[];
  proposals?: object[];
  messages?: object[];
  otherDraft?: boolean;
  connectionList?: object[];
  restoredStep?: { id: string; [key: string]: unknown };
  revisionList?: object[];
  selectedRevision?: typeof revision;
} = {}) {
  if (!preserveSession) sessionStorage.clear();
  if (restoreGeneration || generationState)
    sessionStorage.setItem(
      `composer:generation:${ownerId}:${draftId}`,
      (generationState ?? completedGeneration).id,
    );
  if (restoredStep)
    sessionStorage.setItem(
      `composer:step:${ownerId}:${draftId}`,
      restoredStep.id,
    );
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
        maximum_upload_bytes: 1_000,
        maximum_output_tokens: 1024,
        personal_connections_allowed: true,
      }),
      connections: vi.fn().mockResolvedValue(connectionList),
    },
    drafts: vi
      .fn()
      .mockResolvedValue(otherDraft ? [draft, secondDraft] : [draft]),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id ? secondDraft : draft,
      ),
    messages: vi.fn().mockResolvedValue(messages),
    proposals: vi.fn().mockResolvedValue(proposals),
    questions: vi.fn().mockResolvedValue(questions),
    revisions: vi.fn().mockResolvedValue(revisionList),
    revision: vi.fn().mockResolvedValue(selectedRevision),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue([template]),
    startGeneration: vi.fn(),
    generation: vi
      .fn()
      .mockResolvedValue(generationState ?? completedGeneration),
    generations: vi.fn().mockResolvedValue([]),
    cancelGeneration: vi.fn(),
    publishGeneration: vi.fn(),
    diff: vi.fn().mockResolvedValue({
      from_revision_id: revisionId,
      to_revision_id: revisionId,
      status: "unchanged",
      reason: null,
      scope: "approved_markdown",
      metadata_changes: [],
      changes: [],
    }),
    download: vi.fn().mockResolvedValue(new Response("# Approved source")),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn().mockResolvedValue(pendingStep),
    step: vi.fn().mockResolvedValue(restoredStep ?? pendingStep),
    cancelStep: vi
      .fn()
      .mockResolvedValue({ ...pendingStep, status: "cancelled" }),
    decide: vi.fn(),
    answerQuestion: vi.fn().mockResolvedValue({ data: question }),
    publishProposal: vi.fn(),
    publishDraft: vi.fn().mockResolvedValue({ data: revision }),
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

function expectNonNativeDownloadControl(number: number) {
  const name = `Download revision ${number}`;
  const button = screen.getByRole("button", { name });
  expect(button).toBeVisible();
  expect(button).not.toHaveAttribute("href");
  expect(screen.queryByRole("link", { name })).toBeNull();
  return button;
}

test("capability lookup failure retains the exact saved revision and blocks model requests", async () => {
  const api = setup();
  await screen.findByText(/Selected revision 1/);
  api.connections.capabilities.mockRejectedValue(
    new Error("private settings failure"),
  );
  fireEvent.change(
    await screen.findByRole("combobox", { name: "Saved drafts" }),
    {
      target: { value: draftId },
    },
  );
  expect(
    await screen.findByText(/Model connection details could not be loaded/),
  ).toBeVisible();
  expect(screen.getByText(/Selected revision 1/)).toBeVisible();
  expectNonNativeDownloadControl(1);
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    { target: { value: "Reviewed text" } },
  );
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    {
      target: { value: "50" },
    },
  );
  expect(
    screen.getByRole("button", { name: "Send reviewed text" }),
  ).toBeDisabled();
  expect(api.startStep).not.toHaveBeenCalled();
  expect(document.body).not.toHaveTextContent("private settings failure");
});

test("rejected source creation keeps the selected file for a successful retry", async () => {
  const api = setup();
  api.createDraft
    .mockRejectedValueOnce(
      new ApiError(503, "UNAVAILABLE", "Upload is unavailable."),
    )
    .mockResolvedValueOnce({ data: draft });
  const source = new File(["# Human source"], "source.md", {
    type: "text/markdown",
  });
  fireEvent.change(await screen.findByLabelText("Markdown or Office source"), {
    target: { files: [source] },
  });
  const create = screen.getByRole("button", { name: "Create draft" });
  fireEvent.click(create);
  expect(await screen.findByText("Upload is unavailable.")).toBeVisible();
  expect(screen.getByText(/source.md/)).toBeVisible();
  fireEvent.click(create);
  await waitFor(() => expect(api.createDraft).toHaveBeenCalledTimes(2));
  expect(api.createDraft.mock.calls[1]?.[0]).toBe(source);
  expect(
    await screen.findByText(/source passed upload validation/),
  ).toBeVisible();
});

test("unsupported, oversized, and unreadable chat drops preserve a human message", async () => {
  const api = setup();
  const message = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(message, { target: { value: "Human note" } });
  const drop = (file: File) =>
    fireEvent.drop(message, {
      dataTransfer: { files: [file], getData: () => "" },
    });

  drop(new File(["binary"], "notes.pdf"));
  expect(
    await screen.findByText(/Drop a Markdown file or plain text/),
  ).toBeVisible();
  drop(new File(["x".repeat(1_001)], "large.md"));
  expect(
    await screen.findByText(/exceeds the available upload limit/),
  ).toBeVisible();
  const unreadable = new File(["private"], "unreadable.md");
  vi.spyOn(unreadable, "text").mockRejectedValue(
    new Error("private read error"),
  );
  drop(unreadable);
  expect(
    await screen.findByText(/Markdown file could not be read/),
  ).toBeVisible();
  expect(message).toHaveValue("Human note");
  expect(api.addMessage).not.toHaveBeenCalled();
});

test("plain-text conversation drops build an unsent human message and ignore empty data", async () => {
  const api = setup();
  const message = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  const dropText = (text: string) =>
    fireEvent.drop(message, {
      dataTransfer: { files: [], getData: () => text },
    });

  dropText("First reviewed passage");
  expect(message).toHaveValue("First reviewed passage");
  dropText("Second reviewed passage");
  expect(message).toHaveValue(
    "First reviewed passage\n\nSecond reviewed passage",
  );
  dropText("");
  expect(message).toHaveValue(
    "First reviewed passage\n\nSecond reviewed passage",
  );
  expect(sessionStorage.getItem(`composer:message:${ownerId}:${draftId}`)).toBe(
    "First reviewed passage\n\nSecond reviewed passage",
  );
  expect(api.addMessage).not.toHaveBeenCalled();
  expect(api.startStep).not.toHaveBeenCalled();
});

test("template lookup recovery preserves a selected style and generates with its immutable version", async () => {
  const api = setup();
  const style = await screen.findByRole("combobox", { name: "Style template" });
  await waitFor(() => expect(style).toBeEnabled());
  fireEvent.change(style, { target: { value: templateId } });
  api.templates.mockRejectedValueOnce(
    new ApiError(503, "UNAVAILABLE", "Unavailable"),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pdf" },
  });
  expect(await screen.findByText(/Generation is paused/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Generate PDF" })).toBeDisabled();
  expect(api.startGeneration).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry templates" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Generate PDF" })).toBeEnabled(),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Style template" }), {
    target: { value: templateId },
  });
  api.startGeneration.mockResolvedValue({
    data: {
      id: "00000000-0000-4000-8000-000000000701",
      draft_id: draftId,
      source_revision_id: revisionId,
      job_id: "00000000-0000-4000-8000-000000000801",
      status: "queued",
      output: "pdf",
      template_id: templateId,
      template_version_id: templateVersionId,
      presentation_dialect: null,
      slide_level: null,
      result_revision_id: null,
      publishable: false,
      created_at: "2026-09-23T12:00:00Z",
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Generate PDF" }));
  await waitFor(() => expect(api.startGeneration).toHaveBeenCalledOnce());
  expect(api.startGeneration).toHaveBeenCalledWith(
    expect.objectContaining({ id: draftId }),
    revisionId,
    expect.objectContaining({
      output: "pdf",
      template_id: templateId,
      template_version_id: templateVersionId,
    }),
    expect.any(String),
  );
  expect(api.startStep).not.toHaveBeenCalled();
});

test("a stale generation publication keeps the prior download and retries with the same request key", async () => {
  const api = setup({ restoreGeneration: true });
  api.publishGeneration
    .mockRejectedValueOnce(
      new ApiError(412, "PRECONDITION_FAILED", "Private stale revision"),
    )
    .mockResolvedValueOnce({ data: revision });

  expect(
    await screen.findByText(/draft changed while generating/),
  ).toBeVisible();
  expectNonNativeDownloadControl(1);
  expect(
    sessionStorage.getItem(`composer:generation:${ownerId}:${draftId}`),
  ).toBe(completedGeneration.id);
  const firstKey = api.publishGeneration.mock.calls[0]?.[2];
  expect(firstKey).toEqual(expect.any(String));

  api.generation.mockResolvedValueOnce({ ...completedGeneration });
  fireEvent.click(screen.getByRole("button", { name: "Retry publication" }));
  await waitFor(() => expect(api.publishGeneration).toHaveBeenCalledTimes(2));
  expect(api.publishGeneration.mock.calls[1]?.[2]).toBe(firstKey);
  expect(await screen.findByText(/Generated revision is ready/)).toBeVisible();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("an unauthorized draft list expires the browser view before private content is shown", async () => {
  const api = setup();
  api.drafts.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private detail"),
  );
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(api.revision).not.toHaveBeenCalled();
  expect(document.body).not.toHaveTextContent("# Approved source");
});

test("an obsolete template result cannot replace the choices for a newly selected output", async () => {
  const api = setup();
  const style = await screen.findByRole("combobox", { name: "Style template" });
  await waitFor(() => expect(style).toBeEnabled());
  let resolveOld!: (value: (typeof template)[]) => void;
  const oldLookup = new Promise<(typeof template)[]>((resolve) => {
    resolveOld = resolve;
  });
  const newTemplate = {
    id: "00000000-0000-4000-8000-000000000303",
    current_version_id: "00000000-0000-4000-8000-000000000304",
    name: "Presentation style",
  };
  api.templates
    .mockImplementationOnce(() => oldLookup)
    .mockResolvedValueOnce([newTemplate]);
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pdf" },
  });
  await waitFor(() => expect(api.templates).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pptx" },
  });
  await screen.findByRole("option", { name: "Presentation style" });
  resolveOld([template]);
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Output format" })).toHaveValue(
      "pptx",
    ),
  );
  expect(screen.queryByRole("option", { name: "Approved style" })).toBeNull();
  expect(
    screen.getByRole("option", { name: "Presentation style" }),
  ).toBeVisible();
});

test("model token limits reject invalid values without transmitting the reviewed text", async () => {
  const api = setup();
  const approved = await screen.findByRole("textbox", {
    name: "Exact approved text for transmission",
  });
  fireEvent.change(approved, { target: { value: "Human-reviewed paragraph" } });
  const tokens = screen.getByRole("spinbutton", {
    name: /Maximum output tokens/,
  });
  const send = screen.getByRole("button", { name: "Send reviewed text" });
  for (const invalid of ["0", "1025", "1.5"]) {
    fireEvent.change(tokens, { target: { value: invalid } });
    expect(send).toBeDisabled();
  }
  expect(approved).toHaveValue("Human-reviewed paragraph");
  expect(api.startStep).not.toHaveBeenCalled();
  fireEvent.change(tokens, { target: { value: "1024" } });
  expect(send).toBeEnabled();
});

test("a stale question answer keeps the human correction for an explicit retry", async () => {
  const api = setup({ questions: [question] });
  const answer = await screen.findByRole("textbox", { name: "Your answer" });
  fireEvent.change(answer, {
    target: { value: "The approved date is 23 September." },
  });
  api.answerQuestion.mockRejectedValueOnce(
    new ApiError(412, "PRECONDITION_FAILED", "Question changed."),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  expect(await screen.findByText("Question changed.")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Your answer" })).toHaveValue(
    "The approved date is 23 September.",
  );
  expect(api.startStep).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  await waitFor(() => expect(api.answerQuestion).toHaveBeenCalledTimes(2));
  expect(api.answerQuestion.mock.calls[1]?.[2]).toBe(
    "The approved date is 23 September.",
  );
  expect(
    await screen.findByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
  ).toHaveValue(
    "Question: Which date is approved?\nAnswer: The approved date is 23 September.",
  );
});

test("a failed proposal correction retains the edited value without publishing", async () => {
  const api = setup({ proposals: [proposal] });
  const correction = await screen.findByRole("textbox", {
    name: "Corrected value",
  });
  fireEvent.change(correction, {
    target: { value: "Human verified statement" },
  });
  api.decide.mockRejectedValueOnce(
    new ApiError(503, "UNAVAILABLE", "Try later."),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  expect(await screen.findByText("Try later.")).toBeVisible();
  expect(correction).toHaveValue("Human verified statement");
  expect(api.publishProposal).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  await waitFor(() => expect(api.decide).toHaveBeenCalledTimes(2));
  expect(api.decide.mock.calls[1]).toEqual([
    expect.objectContaining({ id: draftId }),
    proposal.id,
    "edited",
    "Human verified statement",
  ]);
  expect(api.publishProposal).not.toHaveBeenCalled();
});

test("cancelling a queued model step leaves the reviewed draft revision intact", async () => {
  const api = setup();
  api.step.mockResolvedValueOnce(pendingStep).mockResolvedValue({
    ...pendingStep,
    status: "cancelled",
  });
  const approved = await screen.findByRole("textbox", {
    name: "Exact approved text for transmission",
  });
  fireEvent.change(approved, { target: { value: "Reviewed source excerpt" } });
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    {
      target: { value: "64" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Send reviewed text" }));
  const cancel = await screen.findByRole("button", { name: "Cancel step" });
  fireEvent.click(cancel);
  await waitFor(() =>
    expect(api.cancelStep).toHaveBeenCalledWith(draftId, pendingStep.id),
  );
  expect(api.publishProposal).not.toHaveBeenCalled();
  expectNonNativeDownloadControl(1);
});

test("failed older-message pagination leaves recent conversation visible and supports retry", async () => {
  const recent = Array.from({ length: 100 }, (_, index) => ({
    id: `00000000-0000-4000-8000-${(5000 + index).toString(16).padStart(12, "0")}`,
    role: "user",
    content: `Recent message ${index}`,
  }));
  const api = setup({ messages: recent });
  const loadOlder = await screen.findByRole("button", {
    name: "Load older messages",
  });
  api.messages
    .mockRejectedValueOnce(
      new ApiError(503, "UNAVAILABLE", "History unavailable."),
    )
    .mockResolvedValueOnce([
      {
        id: "00000000-0000-4000-8000-000000009999",
        role: "user",
        content: "Older approved message",
      },
    ]);
  fireEvent.click(loadOlder);
  expect(await screen.findByText("History unavailable.")).toBeVisible();
  expect(screen.getByText("Recent message 0")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Load older messages" }));
  expect(await screen.findByText("Older approved message")).toBeVisible();
  expect(screen.getByText("Recent message 0")).toBeVisible();
});

test("a delayed Markdown read cannot append source text to another selected draft", async () => {
  const api = setup({ otherDraft: true });
  const message = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  let finishRead!: (text: string) => void;
  const file = new File(["# First draft source"], "source.md");
  vi.spyOn(file, "text").mockImplementation(
    () =>
      new Promise((resolve) => {
        finishRead = resolve;
      }),
  );
  fireEvent.drop(message, {
    dataTransfer: { files: [file], getData: () => "" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  const nextMessage = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(nextMessage, { target: { value: "Second draft note" } });
  finishRead("# First draft source");
  await waitFor(() => expect(nextMessage).toHaveValue("Second draft note"));
  expect(api.addMessage).not.toHaveBeenCalled();
  expect(window.location.search).toBe(`?draft=${secondDraft.id}`);
});

test("a late upload response cannot replace a newer draft selection", async () => {
  const api = setup({ otherDraft: true });
  let finishUpload!: (result: { data: typeof draft }) => void;
  api.createDraft.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishUpload = resolve;
      }),
  );
  fireEvent.change(await screen.findByLabelText("Markdown or Office source"), {
    target: { files: [new File(["# New"], "new.md")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  await waitFor(() => expect(api.createDraft).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  expect(await screen.findByDisplayValue(secondDraft.content)).toBeVisible();
  finishUpload({ data: draft });
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Saved drafts" })).toHaveValue(
      secondDraft.id,
    ),
  );
  expect(window.location.search).toBe(`?draft=${secondDraft.id}`);
  expect(
    screen.getByRole("textbox", { name: "Editable Markdown" }),
  ).toHaveValue(secondDraft.content);
});

test("a stale older-message response cannot enter a newly selected draft", async () => {
  const recent = Array.from({ length: 100 }, (_, index) => ({
    id: `00000000-0000-4000-8000-${(6000 + index).toString(16).padStart(12, "0")}`,
    role: "user",
    content: `First draft message ${index}`,
  }));
  const api = setup({ otherDraft: true, messages: recent });
  const loadOlder = await screen.findByRole("button", {
    name: "Load older messages",
  });
  let finishPage!: (page: object[]) => void;
  api.messages.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishPage = resolve;
      }),
  );
  fireEvent.click(loadOlder);
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  await screen.findByDisplayValue(secondDraft.content);
  finishPage([
    {
      id: "00000000-0000-4000-8000-000000009998",
      role: "user",
      content: "Only the old draft owns this message",
    },
  ]);
  await waitFor(() =>
    expect(
      screen.queryByText("Only the old draft owns this message"),
    ).toBeNull(),
  );
  expect(screen.getByRole("combobox", { name: "Saved drafts" })).toHaveValue(
    secondDraft.id,
  );
});

test("one pending message save cannot overwrite another draft and the rejected note can be retried", async () => {
  const api = setup({ otherDraft: true });
  const message = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(message, { target: { value: "First draft human note" } });
  const pending = deferred<object>();
  api.addMessage.mockReturnValueOnce(pending.promise);
  const save = screen.getByRole("button", { name: "Save message" });
  fireEvent.click(save);
  fireEvent.click(save);
  await waitFor(() => expect(api.addMessage).toHaveBeenCalledTimes(1));
  expect(save).toBeDisabled();
  expect(api.addMessage.mock.calls[0]?.[0]).toEqual(
    expect.objectContaining({ id: draftId }),
  );

  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  const secondMessage = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(secondMessage, {
    target: { value: "Second draft unsent note" },
  });
  await act(async () => {
    pending.reject(
      new ApiError(503, "UNAVAILABLE", "First draft save failed."),
    );
  });
  expect(secondMessage).toHaveValue("Second draft unsent note");
  expect(screen.queryByText("First draft save failed.")).toBeNull();
  expect(sessionStorage.getItem(`composer:message:${ownerId}:${draftId}`)).toBe(
    "First draft human note",
  );
  expect(api.addMessage).toHaveBeenCalledTimes(1);

  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: draftId },
  });
  const restored = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  await waitFor(() => expect(restored).toHaveValue("First draft human note"));
  api.addMessage.mockResolvedValueOnce({});
  fireEvent.click(screen.getByRole("button", { name: "Save message" }));
  await waitFor(() => expect(api.addMessage).toHaveBeenCalledTimes(2));
  expect(api.addMessage.mock.calls[1]?.[0]).toEqual(
    expect.objectContaining({ id: draftId }),
  );
  expect(api.addMessage.mock.calls[1]?.[1]).toBe("First draft human note");
  expect(await screen.findByText(/Message saved/)).toBeVisible();
});

test("cancelling a queued generation keeps the previous revision downloadable", async () => {
  const queued = {
    ...completedGeneration,
    status: "queued",
    publishable: false,
  };
  const api = setup({ generationState: queued });
  api.cancelGeneration.mockResolvedValue({ ...queued, status: "cancelled" });
  const cancel = await screen.findByRole("button", {
    name: "Cancel generation",
  });
  api.generation.mockResolvedValue({ ...queued, status: "cancelled" });
  fireEvent.click(cancel);
  await waitFor(() =>
    expect(api.cancelGeneration).toHaveBeenCalledWith(draftId, queued.id),
  );
  expectNonNativeDownloadControl(1);
  expect(api.publishGeneration).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
});

test("a failed history selection leaves the previous revision active until retry succeeds", async () => {
  const api = setup();
  await screen.findByRole("combobox", {
    name: "Revision history",
  });
  const newer = {
    ...revision,
    id: "00000000-0000-4000-8000-000000000202",
    number: 2,
    operation: "generate",
  };
  api.revisions.mockResolvedValue([newer, revision]);
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: draftId },
  });
  await screen.findByRole("option", { name: "Revision 2 · generate" });
  const history = screen.getByRole("combobox", { name: "Revision history" });
  api.revision
    .mockRejectedValueOnce(
      new ApiError(503, "UNAVAILABLE", "Revision is unavailable."),
    )
    .mockResolvedValueOnce(newer);
  fireEvent.change(history, { target: { value: newer.id } });
  expect(await screen.findByText("Revision is unavailable.")).toBeVisible();
  expect(history).toHaveValue(revisionId);
  expectNonNativeDownloadControl(1);
  fireEvent.change(history, { target: { value: newer.id } });
  await waitFor(() => expect(history).toHaveValue(newer.id));
  expect(screen.getByText(/Selected revision 2/)).toBeVisible();
});

test("model switching sends only the chosen authorized destination, then revocation blocks another request", async () => {
  const api = setup({ connectionList: [connection, alternateConnection] });
  const connectionSelect = await screen.findByRole("combobox", {
    name: "Connection",
  });
  fireEvent.change(connectionSelect, {
    target: { value: alternateConnection.id },
  });
  const modelSelect = screen.getByRole("combobox", { name: "Model" });
  expect(screen.getByRole("option", { name: "alternate-large" })).toBeVisible();
  fireEvent.change(modelSelect, { target: { value: "alternate-large" } });
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
    { target: { value: "Second endpoint reviewed text" } },
  );
  fireEvent.change(
    screen.getByRole("spinbutton", { name: /Maximum output tokens/ }),
    {
      target: { value: "100" },
    },
  );
  fireEvent.click(screen.getByRole("button", { name: "Send reviewed text" }));
  await waitFor(() => expect(api.startStep).toHaveBeenCalledOnce());
  expect(api.startStep).toHaveBeenCalledWith(
    expect.objectContaining({ id: draftId }),
    expect.objectContaining({
      connection_id: alternateConnection.id,
      approved_endpoint: alternateConnection.endpoint,
      approved_model: "alternate-large",
      content: "Second endpoint reviewed text",
    }),
    expect.any(String),
  );

  api.connections.connections.mockResolvedValue([
    { ...connection, authorized: false },
    { ...alternateConnection, authorized: false },
  ]);
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: draftId },
  });
  await screen.findByRole("textbox", {
    name: "Exact approved text for transmission",
  });
  expect(
    screen.queryByRole("option", { name: "Second approved model" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Send reviewed text" }),
  ).toBeDisabled();
  expect(api.startStep).toHaveBeenCalledOnce();
});

test.each([
  [
    "failed",
    { ...pendingStep, status: "failed", error_code: "MODEL_UNAVAILABLE" },
    "Safe error: MODEL_UNAVAILABLE",
  ],
  [
    "question",
    {
      ...pendingStep,
      status: "completed",
      intent: "question",
      question_id: question.id,
    },
    "A question awaits your answer below.",
  ],
  [
    "proposal",
    { ...pendingStep, status: "completed", proposal_id: proposal.id },
    "A proposal is ready for human review below.",
  ],
] as const)(
  "restored %s model result remains reviewable without rerunning the model",
  async (_kind, step, result) => {
    const api = setup({ restoredStep: step });
    const summary = await screen.findByText(
      `${step.intent === "question" ? "Question" : "Suggestion"} step ${step.status} · ${step.model_identity}`,
    );
    fireEvent.click(summary);
    expect(screen.getByText(result, { exact: false })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancel step" })).toBeNull();
    expect(api.startStep).not.toHaveBeenCalled();
  },
);

test.each([
  ["questions", question, "Which date is approved?", "Older date question"],
  ["proposals", proposal, "Unverified model text", "Older model suggestion"],
] as const)(
  "failed older %s loading leaves current review items and supports retry",
  async (kind, sample, currentText, olderText) => {
    const items = Array.from({ length: 100 }, (_, index) => ({
      ...sample,
      id: `00000000-0000-4000-8000-${(7000 + index).toString(16).padStart(12, "0")}`,
      ...(kind === "questions"
        ? { state: "answered", text: `${currentText} ${index}` }
        : { state: "rejected", proposed_value: `${currentText} ${index}` }),
    }));
    const api = setup(
      kind === "questions" ? { questions: items } : { proposals: items },
    );
    const loadOlder = await screen.findByRole("button", {
      name: `Load older ${kind}`,
    });
    const endpoint = kind === "questions" ? api.questions : api.proposals;
    endpoint
      .mockRejectedValueOnce(
        new ApiError(503, "UNAVAILABLE", "History retry required."),
      )
      .mockResolvedValueOnce([
        {
          ...sample,
          id: "00000000-0000-4000-8000-000000009991",
          ...(kind === "questions"
            ? { state: "answered", text: olderText }
            : { state: "rejected", proposed_value: olderText }),
        },
      ]);
    fireEvent.click(loadOlder);
    expect(await screen.findByText("History retry required.")).toBeVisible();
    expect(screen.getByText(`${currentText} 0`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `Load older ${kind}` }));
    expect(await screen.findByText(olderText)).toBeInTheDocument();
    expect(screen.getByText(`${currentText} 0`)).toBeInTheDocument();
  },
);

test("overlapping question and proposal pages preserve unsent human review", async () => {
  const questions = Array.from({ length: 100 }, (_, index) => ({
    ...question,
    id: `00000000-0000-4000-8000-${(9000 + index).toString(16).padStart(12, "0")}`,
    state: index === 0 ? "pending" : "answered",
    text: `Question ${index}`,
  }));
  const proposals = Array.from({ length: 100 }, (_, index) => ({
    ...proposal,
    id: `00000000-0000-4000-8000-${(10000 + index).toString(16).padStart(12, "0")}`,
    state: index === 0 ? "pending" : "rejected",
    proposed_value: `Suggestion ${index}`,
  }));
  const api = setup({ questions, proposals });
  const answer = await screen.findByRole("textbox", { name: "Your answer" });
  const correction = screen.getByRole("textbox", { name: "Corrected value" });
  fireEvent.change(answer, { target: { value: "Human answer in progress" } });
  fireEvent.change(correction, {
    target: { value: "Human correction in progress" },
  });

  api.questions.mockResolvedValueOnce([
    questions[0],
    {
      ...question,
      id: "00000000-0000-4000-8000-000000009999",
      text: "Older question",
    },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Load older questions" }));
  expect(await screen.findByText("Older question")).toBeVisible();
  expect(api.questions).toHaveBeenCalledWith(draftId, undefined, 100);
  expect(
    within(screen.getByRole("region", { name: "Questions" })).getAllByText(
      "Question 0",
    ),
  ).toHaveLength(1);
  expect(answer).toHaveValue("Human answer in progress");

  api.proposals.mockResolvedValueOnce([
    proposals[0],
    {
      ...proposal,
      id: "00000000-0000-4000-8000-000000010999",
      proposed_value: "Older suggestion",
    },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Load older proposals" }));
  expect(
    await screen.findByText("Older suggestion", { selector: "span" }),
  ).toBeInTheDocument();
  expect(api.proposals).toHaveBeenCalledWith(draftId, undefined, 100);
  expect(
    within(screen.getByRole("region", { name: "Proposals" })).getAllByText(
      "Suggestion 0",
      { selector: "span" },
    ),
  ).toHaveLength(1);
  expect(answer).toHaveValue("Human answer in progress");
  expect(correction).toHaveValue("Human correction in progress");
  expect(api.answerQuestion).not.toHaveBeenCalled();
  expect(api.decide).not.toHaveBeenCalled();
});

test("an answered question can be prepared for model review without another mutation", async () => {
  const answered = {
    ...question,
    state: "answered",
    answer_content: "Use the signed date.",
  };
  const api = setup({ questions: [answered] });
  fireEvent.click(
    await screen.findByRole("button", { name: "Prepare answer for model" }),
  );
  expect(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
  ).toHaveValue(
    "Question: Which date is approved?\nAnswer: Use the signed date.",
  );
  expect(api.answerQuestion).not.toHaveBeenCalled();
  expect(api.startStep).not.toHaveBeenCalled();
});

test("the same owner can resume unsent answers and corrections, then clears each only after its save", async () => {
  setup({ questions: [question], proposals: [proposal] });
  const answer = await screen.findByRole("textbox", { name: "Your answer" });
  const correction = screen.getByRole("textbox", { name: "Corrected value" });
  fireEvent.change(answer, { target: { value: "Use the signed date." } });
  fireEvent.change(correction, {
    target: { value: "Verified replacement text" },
  });
  const answerKey = `composer:answer:${ownerId}:${draftId}:${question.id}`;
  const correctionKey = `composer:correction:${ownerId}:${draftId}:${proposal.id}`;
  expect(sessionStorage.getItem(answerKey)).toBe("Use the signed date.");
  expect(sessionStorage.getItem(correctionKey)).toBe(
    "Verified replacement text",
  );

  cleanup();
  const api = setup({
    preserveSession: true,
    questions: [question],
    proposals: [proposal],
  });
  expect(
    await screen.findByRole("textbox", { name: "Your answer" }),
  ).toHaveValue("Use the signed date.");
  expect(screen.getByRole("textbox", { name: "Corrected value" })).toHaveValue(
    "Verified replacement text",
  );
  fireEvent.click(screen.getByRole("button", { name: "Save answer" }));
  await waitFor(() => expect(api.answerQuestion).toHaveBeenCalledOnce());
  expect(api.answerQuestion.mock.calls[0]?.[2]).toBe("Use the signed date.");
  await waitFor(() => expect(sessionStorage.getItem(answerKey)).toBeNull());
  expect(sessionStorage.getItem(correctionKey)).toBe(
    "Verified replacement text",
  );
  expect(
    screen.getByRole("textbox", {
      name: "Exact approved text for transmission",
    }),
  ).toHaveValue(
    "Question: Which date is approved?\nAnswer: Use the signed date.",
  );

  const saveCorrection = screen.getByRole("button", {
    name: "Save correction",
  });
  await waitFor(() => expect(saveCorrection).toBeEnabled());
  fireEvent.click(saveCorrection);
  await waitFor(() => expect(api.decide).toHaveBeenCalledOnce());
  expect(api.decide.mock.calls[0]?.[3]).toBe("Verified replacement text");
  await waitFor(() => expect(sessionStorage.getItem(correctionKey)).toBeNull());
});

test("semantic comparison failure keeps the selected exact revision available", async () => {
  const previous = {
    ...revision,
    id: "00000000-0000-4000-8000-000000000200",
    number: 1,
  };
  const latest = { ...revision, number: 2 };
  const api = setup({
    revisionList: [latest, previous],
    selectedRevision: latest,
  });
  api.diff.mockRejectedValueOnce(
    new ApiError(503, "UNAVAILABLE", "Comparison unavailable."),
  );
  expect(await screen.findByText("Comparison unavailable.")).toBeVisible();
  expect(screen.getByText(/Selected revision 2/)).toBeVisible();
  expectNonNativeDownloadControl(2);
});

test("copy-forward restore failure keeps the exact revision available for retry", async () => {
  const api = setup();
  api.restore
    .mockRejectedValueOnce(
      new ApiError(412, "PRECONDITION_FAILED", "Revision changed."),
    )
    .mockResolvedValueOnce({ data: revision });
  const restore = await screen.findByRole("button", {
    name: "Restore as new revision",
  });
  fireEvent.click(restore);
  expect(await screen.findByText("Revision changed.")).toBeVisible();
  expectNonNativeDownloadControl(1);
  fireEvent.click(
    screen.getByRole("button", { name: "Restore as new revision" }),
  );
  await waitFor(() => expect(api.restore).toHaveBeenCalledTimes(2));
  expect(api.restore.mock.calls[1]?.[1]).toBe(revisionId);
});

test("dragging a ZIP source replaces the chooser selection before upload", async () => {
  const api = setup();
  const label = (await screen.findByText("Markdown or Office source")).closest(
    "label",
  );
  expect(label).not.toBeNull();
  const zip = new File(["ZIP-with-assets"], "bundle.zip", {
    type: "application/zip",
  });
  fireEvent.dragEnter(label!);
  expect(screen.getByText("Drop the file now.")).toBeVisible();
  fireEvent.dragLeave(label!);
  expect(screen.getByText("Choose or drop a source file.")).toBeVisible();
  fireEvent.drop(label!, {
    dataTransfer: { files: [zip] },
  });
  expect(screen.getByText(/bundle.zip/)).toBeVisible();
  api.createDraft.mockResolvedValue({ data: draft });
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  await waitFor(() => expect(api.createDraft).toHaveBeenCalledWith(zip));
});

test("a paginated-history 401 closes private UI and a late preview response cannot rehydrate it", async () => {
  const recent = Array.from({ length: 100 }, (_, index) => ({
    id: `00000000-0000-4000-8000-${(8000 + index).toString(16).padStart(12, "0")}`,
    role: "user",
    content: `Current message ${index}`,
  }));
  const api = setup({ messages: recent });
  const preview = deferred<Response>();
  api.download.mockImplementationOnce(() => preview.promise);
  const older = await screen.findByRole("button", {
    name: "Load older messages",
  });
  const page = deferred<object[]>();
  api.messages.mockImplementationOnce(() => page.promise);
  fireEvent.click(older);
  await waitFor(() => expect(api.messages).toHaveBeenCalledTimes(2));
  page.reject(new ApiError(401, "AUTHENTICATION_REQUIRED", "private history"));
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  await act(async () => {
    preview.resolve(new Response("private Markdown preview"));
    await preview.promise;
  });
  expect(screen.queryByText("private Markdown preview")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull();
});

test("a template-fetch 401 expires the session while a 403 keeps the owned revision accessible", async () => {
  const api = setup();
  const style = await screen.findByRole("combobox", { name: "Style template" });
  await waitFor(() => expect(style).toBeEnabled());
  api.templates.mockRejectedValueOnce(
    new ApiError(403, "FORBIDDEN", "This template is not available."),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pdf" },
  });
  expect(await screen.findByText(/Generation is paused/)).toBeVisible();
  expect(screen.getByRole("heading", { name: "Composer" })).toBeVisible();
  expectNonNativeDownloadControl(1);

  api.templates.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private session"),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pptx" },
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
});

test("a background model-step polling 401 closes the private workspace", async () => {
  const api = setup({ restoredStep: pendingStep });
  api.step
    .mockResolvedValueOnce(pendingStep)
    .mockRejectedValueOnce(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "private step"),
    );
  expect(await screen.findByText(/Suggestion step pending/)).toBeVisible();
  await waitFor(() => expect(api.step).toHaveBeenCalledTimes(2), {
    timeout: 4_000,
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
});

test("a semantic-diff 401 closes private revisions instead of showing stale content", async () => {
  const previous = {
    ...revision,
    id: "00000000-0000-4000-8000-000000000200",
    number: 1,
  };
  const latest = { ...revision, number: 2 };
  const api = setup({
    revisionList: [latest, previous],
    selectedRevision: latest,
  });
  api.diff.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private diff"),
  );
  await waitFor(() => expect(api.diff).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
});

test("a Markdown artifact 401 closes private preview and download controls", async () => {
  const api = setup();
  api.download.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private artifact"),
  );
  await waitFor(() => expect(api.download).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(
    screen.queryByRole("button", { name: "Download revision 1" }),
  ).toBeNull();
});

test("a 401 while selecting another draft closes the old private draft", async () => {
  const api = setup({ otherDraft: true });
  await screen.findByRole("textbox", { name: "Editable Markdown" });
  api.draft.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private draft"),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(screen.queryByText(secondDraft.content)).toBeNull();
});

test("a 401 while selecting a revision closes the old private revision", async () => {
  const api = setup();
  await screen.findByRole("combobox", { name: "Revision history" });
  const newer = {
    ...revision,
    id: "00000000-0000-4000-8000-000000000202",
    number: 2,
    operation: "generate",
  };
  api.revisions.mockResolvedValue([newer, revision]);
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: draftId },
  });
  await screen.findByRole("option", { name: "Revision 2 · generate" });
  api.revision.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "private revision"),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "Revision history" }), {
    target: { value: newer.id },
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
});

test.each([
  {
    format: "Markdown",
    mediaType: "text/markdown",
    bytes: "# Approved source",
    digest: "c00aa01de9a237d9dc52e8d3b4d439d2f038bd30f7579aa8df378e77bcd55a6e",
    extension: "md",
    activate: "pointer",
  },
  {
    format: "ZIP",
    mediaType: "application/zip",
    bytes: "PKzip-payload",
    digest: "0e81c0f7243486f6b2de0d725ccd14c2c6e7b5ab4c5094f9fb56969f94bb75af",
    extension: "zip",
    activate: "keyboard",
  },
] as const)(
  "$format artifact uses an authenticated verified callback for $activate activation",
  async ({ mediaType, bytes, digest, extension, activate }) => {
    const selectedRevision = {
      ...revision,
      artifacts: revision.artifacts.map((artifact) => ({
        ...artifact,
        media_type: mediaType,
        ...(artifact.kind === "download"
          ? { size: bytes.length, sha256: digest }
          : {}),
      })),
    };
    const api = setup({ selectedRevision });
    api.download.mockImplementation(
      async () =>
        new Response(bytes, { headers: { "content-type": mediaType } }),
    );
    const button = await screen.findByRole("button", {
      name: "Download revision 1",
    });
    expectNonNativeDownloadControl(1);
    const before = window.location.href;
    const originalCreate = Object.getOwnPropertyDescriptor(
      URL,
      "createObjectURL",
    );
    const originalRevoke = Object.getOwnPropertyDescriptor(
      URL,
      "revokeObjectURL",
    );
    const create = vi.fn((blob: Blob) => {
      expect(blob.size).toBe(bytes.length);
      expect(blob.type).toBe(mediaType);
      return "blob:verified-revision";
    });
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: create,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revoke,
    });
    const saved: Array<{ href: string; filename: string }> = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        saved.push({ href: this.href, filename: this.download });
      });
    try {
      if (activate === "keyboard") {
        button.focus();
        await userEvent.setup().keyboard("{Enter}");
      } else {
        fireEvent.click(button);
      }
      await waitFor(() => expect(create).toHaveBeenCalledOnce());
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        revisionId,
        "download",
      );
      expect(
        api.download.mock.calls.filter((call) => call[2] === "download"),
      ).toHaveLength(1);
      expect(saved).toEqual([
        {
          href: "blob:verified-revision",
          filename: `revision-${revisionId}.${extension}`,
        },
      ]);
      expect(window.location.href).toBe(before);
      expectNonNativeDownloadControl(1);
      await waitFor(() =>
        expect(revoke).toHaveBeenCalledWith("blob:verified-revision"),
      );
    } finally {
      click.mockRestore();
      if (originalCreate)
        Object.defineProperty(URL, "createObjectURL", originalCreate);
      else Reflect.deleteProperty(URL, "createObjectURL");
      if (originalRevoke)
        Object.defineProperty(URL, "revokeObjectURL", originalRevoke);
      else Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  },
);

test.each([
  [401, true],
  [403, false],
] as const)(
  "a %i during a modified ZIP download %s the private workspace",
  async (status, expires) => {
    const zipRevision = {
      ...revision,
      artifacts: revision.artifacts.map((artifact) => ({
        ...artifact,
        media_type: "application/zip",
      })),
    };
    const api = setup({ selectedRevision: zipRevision });
    const button = await screen.findByRole("button", {
      name: "Download revision 1",
    });
    expectNonNativeDownloadControl(1);
    const pending = deferred<Response>();
    api.download.mockReturnValueOnce(pending.promise);
    const before = window.location.href;
    fireEvent(button, new MouseEvent("auxclick", { bubbles: true, button: 1 }));
    fireEvent.contextMenu(button);
    expect(api.download).not.toHaveBeenCalled();
    expect(window.location.href).toBe(before);
    fireEvent.click(button, { ctrlKey: true, metaKey: true, shiftKey: true });
    await waitFor(() =>
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        revisionId,
        "download",
      ),
    );
    expect(window.location.href).toBe(before);
    expectNonNativeDownloadControl(1);
    await act(async () => {
      pending.reject(
        new ApiError(status, "DOWNLOAD_DENIED", "Download unavailable."),
      );
    });
    if (expires) {
      await waitFor(() =>
        expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
      );
      expect(
        screen.queryByRole("button", { name: "Download revision 1" }),
      ).toBeNull();
    } else {
      expect(await screen.findByText("Download unavailable.")).toBeVisible();
      expectNonNativeDownloadControl(1);
      expect(screen.getByText(/Selected revision 1/)).toBeVisible();
    }
  },
);
