import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
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
    onDownload,
  }: {
    draftId: string;
    revisionId: string;
    format: "docx" | "pptx" | "pdf";
    downloadUrl: string;
    onDownload: (active: {
      draftId: string;
      revisionId: string;
      format: "docx" | "pptx" | "pdf";
      downloadUrl: string;
    }) => void;
  }) => (
    <>
      <p>
        Native preview for {retainedNativeRevision?.revisionId ?? revisionId}
      </p>
      <button
        type="button"
        onClick={() =>
          onDownload(
            retainedNativeRevision ?? {
              draftId,
              revisionId,
              format,
              downloadUrl,
            },
          )
        }
      >
        Download native revision
      </button>
    </>
  ),
}));

let retainedNativeRevision: {
  draftId: string;
  revisionId: string;
  format: "docx" | "pptx" | "pdf";
  downloadUrl: string;
} | null = null;

const ownerId = "00000000-0000-4000-8000-000000000001";
const draftId = "00000000-0000-4000-8000-000000000101";
const otherDraftId = "00000000-0000-4000-8000-000000000102";
const revisionId = "00000000-0000-4000-8000-000000000201";
const retainedRevisionId = "00000000-0000-4000-8000-000000000299";
const generatedSourceId = "00000000-0000-4000-8000-000000000202";
const generationId = "00000000-0000-4000-8000-000000000701";
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
  title: "Reviewed report",
  content: "# Approved report",
  version: 4,
  current_revision_id: revisionId,
  source_kind: "upload",
  source_media_type: "text/markdown",
  created_at: "2026-09-23T12:00:00Z",
  updated_at: "2026-09-23T12:00:00Z",
  etag: '"draft-4"',
};
const otherDraft = {
  ...draft,
  id: otherDraftId,
  title: "Other report",
  content: "# Other approved report",
  current_revision_id: null,
};
const revision = {
  id: revisionId,
  draft_id: draftId,
  number: 1,
  operation: "publish_draft",
  provenance: "human:source",
  source_sha256: "a".repeat(64),
  template_reference: null,
  approved_values: '{"content":"# Approved report"}',
  render_options: "{}",
  model_identity: null,
  artifacts: ["preview", "download"].map((kind) => ({
    kind,
    sha256: "a".repeat(64),
    size: 17,
    media_type: "text/markdown",
  })),
  restored_from_revision_id: null,
  created_at: "2026-09-23T12:00:00Z",
};
const docxType =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const nativeRevision = {
  ...revision,
  artifacts: revision.artifacts.map((artifact) => ({
    ...artifact,
    media_type: docxType,
    sha256: "be7d23714a6fb3056afe95040d401023b9ddc22110c1f350f7d60eb389413d94",
    size: 19,
  })),
};
const retainedRevision = { ...nativeRevision, id: retainedRevisionId };
const proposal = {
  id: "00000000-0000-4000-8000-000000000601",
  draft_id: draftId,
  base_version: 2,
  state: "accepted",
  proposed_value: "# Model suggestion",
  decided_value: "# Model suggestion",
  provenance: "model:small-model",
  created_at: "2026-09-23T12:00:00Z",
  decided_at: "2026-09-23T12:01:00Z",
  decided_by: ownerId,
};
const queuedGeneration = {
  id: generationId,
  draft_id: draftId,
  source_revision_id: revisionId,
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function setup(
  options: {
    approvedProposal?: "accepted" | "edited";
    approvedValues?: string;
    existingGeneration?: typeof queuedGeneration;
    native?: boolean;
    otherDraft?: boolean;
    publication?: Promise<{ data: typeof revision }>;
    templateCatalog?: {
      id: string;
      current_version_id: string | null;
      name: string;
    }[];
  } = {},
) {
  sessionStorage.clear();
  retainedNativeRevision = null;
  window.history.replaceState(null, "", "/composer");
  if (options.existingGeneration)
    sessionStorage.setItem(
      `composer:generation:${ownerId}:${draftId}`,
      options.existingGeneration.id,
    );
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(owner),
  } as unknown as ApiTransport);
  const currentRevision = {
    ...(options.native ? nativeRevision : revision),
    approved_values: options.approvedValues ?? revision.approved_values,
  };
  const api = {
    connections: {
      capabilities: vi.fn().mockResolvedValue({
        status: "ready",
        status_message: null,
        instance_connections_manageable: false,
        maximum_upload_bytes: 1_000_000,
        maximum_output_tokens: 1024,
        personal_connections_allowed: true,
      }),
      connections: vi.fn().mockResolvedValue([connection]),
    },
    drafts: vi
      .fn()
      .mockResolvedValue(options.otherDraft ? [draft, otherDraft] : [draft]),
    draft: vi
      .fn()
      .mockImplementation(async (id: string) =>
        id === otherDraftId ? otherDraft : draft,
      ),
    messages: vi.fn().mockResolvedValue([]),
    proposals: vi
      .fn()
      .mockResolvedValue(
        options.approvedProposal
          ? [{ ...proposal, state: options.approvedProposal }]
          : [],
      ),
    questions: vi.fn().mockResolvedValue([]),
    revisions: vi.fn().mockResolvedValue([currentRevision]),
    revision: vi
      .fn()
      .mockImplementation(async (_draftId: string, id: string) =>
        id === retainedRevisionId ? retainedRevision : currentRevision,
      ),
    conversionOptions: vi.fn().mockResolvedValue({
      resolved_template: null,
      template_version_id: null,
      selection_source: "pandoc_default",
    }),
    templates: vi.fn().mockResolvedValue(options.templateCatalog ?? []),
    startGeneration: vi.fn().mockResolvedValue({ data: queuedGeneration }),
    generation: vi
      .fn()
      .mockResolvedValue(options.existingGeneration ?? queuedGeneration),
    generations: vi.fn().mockResolvedValue([]),
    cancelGeneration: vi.fn(),
    publishGeneration: vi.fn().mockImplementation(() => options.publication),
    diff: vi.fn().mockResolvedValue({
      from_revision_id: revisionId,
      to_revision_id: revisionId,
      status: "unchanged",
      reason: null,
      scope: "approved_markdown",
      metadata_changes: [],
      changes: [],
    }),
    download: vi.fn().mockResolvedValue(new Response("# Approved report")),
    saveDraft: vi.fn(),
    createDraft: vi.fn(),
    addMessage: vi.fn(),
    startStep: vi.fn(),
    step: vi.fn(),
    cancelStep: vi.fn(),
    decide: vi.fn(),
    answerQuestion: vi.fn(),
    publishProposal: vi.fn(),
    publishDraft: vi.fn().mockResolvedValue({ data: currentRevision }),
    captureSource: vi.fn(),
    restore: vi.fn(),
  };
  render(
    <AuthProvider controller={auth}>
      <ComposerWorkspace api={api as unknown as ComposerWorkspaceApi} />
    </AuthProvider>,
  );
  return { api, auth };
}

