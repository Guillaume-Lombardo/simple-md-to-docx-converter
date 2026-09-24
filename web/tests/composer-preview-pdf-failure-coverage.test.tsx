import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { revisionArtifactPath } from "../src/composer/preview/bytes";
import { ComposerPreview } from "../src/composer/preview/composer-preview";
import { PdfPreview } from "../src/composer/preview/pdf-preview";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => pdf);
vi.mock("pdfjs-dist/build/pdf.worker.mjs", () => ({}));

function response(status = 200) {
  return new Response(new Uint8Array([37, 80, 68, 70]), {
    status,
    headers: { "Content-Type": "application/pdf" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function documentTask(numPages = 3) {
  const page = {
    getViewport: vi.fn(({ scale }: { scale: number }) => ({
      width: 200 * scale,
      height: 300 * scale,
    })),
    render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
  };
  const getPage = vi.fn(async (number: number) => {
    void number;
    return page;
  });
  const destroy = vi.fn(async () => undefined);
  return {
    page,
    getPage,
    destroy,
    task: { promise: Promise.resolve({ numPages, getPage }), destroy },
  };
}

function preview(
  key: string,
  callbacks: {
    onReady?: (key: string) => void;
    onError?: (key: string, message: string) => void;
    onUnauthorized?: (key: string) => void;
  } = {},
) {
  return (
    <PdfPreview
      artifactUrl={`/pdf/${key}`}
      downloadUrl={`/pdf/${key}/download`}
      maxBytes={1024}
      revisionKey={key}
      zoom={1}
      {...callbacks}
    />
  );
}

beforeEach(() => {
  pdf.getDocument.mockReset();
  pdf.getDocument.mockReturnValue(documentTask().task);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response()),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,AA==",
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  pdf.getDocument.mockReset();
});

test("401 and 403 replacements keep the usable PDF until an authorized revision succeeds", async () => {
  const old = documentTask();
  const newest = documentTask();
  pdf.getDocument
    .mockReturnValueOnce(old.task)
    .mockReturnValueOnce(newest.task);
  const onReady = vi.fn();
  const onError = vi.fn();
  const onUnauthorized = vi.fn();
  const callbacks = { onReady, onError, onUnauthorized };
  const fetchMock = vi.mocked(fetch);
  const view = render(preview("A", callbacks));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));

  fetchMock.mockResolvedValueOnce(response(401));
  view.rerender(preview("B", callbacks));
  await waitFor(() =>
    expect(onError).toHaveBeenCalledWith("B", expect.any(String)),
  );
  expect(onUnauthorized).toHaveBeenCalledExactlyOnceWith("B");
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(old.destroy).not.toHaveBeenCalled();

  fetchMock.mockResolvedValueOnce(response(403));
  view.rerender(preview("C", callbacks));
  await waitFor(() =>
    expect(onError).toHaveBeenCalledWith("C", expect.any(String)),
  );
  expect(onUnauthorized).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(pdf.getDocument).toHaveBeenCalledTimes(1);

  view.rerender(preview("D", callbacks));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith("D"));
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A", "D"]);
  expect(old.destroy).toHaveBeenCalledOnce();
  expect(screen.queryByRole("alert")).toBeNull();
  view.unmount();
  expect(newest.destroy).toHaveBeenCalledOnce();
});

test("a late failed fetch for a discarded revision cannot report an error or replace a newer page", async () => {
  const pending = deferred<Response>();
  const fetchMock = vi.mocked(fetch);
  const onReady = vi.fn();
  const onError = vi.fn();
  const onUnauthorized = vi.fn();
  const callbacks = { onReady, onError, onUnauthorized };
  const view = render(preview("A", callbacks));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  fetchMock.mockReturnValueOnce(pending.promise);
  view.rerender(preview("B", callbacks));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  view.rerender(preview("C", callbacks));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith("C"));

  await act(async () => {
    pending.reject(new Error("Old private network error"));
    await pending.promise.catch(() => undefined);
  });
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A", "C"]);
  expect(onError).not.toHaveBeenCalled();
  expect(onUnauthorized).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  view.unmount();
});

test("a broken thumbnail encoder does not hide pages or prevent nearby thumbnail recovery", async () => {
  const toDataURL = vi.mocked(HTMLCanvasElement.prototype.toDataURL);
  toDataURL
    .mockImplementationOnce(() => {
      throw new Error("Thumbnail encoder failed");
    })
    .mockReturnValue("data:image/png;base64,AA==");
  const onReady = vi.fn();
  const onError = vi.fn();
  const view = render(preview("A", { onReady, onError }));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Show PDF page 2" }),
    ).toBeVisible(),
  );
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Show PDF page 2" }));
  expect(await screen.findByText("Page 2 of 3")).toBeVisible();
  expect(onError).not.toHaveBeenCalled();
  expect(screen.queryByRole("alert")).toBeNull();
  view.unmount();
});

