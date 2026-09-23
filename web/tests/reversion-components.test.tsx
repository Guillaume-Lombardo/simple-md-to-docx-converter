import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach } from "vitest";
import { ApiError, type ApiTransport } from "../src/api/transport";
import { AuthController } from "../src/auth/controller";
import { AuthProvider } from "../src/auth/context";
import { ConversionController } from "../src/conversion/controller";
import { ConversionWorkspace } from "../src/conversion/workspace";
import { ReversionController } from "../src/reversion/controller";
import {
  ReversionWorkspace,
  saveReversionDownload,
} from "../src/reversion/workspace";
import {
  readReversionInputs,
  writeReversionInputs,
  type SavedReversionInputs,
} from "../src/conversion/persistence";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("../src/conversion/persistence", () => ({
  ownerStorageEpoch: vi.fn().mockReturnValue(0),
  resumeOwnerStorage: vi.fn(),
  readConversionInputs: vi.fn().mockResolvedValue(undefined),
  writeConversionInputs: vi.fn().mockResolvedValue(undefined),
  readReversionInputs: vi.fn().mockResolvedValue(undefined),
  writeReversionInputs: vi.fn().mockResolvedValue(undefined),
  clearConversionInputs: vi.fn().mockResolvedValue(undefined),
}));

afterEach(() => {
  vi.restoreAllMocks();
  vi.mocked(readReversionInputs).mockReset().mockResolvedValue(undefined);
  vi.mocked(writeReversionInputs).mockReset().mockResolvedValue(undefined);
});

const user = {
  active: true,
  effective_idle_minutes: 30,
  id: "00000000-0000-4000-8000-000000000001",
  password_change_required: false,
  role: "user" as const,
  username: "Alice",
};

const capabilities = {
  admission: {
    csv_policy: "bounded text",
    extension_is_hint: true,
    mismatch_policy: "reject",
    scanner_order: "scan first",
    undetected_policy: "reject",
  },
  execution: { hosted_fallback: false, local: true, ocr: false },
  extraction: {
    default_mode: "anydoc",
    include_images_default: true,
    include_notes_default: true,
    modes: ["anydoc", "slides", "marp"],
    structured_extensions: [".pptx"],
  },
  format_families: [
    {
      content_detection: "signature",
      detected_formats: ["docx"],
      extensions: [".docx", ".pdf"],
      family: "word" as const,
      selected_parser_format: null,
    },
    {
      content_detection: "signature",
      detected_formats: ["pptx"],
      extensions: [".pptx"],
      family: "powerpoint" as const,
      selected_parser_format: null,
    },
  ],
  maximum_upload_bytes: 1_000,
  pdf: {
    contract: "text extraction only",
    document_model_available: false,
    embedded_assets_available: false,
    image_preservation: false,
    mixed_or_image_only_pages: "reject as needs_ocr",
    warning: "Images are not preserved",
  },
  result_package_modes: ["markdown" as const],
  schema_version: 1,
};

const reversionJob = {
  attempt: 0,
  cancel_requested: false,
  component_versions: [] as Array<[string, string]>,
  correlation_id: "correlation",
  created_at: "2026-09-06T08:00:00Z",
  detected_format: "docx",
  error_code: null,
  error_message: null,
  expires_at: null,
  id: "00000000-0000-4000-8000-000000000201",
  owner_id: user.id,
  result_mode: "markdown" as const,
  result_size: 10,
  source_extension: ".docx",
  source_family: "word" as const,
  source_stem: "report",
  state: "succeeded" as const,
  step: "complete" as const,
  updated_at: "2026-09-06T08:00:00Z",
};