function oldDownload() {
  return screen.getByRole("button", { name: "Download revision 1" });
}

test.each(["accepted", "edited"] as const)(
  "%s proposal approval is published before generation and keeps the old preview during preparation",
  async (state) => {
    const { api } = setup({ approvedProposal: state });
    const pending = deferred<{ data: typeof revision }>();
    const published = {
      ...revision,
      id: generatedSourceId,
      operation: `publish_proposal:${proposal.id}`,
      approved_values:
        state === "edited"
          ? '{"content":"# Human correction"}'
          : '{"content":"# Model suggestion"}',
    };
    const refreshed = { ...draft, version: 5, etag: '"draft-5"' };
    api.publishProposal.mockReturnValueOnce(pending.promise);
    api.draft.mockResolvedValueOnce(draft).mockResolvedValue(refreshed);
    const generate = await screen.findByRole("button", {
      name: "Generate DOCX",
    });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await waitFor(() =>
      expect(api.publishProposal).toHaveBeenCalledWith(
        draft,
        proposal.id,
        expect.any(String),
      ),
    );
    expect(api.startGeneration).not.toHaveBeenCalled();
    expect(oldDownload()).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Revision history" }),
    ).toHaveValue(revisionId);
    pending.resolve({ data: published });
    await waitFor(() =>
      expect(api.startGeneration).toHaveBeenCalledWith(
        refreshed,
        published.id,
        expect.objectContaining({ output: "docx" }),
        expect.any(String),
      ),
    );
    expect(api.publishDraft).not.toHaveBeenCalled();
    expect(api.startStep).not.toHaveBeenCalled();
  },
);

