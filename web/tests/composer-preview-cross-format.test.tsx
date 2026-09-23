import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { ComposerPreview } from "../src/composer/preview/composer-preview";
import { revisionArtifactPath } from "../src/composer/preview/bytes";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => pdf);
vi.mock("pdfjs-dist/build/pdf.worker.mjs", () => ({}));

const draftId = "11111111-1111-1111-1111-111111111111";
const firstRevision = "22222222-2222-2222-2222-222222222222";
const nextRevision = "33333333-3333-3333-3333-333333333333";
const lastRevision = "44444444-4444-4444-4444-444444444444";
type Format = "docx" | "pptx" | "pdf";
type Active = {
  draftId: string;
  revisionId: string;
  format: Format;
  downloadUrl: string;
};

const mime: Record<Format, string> = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  pdf: "application/pdf",
};

function artifact(revisionId: string, kind: "preview" | "download") {
  return revisionArtifactPath(draftId, revisionId, kind);
}

function active(revisionId: string, format: Format): Active {
  return {
    draftId,
    revisionId,
    format,
    downloadUrl: artifact(revisionId, "download"),
  };
}

function preview(
  revisionId: string,
  format: Format,
  onActiveRevisionChange: (value: Active | null) => void,
) {
  return (
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionId}
      format={format}
      onActiveRevisionChange={onActiveRevisionChange}
    />
  );
}

function expectActive(revisionId: string, format: Format) {
  expect(
    screen.getByText(`${format.toUpperCase()} revision ${revisionId} preview`),
  ).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute("href", artifact(revisionId, "download"));
}

async function readyOffice(revisionId: string, format: "docx" | "pptx") {
  const frame = (await screen.findByTitle(
    `${format.toUpperCase()} revision ${revisionId} preview`,
  )) as HTMLIFrameElement;
  const frameWindow = frame.contentWindow!;
  const post = vi.spyOn(frameWindow, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frameWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
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
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: frameWindow,
      data: { ...request, type: "composer-preview-result", status: "ready" },
    }),
  );
  await waitFor(() => expect(frame).toHaveAttribute("aria-hidden", "false"));
  return { frame, frameWindow, request };
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,aGVsbG8=",
  );
  pdf.getDocument.mockImplementation(() => ({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({
          width: 200 * scale,
          height: 300 * scale,
        }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
      }),
    }),
    destroy: vi.fn(async () => undefined),
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const format: Format =
        url === artifact(firstRevision, "preview") ? "docx" : "pdf";
      return new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: { "Content-Type": mime[format] },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  pdf.getDocument.mockReset();
});

test("same-format replacement keeps A visible and bound until B renders", async () => {
  const onActiveRevisionChange = vi.fn<(value: Active | null) => void>();
  vi.mocked(fetch).mockImplementation(
    async () =>
      new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: { "Content-Type": mime.docx },
      }),
  );
  const { rerender } = render(
    preview(firstRevision, "docx", onActiveRevisionChange),
  );
  expect(onActiveRevisionChange).not.toHaveBeenCalledWith(
    active(firstRevision, "docx"),
  );
  const first = await readyOffice(firstRevision, "docx");
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      active(firstRevision, "docx"),
    ),
  );
  expectActive(firstRevision, "docx");

  rerender(preview(nextRevision, "docx", onActiveRevisionChange));
  await screen.findByTitle(`DOCX revision ${nextRevision} preview`);
  expect(screen.getByText(/Preparing revision/)).toBeVisible();
  expect(first.frame).toHaveAttribute("aria-hidden", "false");
  expectActive(firstRevision, "docx");
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
    active(firstRevision, "docx"),
  );

  await readyOffice(nextRevision, "docx");
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      active(nextRevision, "docx"),
    ),
  );
  expectActive(nextRevision, "docx");
  expect(
    screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
  ).toBeNull();
});

test("callback starts only after a usable render and clears committed identity on draft switch and unmount", async () => {
  const onActiveRevisionChange = vi.fn<(value: Active | null) => void>();
  vi.mocked(fetch).mockImplementation(
    async () =>
      new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: { "Content-Type": mime.docx },
      }),
  );
  const otherDraftId = "55555555-5555-5555-5555-555555555555";
  const view = render(preview(firstRevision, "docx", onActiveRevisionChange));
  await screen.findByTitle(`DOCX revision ${firstRevision} preview`);
  expect(onActiveRevisionChange).not.toHaveBeenCalled();
  await readyOffice(firstRevision, "docx");
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenCalledWith(
      active(firstRevision, "docx"),
    ),
  );
  view.rerender(
    <ComposerPreview
      draftId={otherDraftId}
      revisionId={lastRevision}
      format="docx"
      onActiveRevisionChange={onActiveRevisionChange}
    />,
  );
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(null),
  );
  await screen.findByTitle(`DOCX revision ${lastRevision} preview`);
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(null);
  await readyOffice(lastRevision, "docx");
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith({
      draftId: otherDraftId,
      revisionId: lastRevision,
      format: "docx",
      downloadUrl: revisionArtifactPath(otherDraftId, lastRevision, "download"),
    }),
  );
  view.unmount();
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(null);
});