test.each(["Convert", "Revert"] as const)(
  "%s uploads and late failures never enter the other workspace",
  async (startingWorkspace) => {
    const failures: Array<(error: Error) => void> = [];
    const multipartWithMetadata = vi.fn(
      () => new Promise((_resolve, reject) => failures.push(reject)),
    );
    const api = {
      json: vi.fn(async (path: string) => {
        if (path === "/api/v1/conversion-options")
          return {
            conversion_upload_max_bytes: 1_000,
            resolved_template: null,
            selection_source: "pandoc_default",
            template_version_id: null,
          };
        if (path === "/api/v1/reversions/capabilities") return capabilities;
        return { items: [], limit: 10, offset: 0, total: 0 };
      }),
      multipartWithMetadata,
    } as unknown as ApiTransport;
    const auth = new AuthController({
      json: vi.fn().mockResolvedValue(user),
    } as unknown as ApiTransport);
    const forward = new ConversionController(
      api,
      undefined,
      () => "forward-key",
    );
    const reverse = new ReversionController(
      api,
      undefined,
      () => "reverse-key",
    );
    const streams = [
      {
        controller: forward,
        element: <ConversionWorkspace controller={forward} />,
        label: /Source file/,
        source: new File(["# Markdown"], "forward-only.md"),
        endpoint: "/api/v1/conversions",
        key: "forward-key",
      },
      {
        controller: reverse,
        element: <ReversionWorkspace controller={reverse} />,
        label: /Source document/,
        source: new File(["document"], "reverse-only.docx"),
        endpoint: "/api/v1/reversions",
        key: "reverse-key",
      },
    ];
    if (startingWorkspace === "Revert") streams.reverse();
    const [first, second] = streams;
    const view = render(
      <AuthProvider controller={auth}>{first!.element}</AuthProvider>,
    );
    fireEvent.change(await screen.findByLabelText(first!.label), {
      target: { files: [first!.source] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
    expect(multipartWithMetadata).toHaveBeenCalledOnce();

    view.rerender(
      <AuthProvider controller={auth}>{second!.element}</AuthProvider>,
    );
    const input = (await screen.findByLabelText(
      second!.label,
    )) as HTMLInputElement;
    expect(input.files).toHaveLength(0);
    expect(second!.controller.snapshot().source).toBeUndefined();
    expect(screen.queryByText(new RegExp(first!.source.name))).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(multipartWithMetadata).toHaveBeenCalledOnce();
    await act(async () => {
      failures[0]!(
        new ApiError(422, "INVALID_SOURCE", "First stream rejected."),
      );
    });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(second!.controller.snapshot().active).toBeUndefined();

    fireEvent.change(input, { target: { files: [second!.source] } });
    fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
    expect(multipartWithMetadata).toHaveBeenCalledTimes(2);
    for (const [index, stream] of streams.entries()) {
      const call = vi.mocked(api.multipartWithMetadata).mock.calls[index]!;
      expect(call[0]).toBe(stream.endpoint);
      expect(call[1].get("source")).toBe(stream.source);
      expect(call[3]?.idempotencyKey).toBe(stream.key);
    }
    await act(async () => {
      failures[1]!(
        new ApiError(422, "INVALID_SOURCE", "Second stream rejected."),
      );
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Second stream rejected.",
    );
    expect(first!.controller.snapshot().error).toBeUndefined();
  },
);

function renderWorkspace(reversionApi: Partial<ApiTransport>) {
  const auth = new AuthController({
    json: vi.fn().mockResolvedValue(user),
  } as unknown as ApiTransport);
  const reversion = new ReversionController(
    reversionApi as ApiTransport,
    () => auth.expire(),
    () => "key",
  );
  return {
    auth,
    reversion,
    ...render(
      <AuthProvider controller={auth}>
        <ReversionWorkspace controller={reversion} />
      </AuthProvider>,
    ),
  };
}

test("workspace uses authoritative capabilities for accessible controls and copy", async () => {
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  expect(
    await screen.findByRole("heading", { name: "Revert to Markdown" }),
  ).toBeVisible();
  expect(screen.getAllByText("Experimental")).not.toHaveLength(0);
  expect(
    screen.getByRole("link", { name: "2md, Experimental" }),
  ).toHaveAttribute("aria-current", "page");
  expect(screen.getByLabelText(/Source document/)).toHaveAttribute(
    "accept",
    ".docx,.pdf,.pptx",
  );
  expect(
    screen.getByText(/server currently accepts .docx, .pdf, .pptx/),
  ).toBeVisible();
  expect(screen.getByText(/Extensions are a selection hint/)).toBeVisible();
  expect(screen.getByText(/CPU-only, low-compute/)).toBeVisible();
  expect(screen.getByText(/OCR is not available/)).toBeVisible();
  expect(screen.getByText(/Images are not preserved/)).toBeVisible();
  expect(screen.getByText("No recent document conversions.")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    (screen.getByLabelText(/Source document/) as HTMLInputElement).files,
  ).toHaveLength(0);
  expect(
    screen.getByRole("heading", { name: "New conversion to Markdown" })
      .parentElement?.parentElement,
  ).toHaveClass("lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]");
});

test("drop, submission, status, and download form one browser workflow", async () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:markdown-result");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const multipartWithMetadata = vi.fn().mockResolvedValue({
    data: reversionJob,
    location: `/api/v1/reversions/${reversionJob.id}`,
    retryAfterSeconds: 1,
    status: 202,
  });
  const source = new File(["document"], "report.docx");
  renderWorkspace({
    download: vi.fn().mockResolvedValue(
      new Response("# report", {
        headers: {
          "Cache-Control": "private, no-store",
          "Content-Disposition": 'attachment; filename="report.md"',
          "Content-Type": "text/markdown; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      }),
    ),
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  const hint = await screen.findByText(/Choose or drop exactly one/);
  const dropZone = hint.closest("label")!;
  fireEvent.dragEnter(dropZone);
  expect(screen.getByText("Drop the document now.")).toBeVisible();
  fireEvent.dragLeave(dropZone);
  fireEvent.dragOver(dropZone);
  fireEvent.drop(dropZone, { dataTransfer: { files: [source] } });
  expect(screen.getByText("Change file")).toBeVisible();
  expect(dropZone.querySelector('input[type="file"]')).toHaveClass("sr-only");
  expect(screen.getByText(/Selected report.docx/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
  expect(
    await screen.findByText("Your Markdown result is ready."),
  ).toBeVisible();
  expect(
    (multipartWithMetadata.mock.calls[0]![1] as FormData).get("source"),
  ).toBe(source);
  expect(screen.getByRole("button", { name: "Download result" })).toHaveClass(
    "primary-button",
  );
  expect(
    screen.getByRole("button", { name: /report.docx · succeeded/ }),
  ).toHaveAttribute("title", reversionJob.id);
  fireEvent.click(screen.getByRole("button", { name: "Download result" }));
  await vi.waitFor(() => expect(anchorClick).toHaveBeenCalledOnce());
  expect(createObjectURL).toHaveBeenCalledOnce();
  await vi.waitFor(() =>
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:markdown-result"),
  );
});

test("PowerPoint controls are capability-driven and send the selected options", async () => {
  const multipartWithMetadata = vi.fn().mockResolvedValue({
    data: reversionJob,
    location: `/api/v1/reversions/${reversionJob.id}`,
    retryAfterSeconds: 1,
    status: 202,
  });
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce({
        ...capabilities,
        format_families: [
          {
            ...capabilities.format_families[0],
            extensions: [".pptx"],
          },
        ],
      })
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [new File(["deck"], "slides.pptx")] },
  });
  fireEvent.click(screen.getByLabelText("Marp Markdown"));
  fireEvent.click(screen.getByLabelText("Include presenter notes"));
  expect(
    screen.getByText(/Unsupported meaningful content is marked/),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Start conversion" }));
  await vi.waitFor(() => expect(multipartWithMetadata).toHaveBeenCalledOnce());
  const form = multipartWithMetadata.mock.calls[0]![1] as FormData;
  expect(form.get("extraction")).toBe("marp");
  expect(form.get("include_notes")).toBe("false");
  expect(form.get("include_images")).toBe("true");
});

test("PowerPoint controls omit unavailable modes and select the advertised default", async () => {
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce({
        ...capabilities,
        extraction: {
          ...capabilities.extraction,
          default_mode: "marp",
          modes: ["slides", "marp"],
        },
      })
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [new File(["deck"], "slides.pptx")] },
  });
  expect(screen.queryByLabelText("Standard document extraction")).toBeNull();
  expect(screen.getByLabelText("Marp Markdown")).toBeChecked();
  expect(screen.getByLabelText("Include presenter notes")).toBeChecked();
});

test("recent running jobs can be reopened and cancelled", async () => {
  const running = {
    ...reversionJob,
    result_mode: null,
    result_size: null,
    state: "running" as const,
    step: "converting" as const,
  };
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities)
    .mockResolvedValueOnce({ items: [running], limit: 10, offset: 0, total: 1 })
    .mockResolvedValueOnce(running);
  const cancel = vi.fn().mockResolvedValue({
    ...running,
    cancel_requested: true,
  });
  renderWorkspace({ cancel, json });
  fireEvent.click(
    await screen.findByRole("button", { name: "report.docx · running" }),
  );
  expect(await screen.findByText(/Converting the document/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Cancel conversion" }));
  expect(await screen.findByText(/Cancellation requested/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Download result" })).toBeNull();
});

test("unavailable capabilities fail closed and retry without leaking details", async () => {
  const json = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("private host"))
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 })
    .mockResolvedValueOnce(capabilities)
    .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  renderWorkspace({ json });
  expect(
    await screen.findByRole("heading", { name: "Revert is unavailable" }),
  ).toBeVisible();
  expect(screen.getByText(/Submission is disabled/)).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "The reverse-conversion service is unavailable",
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Try again or contact your administrator.",
  );
  expect(document.body).not.toHaveTextContent("private host");
  expect(screen.queryByRole("button", { name: "Start conversion" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(
    await screen.findByRole("heading", { name: "New conversion to Markdown" }),
  ).toBeVisible();
});

test("download saving defers object URL revocation", () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:deferred");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  const deferred: Array<() => void> = [];
  saveReversionDownload(
    { blob: new Blob(["result"]), filename: "result.md" },
    (callback) => deferred.push(callback),
  );
  expect(createObjectURL).toHaveBeenCalledOnce();
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).not.toHaveBeenCalled();
  deferred[0]!();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:deferred");
});

test("selected Office source explicitly creates a scanned Composer draft", async () => {
  push.mockReset();
  const draftId = "00000000-0000-4000-8000-000000000301";
  const multipartWithMetadata = vi.fn().mockResolvedValue({
    data: { id: draftId },
    location: `/api/v1/composer/drafts/${draftId}`,
    status: 201,
  });
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  const source = new File(["office bytes"], "report.docx");
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [source] },
  });
  expect(screen.getByText(/Office editing is not yet available/)).toBeVisible();
  await vi.waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Open selected source in Composer" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Open selected source in Composer" }),
  );
  await vi.waitFor(() =>
    expect(push).toHaveBeenCalledWith(`/composer?draft=${draftId}`),
  );
  expect(multipartWithMetadata).toHaveBeenCalledWith(
    "/api/v1/composer/drafts",
    expect.any(FormData),
    expect.anything(),
    expect.objectContaining({ csrf: true }),
  );
  expect(
    (multipartWithMetadata.mock.calls[0]![1] as FormData).get("source"),
  ).toBe(source);
});