test.each(["{not-json", '{"content":"# Different text"}'])(
  "malformed or mismatched approval metadata %s requires a fresh immutable source",
  async (approvedValues) => {
    const { api } = setup({ approvedValues });
    const pending = deferred<{ data: typeof revision }>();
    api.publishDraft.mockReturnValueOnce(pending.promise);
    const generate = await screen.findByRole("button", {
      name: "Generate DOCX",
    });
    await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    await waitFor(() => expect(api.publishDraft).toHaveBeenCalledOnce());
    expect(api.startGeneration).not.toHaveBeenCalled();
    expect(oldDownload()).toBeVisible();
    pending.resolve({ data: { ...revision, id: generatedSourceId } });
    await waitFor(() =>
      expect(api.startGeneration).toHaveBeenCalledWith(
        expect.objectContaining({ id: draftId }),
        generatedSourceId,
        expect.objectContaining({ output: "docx" }),
        expect.any(String),
      ),
    );
  },
);

test("a stale generation submission reloads the draft and retains the exact old download", async () => {
  const { api } = setup();
  api.startGeneration.mockRejectedValueOnce(
    new ApiError(412, "PRECONDITION_FAILED", "Draft changed; review again."),
  );
  const generate = await screen.findByRole("button", {
    name: "Generate DOCX",
  });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  expect(await screen.findByText("Draft changed; review again.")).toBeVisible();
  expect(api.draft).toHaveBeenCalledTimes(2);
  fireEvent.click(oldDownload());
  await waitFor(() =>
    expect(api.download).toHaveBeenCalledWith(draftId, revisionId, "download"),
  );
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("an unauthorized generation submission expires the authenticated workspace", async () => {
  const { api } = setup();
  api.startGeneration.mockRejectedValueOnce(
    new ApiError(401, "AUTHENTICATION_REQUIRED", "Session expired."),
  );
  const generate = await screen.findByRole("button", {
    name: "Generate DOCX",
  });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

test("a 401 during automatic generation publication clears private Composer content", async () => {
  const publication = deferred<{ data: typeof revision }>();
  const { api } = setup({
    existingGeneration: {
      ...queuedGeneration,
      status: "succeeded",
      publishable: true,
    },
    publication: publication.promise,
  });
  expect(
    await screen.findByRole("button", { name: "Download revision 1" }),
  ).toBeVisible();
  await waitFor(() => expect(api.publishGeneration).toHaveBeenCalledOnce());
  await act(async () => {
    publication.reject(
      new ApiError(401, "AUTHENTICATION_REQUIRED", "Session expired."),
    );
    await publication.promise.catch(() => undefined);
  });
  await waitFor(() =>
    expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
  );
  expect(screen.queryByText("# Approved report")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Download revision 1" }),
  ).toBeNull();
});

test.each(["failed", "cancelled"])(
  "a recovered %s job cannot replace a retained revision",
  async (status) => {
    const { api } = setup({
      existingGeneration: { ...queuedGeneration, status },
    });
    expect(
      await screen.findByText(`Generation · DOCX · ${status}`),
    ).toBeVisible();
    expect(oldDownload()).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Revision history" }),
    ).toHaveValue(revisionId);
    expect(api.publishGeneration).not.toHaveBeenCalled();
    expect(api.startGeneration).not.toHaveBeenCalled();
  },
);

test("PowerPoint generation freezes the selected PPTX template and slide options", async () => {
  const { api } = setup();
  const pptxTemplate = {
    id: templateId,
    current_version_id: templateVersionId,
    name: "Slides",
  };
  api.templates.mockResolvedValue([pptxTemplate]);
  await screen.findByRole("combobox", { name: "Output format" });
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pptx" },
  });
  const style = await screen.findByRole("combobox", {
    name: "Style template",
  });
  await screen.findByRole("option", { name: "Slides" });
  fireEvent.change(style, { target: { value: templateId } });
  fireEvent.change(screen.getByRole("combobox", { name: "Markdown format" }), {
    target: { value: "marp" },
  });
  fireEvent.change(
    screen.getByRole("combobox", { name: "Slide heading level" }),
    {
      target: { value: "4" },
    },
  );
  const generate = screen.getByRole("button", { name: "Generate PPTX" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenCalledWith(
      draft,
      revisionId,
      {
        output: "pptx",
        template_id: templateId,
        template_version_id: templateVersionId,
        presentation_dialect: "marp",
        slide_level: 4,
      },
      expect.any(String),
    ),
  );
  expect(api.startStep).not.toHaveBeenCalled();
});

test("a delayed generation response cannot attach the first draft's job to a newly selected draft", async () => {
  const { api } = setup({ otherDraft: true });
  const pending = deferred<{ data: typeof queuedGeneration }>();
  api.startGeneration.mockReturnValueOnce(pending.promise);
  const generate = await screen.findByRole("button", {
    name: "Generate DOCX",
  });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  await waitFor(() => expect(api.startGeneration).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
    target: { value: otherDraftId },
  });
  await screen.findByDisplayValue("# Other approved report");
  pending.resolve({ data: queuedGeneration });
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Saved drafts" })).toHaveValue(
      otherDraftId,
    ),
  );
  expect(screen.queryByText(/Generation · DOCX · queued/)).toBeNull();
  expect(window.location.search).toBe(`?draft=${otherDraftId}`);
  expect(api.publishGeneration).not.toHaveBeenCalled();
});