test.each(["pdf", "pptx"] as const)(
  "DOCX to %s keeps the old preview, download, and callback until the new format is usable",
  async (nextFormat) => {
    const onActiveRevisionChange = vi.fn<(value: Active | null) => void>();
    const fetchMock = vi.mocked(fetch);
    let releasePdf: (() => void) | undefined;
    fetchMock.mockImplementation(async (url) => {
      if (
        nextFormat === "pdf" &&
        String(url) === artifact(nextRevision, "preview")
      )
        await new Promise<void>((resolve) => {
          releasePdf = resolve;
        });
      return new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: {
          "Content-Type":
            String(url) === artifact(firstRevision, "preview")
              ? mime.docx
              : mime[nextFormat],
        },
      });
    });
    const { rerender } = render(
      preview(firstRevision, "docx", onActiveRevisionChange),
    );
    const first = await readyOffice(firstRevision, "docx");
    expectActive(firstRevision, "docx");
    rerender(preview(nextRevision, nextFormat, onActiveRevisionChange));
    expect(await screen.findByText(/Preparing revision/)).toBeVisible();
    expect(first.frame).toHaveAttribute("aria-hidden", "false");
    expectActive(firstRevision, "docx");
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      active(firstRevision, "docx"),
    );
    if (nextFormat === "pdf") {
      await waitFor(() => expect(releasePdf).toBeDefined());
      releasePdf?.();
      await screen.findByText("Page 1 of 1");
    } else {
      await readyOffice(nextRevision, "pptx");
    }
    await waitFor(() =>
      expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
        active(nextRevision, nextFormat),
      ),
    );
    expectActive(nextRevision, nextFormat);
    expect(
      screen.queryByTitle(`DOCX revision ${firstRevision} preview`),
    ).toBeNull();
  },
);

test.each(["docx", "pptx"] as const)(
  "PDF to %s keeps the PDF page and download while the Office frame prepares",
  async (nextFormat) => {
    const onActiveRevisionChange = vi.fn<(value: Active | null) => void>();
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockImplementation(
      async (url) =>
        new Response(new Uint8Array([37, 80, 68, 70]), {
          headers: {
            "Content-Type":
              String(url) === artifact(firstRevision, "preview")
                ? mime.pdf
                : mime[nextFormat],
          },
        }),
    );
    const { rerender } = render(
      preview(firstRevision, "pdf", onActiveRevisionChange),
    );
    await screen.findByText("Page 1 of 1");
    await waitFor(() =>
      expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
        active(firstRevision, "pdf"),
      ),
    );
    rerender(preview(nextRevision, nextFormat, onActiveRevisionChange));
    await screen.findByTitle(
      `${nextFormat.toUpperCase()} revision ${nextRevision} preview`,
    );
    expect(screen.getByText(/Preparing revision/)).toBeVisible();
    expect(screen.getByText("Page 1 of 1")).toBeVisible();
    expectActive(firstRevision, "pdf");
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      active(firstRevision, "pdf"),
    );
    await readyOffice(nextRevision, nextFormat);
    await waitFor(() =>
      expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
        active(nextRevision, nextFormat),
      ),
    );
    expectActive(nextRevision, nextFormat);
    expect(screen.queryByText("Page 1 of 1")).toBeNull();
  },
);

test("failed and stale replacement results cannot displace the committed revision", async () => {
  const onActiveRevisionChange = vi.fn<(value: Active | null) => void>();
  const fetchMock = vi.mocked(fetch);
  fetchMock.mockImplementation(
    async (url) =>
      new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: {
          "Content-Type":
            String(url) === artifact(firstRevision, "preview")
              ? mime.docx
              : mime.pptx,
        },
      }),
  );
  const { rerender } = render(
    preview(firstRevision, "docx", onActiveRevisionChange),
  );
  await readyOffice(firstRevision, "docx");
  rerender(preview(nextRevision, "pptx", onActiveRevisionChange));
  const second = (await screen.findByTitle(
    `PPTX revision ${nextRevision} preview`,
  )) as HTMLIFrameElement;
  const secondWindow = second.contentWindow!;
  const post = vi.spyOn(secondWindow, "postMessage");
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: secondWindow,
      data: { type: "composer-preview-ready" },
    }),
  );
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const staleRequest = post.mock.calls.find(
    ([message]) => message.type === "composer-preview-render",
  )![0];
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: secondWindow,
      data: {
        ...staleRequest,
        type: "composer-preview-result",
        status: "error",
      },
    }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "cannot be shown safely",
  );
  expectActive(firstRevision, "docx");
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
    active(firstRevision, "docx"),
  );

  rerender(preview(lastRevision, "pptx", onActiveRevisionChange));
  await screen.findByTitle(`PPTX revision ${lastRevision} preview`);
  fireEvent(
    window,
    new MessageEvent("message", {
      origin: "null",
      source: secondWindow,
      data: {
        ...staleRequest,
        type: "composer-preview-result",
        status: "ready",
      },
    }),
  );
  expectActive(firstRevision, "docx");
  expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
    active(firstRevision, "docx"),
  );
  await readyOffice(lastRevision, "pptx");
  await waitFor(() =>
    expect(onActiveRevisionChange).toHaveBeenLastCalledWith(
      active(lastRevision, "pptx"),
    ),
  );
  expectActive(lastRevision, "pptx");
});