test("completed Markdown result uses the owner-scoped reversion handoff", async () => {
  push.mockReset();
  const draftId = "00000000-0000-4000-8000-000000000302";
  const jsonWithMetadata = vi.fn().mockResolvedValue({
    data: { id: draftId },
    location: `/api/v1/composer/drafts/${draftId}`,
    status: 201,
  });
  const download = vi.fn();
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({
        items: [reversionJob],
        limit: 10,
        offset: 0,
        total: 1,
      })
      .mockResolvedValueOnce(reversionJob),
    jsonWithMetadata,
    download,
  });
  fireEvent.click(
    await screen.findByRole("button", { name: /report.docx · succeeded/ }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Open Markdown result in Composer" }),
  );
  await vi.waitFor(() =>
    expect(push).toHaveBeenCalledWith(`/composer?draft=${draftId}`),
  );
  expect(jsonWithMetadata).toHaveBeenCalledWith(
    `/api/v1/composer/drafts/from-reversion/${reversionJob.id}`,
    expect.anything(),
    expect.objectContaining({
      body: JSON.stringify({ title: null }),
      csrf: true,
      method: "POST",
    }),
  );
  expect(download).not.toHaveBeenCalled();
});

test("selected PPTX and extraction settings return after Revert remounts", async () => {
  let saved: SavedReversionInputs | undefined;
  vi.mocked(readReversionInputs).mockImplementation(async () => saved);
  vi.mocked(writeReversionInputs).mockImplementation(async (_owner, state) => {
    saved = { ...state };
  });
  const json = () =>
    vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const first = renderWorkspace({ json: json() });
  const source = new File(["slides"], "deck.pptx");
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [source] },
  });
  fireEvent.click(screen.getByLabelText("Marp Markdown"));
  await vi.waitFor(() =>
    expect(saved).toMatchObject({
      source,
      options: { extraction: "marp" },
    }),
  );
  first.unmount();
  renderWorkspace({ json: json() });
  expect(
    await screen.findByText("Selected deck.pptx (6 bytes)."),
  ).toBeVisible();
  expect(screen.getByLabelText("Marp Markdown")).toBeChecked();
});