function showRetainedNativeDownload() {
  retainedNativeRevision = {
    draftId,
    revisionId: retainedRevisionId,
    format: "docx",
    downloadUrl: `/api/v1/composer/drafts/${draftId}/revisions/${retainedRevisionId}/artifacts/download`,
  };
}

test("a retained native preview downloads only its own verified revision bytes", async () => {
  const { api } = setup({ native: true });
  const download = await screen.findByRole("button", {
    name: "Download native revision",
  });
  showRetainedNativeDownload();
  api.download.mockResolvedValueOnce(
    new Response("office-byte-payload", {
      headers: { "content-type": docxType },
    }),
  );
  const originalCreate = Object.getOwnPropertyDescriptor(
    URL,
    "createObjectURL",
  );
  const createObjectURL = vi.fn(() => "blob:private-revision");
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  try {
    fireEvent.click(download);
    await waitFor(() =>
      expect(api.revision).toHaveBeenCalledWith(draftId, retainedRevisionId),
    );
    await waitFor(() =>
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        retainedRevisionId,
        "download",
      ),
    );
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledOnce());
    expect(click).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("combobox", { name: "Revision history" }),
    ).toHaveValue(revisionId);
  } finally {
    click.mockRestore();
    if (originalCreate)
      Object.defineProperty(URL, "createObjectURL", originalCreate);
    else Reflect.deleteProperty(URL, "createObjectURL");
  }
});

test.each([
  [401, true],
  [403, false],
] as const)(
  "a %i on retained native download %s the private Composer view",
  async (status, expires) => {
    const { api } = setup({ native: true });
    const download = await screen.findByRole("button", {
      name: "Download native revision",
    });
    showRetainedNativeDownload();
    api.download.mockRejectedValueOnce(
      new ApiError(status, "DOWNLOAD_FORBIDDEN", "Download unavailable."),
    );
    fireEvent.click(download);
    await waitFor(() =>
      expect(api.revision).toHaveBeenCalledWith(draftId, retainedRevisionId),
    );
    await waitFor(() =>
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        retainedRevisionId,
        "download",
      ),
    );
    if (expires) {
      await waitFor(() =>
        expect(screen.queryByRole("heading", { name: "Composer" })).toBeNull(),
      );
      expect(
        screen.queryByRole("button", { name: "Download native revision" }),
      ).toBeNull();
    } else {
      expect(await screen.findByText("Download unavailable.")).toBeVisible();
      expect(download).toBeVisible();
      expect(
        screen.getByRole("combobox", { name: "Revision history" }),
      ).toHaveValue(revisionId);
    }
  },
);

