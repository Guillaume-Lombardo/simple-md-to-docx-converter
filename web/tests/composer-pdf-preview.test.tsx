import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PdfPreview } from "../src/composer/preview/pdf-preview";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => pdf);
vi.mock("pdfjs-dist/build/pdf.worker.mjs", () => ({}));

function pdfResponse(bytes = new Uint8Array([37, 80, 68, 70])) {
  return new Response(new Uint8Array(bytes), {
    headers: { "Content-Type": "application/pdf" },
  });
}

describe("PDF revision preview", () => {
  let getPage: ReturnType<typeof vi.fn>;
  let getViewport: ReturnType<typeof vi.fn>;
  let renderPage: ReturnType<typeof vi.fn>;
  let destroy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    getViewport = vi.fn(({ scale }: { scale: number }) => ({
      width: 200 * scale,
      height: 300 * scale,
      scale,
    }));
    renderPage = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
    getPage = vi.fn(async () => ({ getViewport, render: renderPage }));
    destroy = vi.fn(async () => undefined);
    pdf.getDocument.mockReturnValue({
      promise: Promise.resolve({ numPages: 3, getPage }),
      destroy,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => pdfResponse()),
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

  it("loads the authorized PDF, renders one page and nearby thumbnails, and navigates", async () => {
    const { rerender, unmount } = render(
      <PdfPreview
        artifactUrl="/revision/preview"
        downloadUrl="/revision/download"
        maxBytes={1024}
        revisionKey="rev-1"
        zoom={1}
      />,
    );
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByLabelText("PDF page 1")).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Show PDF page 2" }),
      ).toBeInTheDocument(),
    );
    expect(fetch).toHaveBeenCalledWith(
      "/revision/preview",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByText("Page 2 of 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show PDF page 3" }));
    expect(await screen.findByText("Page 3 of 3")).toBeInTheDocument();
    rerender(
      <PdfPreview
        artifactUrl="/revision/preview"
        downloadUrl="/revision/download"
        maxBytes={1024}
        revisionKey="rev-1"
        zoom={2}
      />,
    );
    await waitFor(() => expect(getViewport).toHaveBeenCalledWith({ scale: 2 }));
    unmount();
    expect(destroy).toHaveBeenCalled();
  });

  it("reports an oversized page and drops thumbnails over the artifact budget", async () => {
    getViewport.mockImplementation(({ scale }: { scale: number }) => ({
      width: scale === 1 ? 5_000 : 200 * scale,
      height: scale === 1 ? 5_000 : 300 * scale,
    }));
    render(
      <PdfPreview
        artifactUrl="/revision/preview"
        downloadUrl="/revision/download"
        maxBytes={64}
        revisionKey="rev-2"
        zoom={1}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("canvas limit");
    expect(
      screen.queryByRole("button", { name: "Show PDF page 1" }),
    ).toBeNull();
  });

  it("keeps the previous page visible when a replacement artifact fails validation", async () => {
    const fetchMock = vi.mocked(fetch);
    const onReady = vi.fn();
    const { rerender } = render(
      <PdfPreview
        artifactUrl="/revision/first"
        downloadUrl="/revision/first-download"
        maxBytes={1024}
        revisionKey="first"
        zoom={1}
        onReady={onReady}
      />,
    );
    expect(await screen.findByText("Page 1 of 3")).toBeInTheDocument();
    expect(onReady).toHaveBeenCalledWith("first");
    fetchMock.mockResolvedValueOnce(
      new Response("wrong", { headers: { "Content-Type": "text/plain" } }),
    );
    rerender(
      <PdfPreview
        artifactUrl="/revision/second"
        downloadUrl="/revision/second-download"
        maxBytes={1024}
        revisionKey="second"
        zoom={1}
        onReady={onReady}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("file type");
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(pdf.getDocument).toHaveBeenCalledTimes(1);
  });

  it("reports only the current PDF preview's HTTP 401", async () => {
    const onUnauthorized = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("expired", { status: 401 }))
        .mockResolvedValueOnce(new Response("forbidden", { status: 403 })),
    );
    const { rerender } = render(
      <PdfPreview
        artifactUrl="/revision/first"
        downloadUrl="/revision/first-download"
        maxBytes={1024}
        revisionKey="first"
        zoom={1}
        onUnauthorized={onUnauthorized}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledWith("first");
    rerender(
      <PdfPreview
        artifactUrl="/revision/second"
        downloadUrl="/revision/second-download"
        maxBytes={1024}
        revisionKey="second"
        zoom={1}
        onUnauthorized={onUnauthorized}
      />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale PDF 401 after the selected revision changes", async () => {
    const onUnauthorized = vi.fn();
    const finishFetch: Array<(response: Response) => void> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            finishFetch.push(resolve);
          }),
      ),
    );
    const { rerender } = render(
      <PdfPreview
        artifactUrl="/revision/first"
        downloadUrl="/revision/first-download"
        maxBytes={1024}
        revisionKey="first"
        zoom={1}
        onUnauthorized={onUnauthorized}
      />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    rerender(
      <PdfPreview
        artifactUrl="/revision/second"
        downloadUrl="/revision/second-download"
        maxBytes={1024}
        revisionKey="second"
        zoom={1}
        onUnauthorized={onUnauthorized}
      />,
    );
    await act(async () => {
      finishFetch[0]!(new Response("expired", { status: 401 }));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