test("a later page render failure is local to navigation and leaves the committed PDF usable", async () => {
  const loaded = documentTask();
  let rejectSecondPage = false;
  loaded.getPage.mockImplementation(async (number: number) => {
    if (number === 2 && rejectSecondPage)
      throw new Error("Page two cannot be rendered");
    return loaded.page;
  });
  pdf.getDocument.mockReturnValue(loaded.task);
  const onReady = vi.fn();
  const onError = vi.fn();
  const view = render(preview("A", { onReady, onError }));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  rejectSecondPage = true;
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Page two cannot be rendered",
  );
  expect(onError).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
  expect(await screen.findByText("Page 1 of 3")).toBeVisible();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  view.unmount();
  expect(loaded.destroy).toHaveBeenCalledOnce();
});

test("a non-Error PDF fetch failure reports a safe message, then a later revision becomes usable", async () => {
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockRejectedValueOnce("private transport detail");
  const onReady = vi.fn();
  const onError = vi.fn();
  const view = render(preview("A", { onReady, onError }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The PDF preview failed.",
  );
  expect(onError).toHaveBeenCalledExactlyOnceWith(
    "A",
    "The PDF preview failed.",
  );
  expect(pdf.getDocument).not.toHaveBeenCalled();

  view.rerender(preview("B", { onReady, onError }));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("B"));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  view.unmount();
});

test("thumbnail canvas loss leaves full pages navigable without advertising broken thumbnails", async () => {
  vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(
    function (this: HTMLCanvasElement) {
      return this.width < 50
        ? null
        : ({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D);
    },
  );
  const onReady = vi.fn();
  const view = render(preview("A", { onReady }));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  expect(screen.queryByRole("button", { name: /Show PDF page/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Next page" }));
  expect(await screen.findByText("Page 2 of 3")).toBeVisible();
  expect(screen.getByLabelText("PDF page 2")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  view.unmount();
});

test("an invalid replacement identity keeps the committed PDF and its exact download paired", async () => {
  const draftId = "11111111-1111-1111-1111-111111111111";
  const firstRevision = "22222222-2222-2222-2222-222222222222";
  const onActiveRevisionChange = vi.fn();
  const view = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="pdf"
      onActiveRevisionChange={onActiveRevisionChange}
    />,
  );
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ revisionId: firstRevision, format: "pdf" }),
    ),
  );
  const oldDownload = revisionArtifactPath(draftId, firstRevision, "download");
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute("href", oldDownload);
  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId="not-a-revision"
      format="pdf"
      onActiveRevisionChange={onActiveRevisionChange}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Invalid revision identity.",
  );
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute("href", oldDownload);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
    expect.objectContaining({ revisionId: firstRevision }),
  );
  view.unmount();
});

test("malformed Office frame messages cannot start a fetch, and a failed render request recovers on the next revision", async () => {
  const draftId = "11111111-1111-1111-1111-111111111111";
  const firstRevision = "22222222-2222-2222-2222-222222222222";
  const nextRevision = "33333333-3333-3333-3333-333333333333";
  const lastRevision = "44444444-4444-4444-4444-444444444444";
  const fetchMock = vi.mocked(fetch);
  const officeResponse = () =>
    new Response(new Uint8Array([80, 75]), {
      headers: {
        "Content-Type":
          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      },
    });
  fetchMock
    .mockRejectedValueOnce("private fetch detail")
    .mockResolvedValueOnce(officeResponse())
    .mockResolvedValueOnce(officeResponse());
  const view = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={firstRevision}
      format="docx"
    />,
  );
  const first = (await screen.findByTitle(
    `DOCX revision ${firstRevision} preview`,
  )) as HTMLIFrameElement;
  const send = (frame: HTMLIFrameElement, data: unknown) =>
    fireEvent(
      window,
      new MessageEvent("message", {
        origin: "null",
        source: frame.contentWindow,
        data,
      }),
    );
  send(first, null);
  send(first, "composer-preview-ready");
  send(first, 42);
  expect(fetchMock).not.toHaveBeenCalled();
  send(first, { type: "composer-preview-ready" });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "The preview failed.",
  );
  expect(
    screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
  ).toBeNull();

  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={nextRevision}
      format="docx"
    />,
  );
  const next = (await screen.findByTitle(
    `DOCX revision ${nextRevision} preview`,
  )) as HTMLIFrameElement;
  const post = vi.spyOn(next.contentWindow!, "postMessage");
  send(next, { type: "composer-preview-ready" });
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const request = post.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  send(next, { ...request, type: "composer-preview-result", status: "ready" });
  await waitFor(() =>
    expect(
      screen.getByRole("link", { name: "Download this revision" }),
    ).toHaveAttribute(
      "href",
      revisionArtifactPath(draftId, nextRevision, "download"),
    ),
  );
  expect(screen.queryByRole("alert")).toBeNull();

  send(next, { ...request, type: "composer-preview-position", position: 5 });
  send(next, { ...request, type: "composer-preview-position", position: NaN });
  send(next, { ...request, type: "composer-preview-position", position: "9" });
  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={lastRevision}
      format="docx"
    />,
  );
  const last = (await screen.findByTitle(
    `DOCX revision ${lastRevision} preview`,
  )) as HTMLIFrameElement;
  const lastPost = vi.spyOn(last.contentWindow!, "postMessage");
  send(last, { type: "composer-preview-ready" });
  await waitFor(() =>
    expect(
      lastPost.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  expect(
    lastPost.mock.calls.find(
      ([message]) => message.type === "composer-preview-render",
    )![0],
  ).toMatchObject({ position: 5, revisionId: lastRevision });
  view.unmount();
});