test.each([
  ["size", "short"],
  ["digest", "office-byte-payloae"],
] as const)(
  "a native download with mismatched %s never saves a local artifact",
  async (_kind, bytes) => {
    const { api } = setup({ native: true });
    const download = await screen.findByRole("button", {
      name: "Download native revision",
    });
    showRetainedNativeDownload();
    api.download.mockResolvedValueOnce(
      new Response(bytes, { headers: { "content-type": docxType } }),
    );
    const originalCreate = Object.getOwnPropertyDescriptor(
      URL,
      "createObjectURL",
    );
    const createObjectURL = vi.fn(() => "blob:should-not-exist");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    try {
      fireEvent.click(download);
      expect(
        await screen.findByText(
          "The Composer request could not be completed. Try again.",
        ),
      ).toBeVisible();
      expect(api.revision).toHaveBeenCalledWith(draftId, retainedRevisionId);
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        retainedRevisionId,
        "download",
      );
      expect(createObjectURL).not.toHaveBeenCalled();
      expect(download).toBeVisible();
    } finally {
      if (originalCreate)
        Object.defineProperty(URL, "createObjectURL", originalCreate);
      else Reflect.deleteProperty(URL, "createObjectURL");
    }
  },
);

test("a stale generation precondition reloads the draft and retries from its new approved revision", async () => {
  const { api } = setup();
  const newRevisionId = "00000000-0000-4000-8000-000000000298";
  const refreshed = {
    ...draft,
    version: 5,
    etag: '"draft-5"',
    current_revision_id: newRevisionId,
  };
  const approved = { ...revision, id: newRevisionId, number: 2 };
  api.draft.mockResolvedValueOnce(draft).mockResolvedValue(refreshed);
  api.revision.mockImplementation(async (_draftId: string, id: string) =>
    id === newRevisionId ? approved : revision,
  );
  api.startGeneration.mockRejectedValueOnce(
    new ApiError(412, "PRECONDITION_FAILED", "Draft version changed."),
  );
  const generate = await screen.findByRole("button", {
    name: "Generate DOCX",
  });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  expect(await screen.findByText("Draft version changed.")).toBeVisible();
  expect(oldDownload()).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  await waitFor(() =>
    expect(api.revision).toHaveBeenCalledWith(draftId, newRevisionId),
  );
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenLastCalledWith(
      refreshed,
      newRevisionId,
      expect.objectContaining({ output: "docx" }),
      expect.any(String),
    ),
  );
  expect(api.startGeneration).toHaveBeenCalledTimes(2);
  expect(api.publishDraft).not.toHaveBeenCalled();
});

test("a rejected template version requires an explicit refreshed choice before retry", async () => {
  const firstVersion = {
    id: templateId,
    current_version_id: templateVersionId,
    name: "Reviewed style",
  };
  const { api } = setup({ templateCatalog: [firstVersion] });
  const style = await screen.findByRole("combobox", {
    name: "Style template",
  });
  await waitFor(() => expect(style).toBeEnabled());
  fireEvent.change(style, { target: { value: templateId } });
  api.startGeneration.mockRejectedValueOnce(
    new ApiError(422, "TEMPLATE_VERSION_STALE", "Style version changed."),
  );
  fireEvent.click(screen.getByRole("button", { name: "Generate DOCX" }));
  expect(await screen.findByText("Style version changed.")).toBeVisible();
  expect(api.startGeneration).toHaveBeenCalledWith(
    draft,
    revisionId,
    expect.objectContaining({
      template_id: templateId,
      template_version_id: templateVersionId,
    }),
    expect.any(String),
  );
  expect(oldDownload()).toBeVisible();
  const refreshedVersionId = "00000000-0000-4000-8000-000000000399";
  api.templates.mockResolvedValue([
    { ...firstVersion, current_version_id: refreshedVersionId },
  ]);
  fireEvent.change(screen.getByRole("combobox", { name: "Output format" }), {
    target: { value: "pdf" },
  });
  await waitFor(() => expect(style).toHaveValue(templateId));
  const retry = screen.getByRole("button", { name: "Generate PDF" });
  await waitFor(() => expect(retry).toBeEnabled());
  fireEvent.click(retry);
  await waitFor(() =>
    expect(api.startGeneration).toHaveBeenLastCalledWith(
      draft,
      revisionId,
      expect.objectContaining({
        output: "pdf",
        template_id: templateId,
        template_version_id: refreshedVersionId,
      }),
      expect.any(String),
    ),
  );
  expect(api.startGeneration).toHaveBeenCalledTimes(2);
});

