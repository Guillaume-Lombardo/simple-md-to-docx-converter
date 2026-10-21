import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApiError, type ApiTransport } from "../src/api/transport";
import {
  imageBase64,
  OfficeStructureApi,
} from "../src/composer/office-structure-api";
import {
  ComposerOfficeStructurePanel,
  slideMoves,
} from "../src/composer/office-structure-panel";

const draftId = "00000000-0000-4000-8000-000000000101";
const revisionId = "00000000-0000-4000-8000-000000000201";
const imageTarget = "word/media/image1.png";

function structure(overrides: Record<string, unknown> = {}) {
  return {
    format: "docx",
    revision_id: revisionId,
    download_sha256: "a".repeat(64),
    engine_version: 1,
    paragraphs: [
      {
        target: "word/document.xml#p0",
        paragraph: 0,
        in_table: false,
        style: null,
        text: "Title",
      },
      {
        target: "word/document.xml#p1",
        paragraph: 1,
        in_table: true,
        style: "Normal",
        text: "",
      },
    ],
    paragraph_styles: [
      { style_id: "Normal", name: "Normal" },
      { style_id: "Heading1", name: "heading 1" },
    ],
    slides: [],
    images: [
      {
        target: imageTarget,
        media_type: "image/png",
        sha256: "b".repeat(64),
        size: 10,
        used_by: ["word/document.xml"],
      },
    ],
    ...overrides,
  };
}

const deck = {
  format: "pptx",
  paragraphs: [],
  images: [],
  slides: [
    { slide_id: 256, position: 1, title: "Opening" },
    { slide_id: 257, position: 2, title: "Details" },
    { slide_id: 258, position: 3, title: "" },
  ],
};

function mount(api: Record<string, unknown>, format: "docx" | "pptx" = "docx") {
  const props = { expire: vi.fn(), onPublished: vi.fn() };
  render(
    <ComposerOfficeStructurePanel
      api={api as unknown as OfficeStructureApi}
      draftEtag='"draft-2"'
      draftId={draftId}
      format={format}
      revisionId={revisionId}
      {...props}
    />,
  );
  return props;
}

const published = {
  data: { id: "revision-2", number: 2, draft_id: draftId, operation: "x" },
  status: 201,
};

test("structure requests use exact revision routes, CSRF, and keys", async () => {
  const transport = {
    json: vi.fn().mockResolvedValue({}),
    jsonWithMetadata: vi.fn().mockResolvedValue({ data: {} }),
  };
  const api = new OfficeStructureApi(transport as unknown as ApiTransport);
  const base = `/api/v1/composer/drafts/${draftId}/revisions/${revisionId}`;
  const operations = [
    {
      op: "move_slide" as const,
      slide_id: 2,
      expected_position: 2,
      position: 1,
    },
  ];

  await api.structure(draftId, revisionId, "pptx");
  await api.edit(draftId, revisionId, "pptx", '"draft-2"', "key-1", operations);

  expect(transport.json.mock.calls[0]![0]).toBe(`${base}/pptx-structure`);
  expect(transport.jsonWithMetadata.mock.calls[0]![0]).toBe(
    `${base}/pptx-structure-edits`,
  );
  expect(transport.jsonWithMetadata.mock.calls[0]![2]).toMatchObject({
    body: JSON.stringify({ operations }),
    csrf: true,
    etag: '"draft-2"',
    idempotencyKey: "key-1",
    method: "POST",
  });
  expect(await imageBase64(new Blob([new Uint8Array([0, 255, 1])]))).toBe(
    "AP8B",
  );
});

test("slide moves place each slide once and skip slides already in place", () => {
  expect(slideMoves([1, 2, 3], [1, 2, 3])).toEqual([]);
  expect(slideMoves([1, 2, 3], [3, 1, 2])).toEqual([
    { op: "move_slide", slide_id: 3, expected_position: 3, position: 1 },
  ]);
  expect(slideMoves([1, 2, 3, 4], [2, 4, 1, 3])).toEqual([
    { op: "move_slide", slide_id: 2, expected_position: 2, position: 1 },
    { op: "move_slide", slide_id: 4, expected_position: 4, position: 2 },
  ]);
});

test("paragraph styles and a replacement image publish with their loaded values", async () => {
  const api = {
    structure: vi.fn().mockResolvedValue(structure()),
    edit: vi.fn().mockResolvedValue(published),
  };
  const props = mount(api);

  const title = await screen.findByLabelText("Style of paragraph 1: Title");
  expect(screen.getByText("Paragraph 2 (table): (empty)")).toBeVisible();
  const publish = screen.getByRole("button", {
    name: "Publish structure edits (0)",
  });
  expect(publish).toBeDisabled();
  fireEvent.change(title, { target: { value: "Heading1" } });
  const replacement = screen.getByLabelText(`Replacement for ${imageTarget}`);
  const file = new File([new Uint8Array([1, 2, 3])], "logo.png", {
    type: "image/png",
  });
  fireEvent.change(replacement, { target: { files: [file] } });
  fireEvent.change(replacement, { target: { files: [] } });
  fireEvent.change(replacement, { target: { files: [file] } });
  fireEvent.click(
    screen.getByRole("button", { name: "Publish structure edits (2)" }),
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
        op: "set_paragraph_style",
        target: "word/document.xml#p0",
        expected_style: null,
        style: "Heading1",
      },
      {
        op: "replace_image",
        target: imageTarget,
        expected_sha256: "b".repeat(64),
        filename: "logo.png",
        content_base64: "AQID",
      },
    ],
  );
});