test("unsupported Composer source remains selected and storage failure keeps it visible", async () => {
  const multipartWithMetadata = vi.fn();
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce({
        ...capabilities,
        format_families: [
          { ...capabilities.format_families[0], extensions: [".docm"] },
          capabilities.format_families[1],
        ],
      })
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
    multipartWithMetadata,
  });
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [new File(["macro"], "report.docm")] },
  });
  await vi.waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Open selected source in Composer" }),
    ).toBeEnabled(),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Open selected source in Composer" }),
  );
  expect(
    await screen.findByText(/Composer accepts selected DOCX/),
  ).toBeVisible();
  expect(screen.getByText("Selected report.docm (5 bytes).")).toBeVisible();
  expect(multipartWithMetadata).not.toHaveBeenCalled();
});

test("unsupported Composer format remains selected after leaving and returning to 2md", async () => {
  let saved: SavedReversionInputs | undefined;
  vi.mocked(readReversionInputs).mockImplementation(async () => saved);
  vi.mocked(writeReversionInputs).mockImplementation(async (_owner, state) => {
    saved = { ...state };
  });
  const available = {
    ...capabilities,
    format_families: [
      { ...capabilities.format_families[0], extensions: [".docm"] },
      capabilities.format_families[1],
    ],
  };
  const json = () =>
    vi
      .fn()
      .mockResolvedValueOnce(available)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 });
  const first = renderWorkspace({ json: json() });
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [new File(["macro"], "report.docm")] },
  });
  await vi.waitFor(() => expect(saved?.source?.name).toBe("report.docm"));
  expect(screen.getByRole("link", { name: "Composer" })).toHaveAttribute(
    "href",
    "/composer",
  );
  first.unmount();
  renderWorkspace({ json: json() });
  expect(
    await screen.findByText("Selected report.docm (5 bytes)."),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Open selected source in Composer" }),
  ).toBeEnabled();
});

