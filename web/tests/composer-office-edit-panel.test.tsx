import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "../src/api/transport";
import type { OfficeEditApi } from "../src/composer/office-edit-api";
import { ComposerOfficeEditPanel } from "../src/composer/office-edit-panel";

const draftId = "00000000-0000-4000-8000-000000000101";
const revisionId = "00000000-0000-4000-8000-000000000201";
const model = {
  connectionId: "connection-1",
  endpoint: "https://llm.example/v1",
  model: "small",
  maxOutputTokens: 64,
};
const docxTargets = [
  {
    target: "word/document.xml#t0",
    paragraph: 0,
    in_table: false,
    text: "Title",
  },
  {
    target: "word/document.xml#t4",
    paragraph: 3,
    in_table: true,
    text: "Cell",
  },
];

function page(targets = docxTargets, total = targets.length) {
  return {
    revision_id: revisionId,
    download_sha256: "a".repeat(64),
    engine_version: 1,
    targets,
    total,
    limit: 200,
    offset: 0,
  };
}

function mount(
  api: Partial<Record<keyof OfficeEditApi, unknown>>,
  options: { format?: "docx" | "pptx"; withModel?: boolean } = {},
) {
  const props = {
    expire: vi.fn(),
    onProposalReady: vi.fn(),
    onPublished: vi.fn(),
  };
  render(
    <ComposerOfficeEditPanel
      api={api as unknown as OfficeEditApi}
      draftEtag='"draft-2"'
      draftId={draftId}
      format={options.format ?? "docx"}
      model={options.withModel === false ? null : model}
      revisionId={revisionId}
      {...props}
    />,
  );
  return props;
}

test("changed runs publish with their loaded text as the precondition", async () => {
  const api = {
    targets: vi.fn().mockResolvedValue(page()),
    edit: vi.fn().mockResolvedValue({
      data: { id: "revision-2", number: 2, draft_id: draftId, operation: "x" },
      status: 201,
    }),
  };
  const props = mount(api, { withModel: false });

  const title = await screen.findByLabelText("Paragraph 1: Title");
  expect(screen.getByLabelText("Paragraph 4 (table): Cell")).toBeVisible();
  const publish = screen.getByRole("button", {
    name: "Publish text edits (0)",
  });
  expect(publish).toBeDisabled();
  fireEvent.change(title, { target: { value: "New title" } });
  fireEvent.click(
    screen.getByRole("button", { name: "Publish text edits (1)" }),
  );

  await waitFor(() =>
    expect(props.onPublished).toHaveBeenCalledWith("revision-2"),
  );
  expect(api.edit).toHaveBeenCalledWith(
    draftId,
    revisionId,
    "docx",
    '"draft-2"',
    expect.any(String),
    [
      {
        target: "word/document.xml#t0",
        expected_text: "Title",
        text: "New title",
      },
    ],
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Text edits published as revision 2.",
  );
  expect(
    screen.getByText(/Select an available connection and model/),
  ).toBeVisible();
});

