import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach } from "vitest";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { ComposerWorkspace } from "../src/composer/workspace";
import type { ComposerWorkspaceApi } from "../src/composer/workspace-api";

vi.mock("../src/composer/preview/composer-preview", () => ({
  ComposerPreview: ({ revisionId }: { revisionId: string }) => (
    <p>Native preview for {revisionId}</p>
  ),
}));

afterEach(() => vi.restoreAllMocks());

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
  title: "Reviewed report",
  content: "# Approved source",
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
  content: "# Second source",
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
  approved_values: '{"content":"# Approved source"}',
  render_options: "{}",
  model_identity: null,
  artifacts: [
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function setup(includeSecondDraft = false) {
  sessionStorage.clear();
  window.history.replaceState(null, "", "/composer");
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  let currentDraft = draft;
  const api = {
    connections: {
      capabilities: vi.fn().mockResolvedValue({
        status: "ready",
        status_message: null,
        instance_connections_manageable: false,
        maximum_upload_bytes: 1_000,
        maximum_output_tokens: 1_024,
        personal_connections_allowed: true,
      }),
      connections: vi.fn().mockResolvedValue([connection]),
    },
    drafts: vi
      .fn()
      .mockResolvedValue(includeSecondDraft ? [draft, secondDraft] : [draft]),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === secondDraft.id ? secondDraft : currentDraft,
      ),
    messages: vi.fn().mockResolvedValue([]),
    proposals: vi.fn().mockResolvedValue([]),
    questions: vi.fn().mockResolvedValue([]),
    revisions: vi.fn().mockResolvedValue([revision]),
    revision: vi.fn().mockResolvedValue(revision),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue([]),
    startGeneration: vi.fn().mockResolvedValue({
      data: {
        id: "00000000-0000-4000-8000-000000000401",
        draft_id: draft.id,
        source_revision_id: revision.id,
        job_id: "00000000-0000-4000-8000-000000000501",
        status: "queued",
        output: "docx",
        template_id: null,
        template_version_id: null,
        presentation_dialect: null,
        slide_level: null,
        result_revision_id: null,
        publishable: false,
        created_at: "2026-09-23T12:00:00Z",
      },
    }),
    generation: vi.fn(),
    generations: vi.fn().mockResolvedValue([]),
    cancelGeneration: vi.fn(),
    publishGeneration: vi.fn(),
    diff: vi.fn(),
    download: vi.fn(),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn(),
    step: vi.fn(),
    cancelStep: vi.fn(),
    decide: vi.fn(),
    answerQuestion: vi.fn(),
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
  return {
    ...api,
    auth,
    setCurrentDraft(value: typeof draft) {
      currentDraft = value;
    },
  };
}

test("a failed Markdown read leaves later human typing and its saved copy untouched", async () => {
  const api = setup();
  const message = await screen.findByRole("textbox", {
    name: "Message or answer",
  });
  fireEvent.change(message, { target: { value: "First human note" } });
  const read = deferred<string>();
  const file = new File(["# source"], "attachment.md");
  vi.spyOn(file, "text").mockReturnValue(read.promise);
  fireEvent.drop(message, {
    dataTransfer: { files: [file], getData: () => "" },
  });
  fireEvent.change(message, {
    target: { value: "First human note, corrected during the read" },
  });
  await act(async () => read.reject(new Error("private file read detail")));
  expect(
    await screen.findByText(/Markdown file could not be read/),
  ).toBeVisible();
  expect(message).toHaveValue("First human note, corrected during the read");
  expect(
    sessionStorage.getItem(`composer:message:${owner.id}:${draft.id}`),
  ).toBe("First human note, corrected during the read");
  expect(document.body).not.toHaveTextContent("private file read detail");
  expect(api.addMessage).not.toHaveBeenCalled();
});

test.each([
  [403, "Source upload is forbidden.", false],
  [401, "Session expired.", true],
] as const)(
  "source creation honors a deferred %i without replacing saved edits",
  async (status, message, shouldExpire) => {
    const api = setup();
    const create = deferred<{ data: typeof draft }>();
    api.createDraft.mockReturnValue(create.promise);
    const editor = await screen.findByRole("textbox", {
      name: "Editable Markdown",
    });
    fireEvent.change(editor, { target: { value: "# Unsent human edit" } });
    const source = new File(["# New source"], "new.md");
    fireEvent.change(screen.getByLabelText("Markdown or Office source"), {
      target: { files: [source] },
    });
    const button = screen.getByRole("button", { name: "Create draft" });
    fireEvent.click(button);
    await waitFor(() => expect(api.createDraft).toHaveBeenCalledWith(source));
    expect(button).toBeDisabled();
    expect(window.location.search).toBe("");

    await act(async () =>
      create.reject(new ApiError(status, "UPLOAD_DENIED", message)),
    );
    if (shouldExpire) {
      expect(api.auth.snapshot().phase).toBe("anonymous");
      expect(
        screen.queryByRole("textbox", { name: "Editable Markdown" }),
      ).toBeNull();
      expect(screen.queryByText("new.md (12 bytes)")).toBeNull();
    } else {
      expect(await screen.findByText(message)).toBeVisible();
      expect(editor).toHaveValue("# Unsent human edit");
      expect(screen.getByText("new.md (12 bytes)")).toBeVisible();
      expect(button).toBeEnabled();
      expect(
        sessionStorage.getItem(`composer:content:${owner.id}:${draft.id}`),
      ).toBe("# Unsent human edit");
    }
    expect(api.startStep).not.toHaveBeenCalled();
  },
);

test("a delayed forbidden upload cannot put an error into another selected draft", async () => {
  const api = setup(true);
  const create = deferred<{ data: typeof draft }>();
  api.createDraft.mockReturnValue(create.promise);
  fireEvent.change(await screen.findByLabelText("Markdown or Office source"), {
    target: { files: [new File(["# new"], "new.md")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  await waitFor(() => expect(api.createDraft).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: secondDraft.id },
  });
  const editor = await screen.findByRole("textbox", {
    name: "Editable Markdown",
  });
  fireEvent.change(editor, { target: { value: "# Second draft human edit" } });
  await act(async () =>
    create.reject(new ApiError(403, "FORBIDDEN", "Old upload forbidden")),
  );
  expect(editor).toHaveValue("# Second draft human edit");
  expect(screen.queryByText("Old upload forbidden")).toBeNull();
  expect(window.location.search).toBe(`?draft=${secondDraft.id}`);
  expect(api.auth.snapshot().phase).toBe("authenticated");
});

test("direct Generate retains unsaved Markdown after a forbidden save and retries the reviewed text", async () => {
  const api = setup();
  api.saveDraft.mockRejectedValueOnce(
    new ApiError(403, "FORBIDDEN", "Saving this draft is forbidden."),
  );
  const editor = await screen.findByRole("textbox", {
    name: "Editable Markdown",
  });
  const title = screen.getByRole("textbox", { name: "Draft title" });
  fireEvent.change(title, { target: { value: "Reviewed title" } });
  fireEvent.change(editor, {
    target: { value: "# Reviewed human correction" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  expect(
    await screen.findByText("Saving this draft is forbidden."),
  ).toBeVisible();
  expect(title).toHaveValue("Reviewed title");
  expect(editor).toHaveValue("# Reviewed human correction");
  expect(api.publishDraft).not.toHaveBeenCalled();
  expect(api.startGeneration).not.toHaveBeenCalled();
  expect(api.auth.snapshot().phase).toBe("authenticated");

  const saved = {
    ...draft,
    title: "Reviewed title",
    content: "# Reviewed human correction",
    version: 3,
    etag: '"draft-3"',
  };
  api.saveDraft.mockImplementationOnce(async () => {
    api.setCurrentDraft(saved);
    return { data: saved };
  });
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  await waitFor(() => expect(api.startGeneration).toHaveBeenCalledOnce());
  expect(api.saveDraft).toHaveBeenLastCalledWith(
    expect.objectContaining({ id: draft.id }),
    "Reviewed title",
    "# Reviewed human correction",
  );
  expect(api.publishDraft).toHaveBeenCalledWith(saved, expect.any(String));
  expect(api.startGeneration.mock.calls[0]?.[0]).toMatchObject({
    id: draft.id,
    content: "# Reviewed human correction",
  });
  expect(api.startStep).not.toHaveBeenCalled();
});