test("browser quota failure does not discard Revert's in-memory source", async () => {
  vi.mocked(writeReversionInputs).mockRejectedValue(
    new DOMException("quota", "QuotaExceededError"),
  );
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  fireEvent.change(await screen.findByLabelText(/Source document/), {
    target: { files: [new File(["office"], "report.pdf")] },
  });
  expect(
    await screen.findByText(/could not be saved in this browser/),
  ).toBeVisible();
  expect(screen.getByText("Selected report.pdf (6 bytes).")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Open selected source in Composer" }),
  ).toBeDisabled();
});

test("failed Revert input read never autosaves defaults and retries without replacing a new file", async () => {
  let rejectRead!: (error: Error) => void;
  let resolveRetry!: (value: SavedReversionInputs) => void;
  vi.mocked(readReversionInputs)
    .mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectRead = reject;
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRetry = resolve;
        }),
    );
  renderWorkspace({
    json: vi
      .fn()
      .mockResolvedValueOnce(capabilities)
      .mockResolvedValueOnce({ items: [], limit: 10, offset: 0, total: 0 }),
  });
  const input = await screen.findByLabelText(/Source document/);
  await vi.waitFor(() => expect(readReversionInputs).toHaveBeenCalledOnce());
  const chosen = new File(["current"], "current.docx");
  fireEvent.change(input, { target: { files: [chosen] } });
  rejectRead(new Error("transient IndexedDB failure"));
  expect(
    await screen.findByText(/Saved browser inputs could not be loaded/),
  ).toBeVisible();
  expect(writeReversionInputs).not.toHaveBeenCalled();
  expect(screen.getByText("Selected current.docx (7 bytes).")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Open selected source in Composer" }),
  ).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Try loading saved inputs" }),
  );
  expect(
    screen.getByText(/Saved browser inputs could not be loaded/),
  ).toBeVisible();
  resolveRetry({ source: new File(["old"], "old.pptx") });
  await vi.waitFor(() => expect(writeReversionInputs).toHaveBeenCalled());
  expect(
    screen.queryByText(/Saved browser inputs could not be loaded/),
  ).toBeNull();
  expect(screen.getByText("Selected current.docx (7 bytes).")).toBeVisible();
  expect(vi.mocked(writeReversionInputs).mock.lastCall?.[1].source).toBe(
    chosen,
  );
});

test("a recent reversion opened during a deferred read wins over saved history", async () => {
  let resolveRead!: (value: SavedReversionInputs) => void;
  vi.mocked(readReversionInputs).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveRead = resolve;
      }),
  );
  const json = vi
    .fn()
    .mockResolvedValueOnce(capabilities)
    .mockResolvedValueOnce({
      items: [reversionJob],
      limit: 10,
      offset: 0,
      total: 1,
    })
    .mockResolvedValueOnce(reversionJob);
  const { reversion } = renderWorkspace({ json });
  const recent = await screen.findByRole("button", {
    name: /report.docx · succeeded/,
  });
  await vi.waitFor(() => expect(readReversionInputs).toHaveBeenCalledOnce());
  fireEvent.click(recent);
  await vi.waitFor(() =>
    expect(reversion.snapshot().active?.id).toBe(reversionJob.id),
  );
  resolveRead({
    source: new File(["old"], "old.pptx"),
    activeJobId: "00000000-0000-4000-8000-000000000999",
  });
  await vi.waitFor(() => expect(writeReversionInputs).toHaveBeenCalled());
  expect(reversion.snapshot().active?.id).toBe(reversionJob.id);
  expect(reversion.snapshot().source).toBeUndefined();
  expect(json).not.toHaveBeenCalledWith(
    "/api/v1/reversions/00000000-0000-4000-8000-000000000999",
    expect.anything(),
    expect.anything(),
  );
});