test.each(["failed", "cancelled"])(
  "a %s conversion can be retried without replacing the retained revision",
  async (status) => {
    const { api } = setup({
      existingGeneration: { ...queuedGeneration, status },
    });
    expect(
      await screen.findByText(`Generation · DOCX · ${status}`),
    ).toBeVisible();
    const generate = screen.getByRole("button", { name: "Generate DOCX" });
    await waitFor(() => expect(generate).toBeEnabled());
    const retried = {
      ...queuedGeneration,
      id: "00000000-0000-4000-8000-000000000702",
    };
    api.startGeneration.mockResolvedValueOnce({ data: retried });
    api.generation.mockImplementation(async (_draftId: string, id: string) =>
      id === retried.id ? retried : { ...queuedGeneration, status },
    );
    fireEvent.click(generate);
    await waitFor(() => expect(api.startGeneration).toHaveBeenCalledOnce());
    expect(oldDownload()).toBeVisible();
    expect(api.publishGeneration).not.toHaveBeenCalled();
    expect(await screen.findByText("Generation · DOCX · queued")).toBeVisible();
  },
);

test("a native download finishing after a draft switch cannot save the first draft's bytes", async () => {
  const { api } = setup({ native: true, otherDraft: true });
  const download = await screen.findByRole("button", {
    name: "Download native revision",
  });
  showRetainedNativeDownload();
  const pending = deferred<Response>();
  api.download.mockReturnValueOnce(pending.promise);
  const originalCreate = Object.getOwnPropertyDescriptor(
    URL,
    "createObjectURL",
  );
  const createObjectURL = vi.fn(() => "blob:wrong-owner");
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  try {
    fireEvent.click(download);
    await waitFor(() =>
      expect(api.download).toHaveBeenCalledWith(
        draftId,
        retainedRevisionId,
        "download",
      ),
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Saved drafts" }), {
      target: { value: otherDraftId },
    });
    await screen.findByDisplayValue(otherDraft.content);
    await act(async () => {
      pending.resolve(
        new Response("office-byte-payload", {
          headers: { "content-type": docxType },
        }),
      );
      await pending.promise;
    });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(window.location.search).toBe(`?draft=${otherDraftId}`);
  } finally {
    if (originalCreate)
      Object.defineProperty(URL, "createObjectURL", originalCreate);
    else Reflect.deleteProperty(URL, "createObjectURL");
  }
});

test.each([
  [
    "wrong draft",
    otherDraftId,
    retainedRevisionId,
    `/api/v1/composer/drafts/${otherDraftId}/revisions/${retainedRevisionId}/artifacts/download`,
  ],
  [
    "wrong URL",
    draftId,
    retainedRevisionId,
    `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}/artifacts/download`,
  ],
] as const)(
  "a native download callback with %s cannot request another artifact",
  async (_description, activeDraftId, activeRevisionId, activeUrl) => {
    const { api } = setup({ native: true });
    const download = await screen.findByRole("button", {
      name: "Download native revision",
    });
    retainedNativeRevision = {
      draftId: activeDraftId,
      revisionId: activeRevisionId,
      format: "docx",
      downloadUrl: activeUrl,
    };
    fireEvent.click(download);
    expect(api.download).not.toHaveBeenCalledWith(
      activeDraftId,
      activeRevisionId,
      "download",
    );
    expect(screen.getByRole("heading", { name: "Composer" })).toBeVisible();
  },
);
