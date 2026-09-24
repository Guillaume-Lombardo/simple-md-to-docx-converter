import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { PdfPreview } from "../src/composer/preview/pdf-preview";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => pdf);
vi.mock("pdfjs-dist/build/pdf.worker.mjs", () => ({}));

function response(mime = "application/pdf") {
  return new Response(new Uint8Array([37, 80, 68, 70]), {
    headers: { "Content-Type": mime },
  });
}

function page(
  renderPage: () => Promise<void> = async () => undefined,
  viewport: (scale: number) => { width: number; height: number } = (scale) => ({
    width: 200 * scale,
    height: 300 * scale,
  }),
) {
  return {
    getViewport: ({ scale }: { scale: number }) => viewport(scale),
    render: vi.fn(() => ({ promise: renderPage(), cancel: vi.fn() })),
  };
}

function documentWithPages(
  getPage: (number: number) => Promise<ReturnType<typeof page>> = async () =>
    page(),
  numPages = 3,
) {
  const destroy = vi.fn(async () => undefined);
  return {
    task: {
      promise: Promise.resolve({ numPages, getPage: vi.fn(getPage) }),
      destroy,
    },
    destroy,
  };
}

function preview(
  revisionKey: string,
  onReady = vi.fn(),
  onError = vi.fn(),
  zoom = 1,
) {
  return (
    <PdfPreview
      artifactUrl={`/preview/${revisionKey}`}
      downloadUrl={`/download/${revisionKey}`}
      maxBytes={128}
      revisionKey={revisionKey}
      zoom={zoom}
      onReady={onReady}
      onError={onError}
    />
  );
}

beforeEach(() => {
  pdf.getDocument.mockReturnValue(documentWithPages().task);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,aGVsbG8=",
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  pdf.getDocument.mockReset();
});

test("failed replacement keeps the usable PDF page and only reports the failed key", async () => {
  const onReady = vi.fn();
  const onError = vi.fn();
  const fetchMock = vi.mocked(fetch);
  const { rerender } = render(preview("A", onReady, onError));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();

  fetchMock.mockResolvedValueOnce(response("text/plain"));
  rerender(preview("B", onReady, onError));
  expect(await screen.findByRole("alert")).toHaveTextContent("file type");
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(onError).toHaveBeenCalledWith("B", expect.stringMatching(/file type/));
  expect(pdf.getDocument).toHaveBeenCalledTimes(1);
});

test("a replacement page that fails to render leaves the old PDF and task active", async () => {
  const onReady = vi.fn();
  const onError = vi.fn();
  const old = documentWithPages();
  const broken = documentWithPages(async () =>
    page(async () => {
      throw new Error("Replacement page failed");
    }),
  );
  pdf.getDocument
    .mockReturnValueOnce(old.task)
    .mockReturnValueOnce(broken.task);
  const { rerender, unmount } = render(preview("A", onReady, onError));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  rerender(preview("B", onReady, onError));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Replacement page failed",
  );
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A"]);
  expect(onError).toHaveBeenCalledWith("B", "Replacement page failed");
  expect(old.destroy).not.toHaveBeenCalled();
  unmount();
  expect(old.destroy).toHaveBeenCalled();
  expect(broken.destroy).toHaveBeenCalled();
});

test("a stale artifact response cannot commit after a newer revision becomes usable", async () => {
  const onReady = vi.fn();
  const onError = vi.fn();
  const fetchMock = vi.mocked(fetch);
  const { rerender } = render(preview("A", onReady, onError));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  let releaseB!: (value: Response) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        releaseB = resolve;
      }),
  );

  rerender(preview("B", onReady, onError));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/preview/B",
      expect.objectContaining({ cache: "no-store" }),
    ),
  );
  rerender(preview("C", onReady, onError));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith("C"));
  releaseB(response());
  await waitFor(() => expect(onReady).toHaveBeenCalledTimes(2));
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A", "C"]);
  expect(onError).not.toHaveBeenCalled();
  expect(pdf.getDocument).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
});

test("a failed first-page render never announces readiness and destroys its loading task", async () => {
  const onReady = vi.fn();
  const onError = vi.fn();
  const broken = documentWithPages(async () =>
    page(async () => {
      throw new Error("Private renderer detail");
    }),
  );
  pdf.getDocument.mockReturnValue(broken.task);
  const view = render(preview("A", onReady, onError));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Private renderer detail",
  );
  expect(onReady).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledWith("A", "Private renderer detail");
  expect(screen.queryByLabelText("PDF page 1")).toBeNull();
  view.unmount();
  expect(broken.destroy).toHaveBeenCalled();
});

test("oversized zoom and failed thumbnails leave the committed page usable", async () => {
  const onReady = vi.fn();
  const onError = vi.fn();
  const first = page(undefined, (scale) => ({
    width: scale > 1 ? 5_000 : 200,
    height: scale > 1 ? 5_000 : 300,
  }));
  const thumbnail = page(async () => {
    throw new Error("Thumbnail unavailable");
  });
  const loaded = documentWithPages(async (number) =>
    number === 1 ? first : thumbnail,
  );
  pdf.getDocument.mockReturnValue(loaded.task);
  const { rerender } = render(preview("A", onReady, onError));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Show PDF page 2" })).toBeNull();

  rerender(preview("A", onReady, onError, 2));
  expect(await screen.findByRole("alert")).toHaveTextContent("canvas limit");
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(onError).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Page 2 of 3")).toBeVisible();
});

test("thumbnail cache respects the configured byte cap without hiding full pages", async () => {
  const onReady = vi.fn();
  vi.mocked(HTMLCanvasElement.prototype.toDataURL).mockReturnValue(
    `data:image/png;base64,${"a".repeat(100)}`,
  );
  render(preview("A", onReady));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: "Show PDF page 1" })).toBeNull();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Page 2 of 3")).toBeVisible();
});