test("slides, notes, and further pages are labelled and loaded on request", async () => {
  const slide = {
    target: "ppt/slides/slide1.xml#t0",
    paragraph: 0,
    in_table: false,
    text: "Deck",
    slide: 1,
    notes: false,
  };
  const notes = {
    ...slide,
    target: "ppt/notesSlides/notesSlide1.xml#t0",
    text: "Speak",
    notes: true,
  };
  const api = {
    targets: vi
      .fn()
      .mockResolvedValueOnce(page([slide], 2))
      .mockResolvedValueOnce(page([notes], 2)),
  };
  mount(api, { format: "pptx" });

  expect(
    await screen.findByLabelText("Slide 1, paragraph 1: Deck"),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Load more text" }));
  expect(
    await screen.findByLabelText("Slide 1 notes, paragraph 1: Speak"),
  ).toBeVisible();
  expect(api.targets).toHaveBeenLastCalledWith(draftId, revisionId, "pptx", 1);
  expect(screen.queryByRole("button", { name: "Load more text" })).toBeNull();
  expect(screen.getByRole("heading")).toHaveTextContent(
    "Edit slide and notes text",
  );
});

test.each([
  [new ApiError(412, "STALE", "raw"), "The draft or document changed."],
  [
    new ApiError(422, "COMPOSER_DOCX_EDIT_TARGET_CHANGED", "raw"),
    "A text changed since it was loaded.",
  ],
  [
    new ApiError(413, "COMPOSER_DOCX_EDIT_LIMIT_EXCEEDED", "raw"),
    "configured editing limits",
  ],
  [
    new ApiError(422, "COMPOSER_DOCX_EDIT_UNSUPPORTED_TARGET", "raw"),
    "cannot be edited safely",
  ],
  [new ApiError(404, "GONE", "raw"), "no longer available"],
  [new ApiError(503, "DOWN", "raw"), "could not be completed"],
  [new ApiError(400, "OTHER", "Exact message"), "Exact message"],
  [new Error("network"), "could not be completed"],
])("edit failures are explained safely: %s", async (failure, message) => {
  const api = {
    targets: vi.fn().mockResolvedValue(page()),
    edit: vi.fn().mockRejectedValue(failure),
  };
  mount(api, { withModel: false });
  fireEvent.change(await screen.findByLabelText("Paragraph 1: Title"), {
    target: { value: "Changed" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Publish text edits (1)" }),
  );
  expect(await screen.findByText(new RegExp(message))).toBeVisible();
});

test("an expired session during loading signs the user out", async () => {
  const api = {
    targets: vi.fn().mockRejectedValue(new ApiError(401, "AUTH", "raw")),
  };
  const props = mount(api);
  expect(await screen.findByText(/Your session ended/)).toBeVisible();
  expect(props.expire).toHaveBeenCalled();
  expect(screen.getByText("This revision has no editable text.")).toBeVisible();
});

test("only shared text is previewed, then sent once and reported for review", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const api = {
      targets: vi.fn().mockResolvedValue(page()),
      previewStep: vi.fn().mockResolvedValue({
        format: "docx",
        base_revision_id: revisionId,
        base_sha256: "a".repeat(64),
        transmitted_content: "Formal.\n\nTitle only",
        targets: [{ target: "word/document.xml#t0", text: "Title" }],
        preview_digest: "d".repeat(64),
      }),
      startStep: vi.fn().mockResolvedValue({
        id: "step-1",
        status: "running",
        proposal_id: null,
        error_code: null,
      }),
      step: vi.fn().mockResolvedValue({
        id: "step-1",
        status: "completed",
        proposal_id: "proposal-1",
        error_code: null,
      }),
    };
    const props = mount(api);
    const share = await screen.findByLabelText("Share “Title” with the model");
    const previewButton = screen.getByRole("button", {
      name: "Preview model request",
    });
    expect(previewButton).toBeDisabled();
    fireEvent.click(share);
    fireEvent.change(screen.getByLabelText("Instruction for the model"), {
      target: { value: "  Formal.  " },
    });
    expect(
      screen.getByRole("button", { name: "Send shared text to the model" }),
    ).toBeDisabled();
    fireEvent.click(previewButton);
    expect(await screen.findByText(/Title only/)).toBeVisible();
    expect(api.previewStep).toHaveBeenCalledWith(
      draftId,
      revisionId,
      '"draft-2"',
      {
        connection_id: "connection-1",
        approved_endpoint: "https://llm.example/v1",
        approved_model: "small",
        instruction: "Formal.",
        targets: ["word/document.xml#t0"],
        max_output_tokens: 64,
      },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Send shared text to the model" }),
    );
    await vi.advanceTimersByTimeAsync(600);
    await waitFor(() => expect(props.onProposalReady).toHaveBeenCalled());
    expect(api.startStep.mock.calls[0]![4]).toMatchObject({
      preview_digest: "d".repeat(64),
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "ready for review in the proposals list",
    );
    fireEvent.click(share);
    expect(screen.queryByText(/Title only/)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

test.each([
  ["provider_invalid", "was not a valid text edit"],
  ["provider_unavailable", "temporarily unavailable"],
  ["capacity_exhausted", "temporarily unavailable"],
  ["execution_failed", "did not produce a suggestion"],
])("a failed model step explains %s", async (code, message) => {
  const api = {
    targets: vi.fn().mockResolvedValue(page()),
    previewStep: vi.fn().mockResolvedValue({
      format: "docx",
      base_revision_id: revisionId,
      base_sha256: "a".repeat(64),
      transmitted_content: "Preview",
      targets: [],
      preview_digest: "d".repeat(64),
    }),
    startStep: vi.fn().mockResolvedValue({
      id: "step-1",
      status: "failed",
      proposal_id: null,
      error_code: code,
    }),
  };
  const props = mount(api);
  fireEvent.click(await screen.findByLabelText("Share “Title” with the model"));
  fireEvent.change(screen.getByLabelText("Instruction for the model"), {
    target: { value: "Formal." },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Preview model request" }),
  );
  await screen.findByText("Preview");
  fireEvent.click(
    screen.getByRole("button", { name: "Send shared text to the model" }),
  );
  expect(await screen.findByText(new RegExp(message))).toBeVisible();
  expect(props.onProposalReady).not.toHaveBeenCalled();
});

test("a step still running after the polling budget is left for later review", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const running = {
      id: "step-1",
      status: "running",
      proposal_id: null,
      error_code: null,
    };
    const api = {
      targets: vi.fn().mockResolvedValue(page()),
      previewStep: vi.fn().mockResolvedValue({
        format: "docx",
        base_revision_id: revisionId,
        base_sha256: "a".repeat(64),
        transmitted_content: "Preview",
        targets: [],
        preview_digest: "d".repeat(64),
      }),
      startStep: vi.fn().mockResolvedValue(running),
      step: vi.fn().mockResolvedValue(running),
    };
    mount(api);
    fireEvent.click(
      await screen.findByLabelText("Share “Title” with the model"),
    );
    fireEvent.change(screen.getByLabelText("Instruction for the model"), {
      target: { value: "Formal." },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Preview model request" }),
    );
    await screen.findByText("Preview");
    fireEvent.click(
      screen.getByRole("button", { name: "Send shared text to the model" }),
    );
    await vi.advanceTimersByTimeAsync(500 * 121);
    expect(
      await screen.findByText(/still running\. Refresh the draft later/),
    ).toBeVisible();
    expect(api.step).toHaveBeenCalledTimes(120);
  } finally {
    vi.useRealTimers();
  }
});