test("reordered slides publish the minimal sequential moves", async () => {
  const api = {
    structure: vi.fn().mockResolvedValue(deck),
    edit: vi.fn().mockResolvedValue(published),
  };
  const props = mount(api, "pptx");

  const order = await screen.findByRole("list", { name: "Slide order" });
  expect(
    screen.getByRole("button", { name: "Move “Opening” up" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("button", { name: "Move “Slide 258” down" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Move “Slide 258” up" }));
  fireEvent.click(screen.getByRole("button", { name: "Move “Opening” down" }));
  expect(order).toHaveTextContent(
    "1. Slide 258UpDown2. OpeningUpDown3. DetailsUpDown",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Publish structure edits (1)" }),
  );

  await waitFor(() => expect(props.onPublished).toHaveBeenCalled());
  expect(api.edit.mock.calls[0]![5]).toEqual([
    { op: "move_slide", slide_id: 258, expected_position: 3, position: 1 },
  ]);
});

test("long documents reveal paragraphs in pages and empty revisions say so", async () => {
  const many = Array.from({ length: 60 }, (_, index) => ({
    target: `word/document.xml#p${index}`,
    paragraph: index,
    in_table: false,
    style: "Normal",
    text: `Line ${index}`,
  }));
  mount({
    structure: vi
      .fn()
      .mockResolvedValue(structure({ paragraphs: many, images: [] })),
  });
  await screen.findByLabelText("Style of paragraph 50: Line 49");
  expect(screen.queryByLabelText("Style of paragraph 51: Line 50")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show more paragraphs" }));
  expect(screen.getByLabelText("Style of paragraph 60: Line 59")).toBeVisible();
});

test("an empty revision has nothing to edit", async () => {
  mount({
    structure: vi
      .fn()
      .mockResolvedValue(structure({ paragraphs: [], images: [] })),
  });
  expect(
    await screen.findByText("This revision has no editable structure."),
  ).toBeVisible();
});

test.each([
  [new ApiError(404, "COMPOSER_NOT_FOUND", "x"), "no longer available"],
  [new ApiError(412, "COMPOSER_CHANGED", "x"), "draft or document changed"],
  [
    new ApiError(422, "COMPOSER_DOCX_EDIT_TARGET_CHANGED", "x"),
    "changed since it was loaded",
  ],
  [
    new ApiError(422, "COMPOSER_DOCX_EDIT_INVALID_IMAGE", "x"),
    "same format as the original",
  ],
  [
    new ApiError(422, "UPLOAD_MALWARE_DETECTED", "x"),
    "rejected by the malware scanner",
  ],
  [new ApiError(413, "COMPOSER_REQUEST_TOO_LARGE", "x"), "editing limits"],
  [
    new ApiError(422, "COMPOSER_DOCX_EDIT_LIMIT_EXCEEDED", "x"),
    "editing limits",
  ],
  [new ApiError(422, "COMPOSER_DOCX_EDIT_UNKNOWN_STYLE", "x"), "edited safely"],
  [new ApiError(503, "UNAVAILABLE", "x"), "Try again"],
  [new ApiError(400, "OTHER", "Explained."), "Explained."],
  [new Error("private"), "Try again"],
])("publication failure %# is explained", async (failure, message) => {
  const api = {
    structure: vi.fn().mockResolvedValue(structure()),
    edit: vi.fn().mockRejectedValue(failure),
  };
  const props = mount(api);
  fireEvent.change(
    await screen.findByLabelText("Style of paragraph 1: Title"),
    {
      target: { value: "Heading1" },
    },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Publish structure edits (1)" }),
  );
  expect(await screen.findByText(new RegExp(message))).toBeVisible();
  expect(props.onPublished).not.toHaveBeenCalled();
});

test("loading failures and ended sessions are reported", async () => {
  const props = mount({
    structure: vi.fn().mockRejectedValue(new ApiError(401, "AUTH", "Ended.")),
  });
  expect(
    await screen.findByText("Your session ended. Please sign in again."),
  ).toBeVisible();
  expect(props.expire).toHaveBeenCalledOnce();
});

test("an unmounted panel ignores late structure and edit results", async () => {
  let resolve: (value: unknown) => void = () => undefined;
  const { unmount } = render(
    <ComposerOfficeStructurePanel
      api={
        {
          structure: vi.fn(
            () =>
              new Promise((done) => {
                resolve = done;
              }),
          ),
        } as unknown as OfficeStructureApi
      }
      draftEtag='"draft-2"'
      draftId={draftId}
      expire={vi.fn()}
      format="docx"
      onPublished={vi.fn()}
      revisionId={revisionId}
    />,
  );
  expect(screen.getByText("Loading document structure…")).toBeVisible();
  unmount();
  resolve(structure());
  await Promise.resolve();
});
