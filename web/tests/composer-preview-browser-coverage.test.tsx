import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ComposerPreview } from "../src/composer/preview/composer-preview";
import { revisionArtifactPath } from "../src/composer/preview/bytes";
import { normalizeDocxLists } from "../src/composer/preview/docx-lists";
import { PdfPreview } from "../src/composer/preview/pdf-preview";

const pdf = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock("pdfjs-dist", () => pdf);
vi.mock("pdfjs-dist/build/pdf.worker.mjs", () => ({}));

const wordNs = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const draftId = "11111111-1111-1111-1111-111111111111";
const revisionA = "22222222-2222-2222-2222-222222222222";
const revisionB = "33333333-3333-3333-3333-333333333333";
const revisionC = "44444444-4444-4444-4444-444444444444";
const officeMime =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function numbering(format: string, label: string, start = 1): string {
  return `<w:numbering xmlns:w="${wordNs}">
    <w:abstractNum w:abstractNumId="1">
      <w:lvl w:ilvl="0"><w:start w:val="${start}"/><w:numFmt w:val="${format}"/><w:lvlText w:val="${label}"/></w:lvl>
    </w:abstractNum>
    <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
  </w:numbering>`;
}

function listRoot(count = 2): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = Array.from(
    { length: count },
    (_, index) => `<p class="docx-num-1-0">Item ${index + 1}</p>`,
  ).join("");
  document.body.append(root);
  return root;
}

function labels(root: HTMLElement): Array<string | undefined> {
  return [...root.querySelectorAll("p")].map(
    (paragraph) => paragraph.dataset.previewListLabel,
  );
}

function officeResponse(): Response {
  return new Response(new Uint8Array([1, 2, 3]), {
    headers: { "Content-Type": officeMime },
  });
}

function sendFrame(
  frame: HTMLIFrameElement,
  data: Record<string, unknown>,
  origin = "null",
): void {
  fireEvent(
    window,
    new MessageEvent("message", {
      source: frame.contentWindow,
      origin,
      data,
    }),
  );
}

async function renderOffice(revisionId: string) {
  const frame = (await screen.findByTitle(
    `DOCX revision ${revisionId} preview`,
  )) as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  sendFrame(frame, { type: "composer-preview-ready" });
  await waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-render",
      ),
    ).toBe(true),
  );
  const message = post.mock.calls.find(
    ([value]) => value.type === "composer-preview-render",
  )![0];
  return { frame, post, message };
}

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  pdf.getDocument.mockReset();
});

test("Word labels preserve alphabetic and Roman numbering across rollover", () => {
  const upper = listRoot(3);
  expect(normalizeDocxLists(upper, numbering("upperLetter", "%1.", 26))).toBe(
    3,
  );
  expect(labels(upper)).toEqual(["Z.", "AA.", "AB."]);

  const lower = listRoot(2);
  expect(normalizeDocxLists(lower, numbering("lowerRoman", "%1.", 4))).toBe(2);
  expect(labels(lower)).toEqual(["iv.", "v."]);
  expect(lower.querySelector("style")?.textContent).toContain(
    "attr(data-preview-list-label)",
  );
});

test("Word numbering refuses unresolved references and excessive counter values", () => {
  const missingParent = listRoot(1);
  expect(() =>
    normalizeDocxLists(missingParent, numbering("decimal", "%2.")),
  ).toThrow("unsupported numbering");
  expect(labels(missingParent)).toEqual([undefined]);

  const overRomanRange = listRoot(1);
  expect(() =>
    normalizeDocxLists(overRomanRange, numbering("upperRoman", "%1.", 4000)),
  ).toThrow("unsupported numbering");

  const overCounterLimit = listRoot(1);
  expect(() =>
    normalizeDocxLists(
      overCounterLimit,
      numbering("decimal", "%1.", 1_000_000),
    ),
  ).toThrow("unsupported numbering");
});

test("Word numbering rejects unsafe labels and hostile XML before adding styles", () => {
  const root = listRoot(1);
  for (const xml of [
    "<!DOCTYPE w:numbering><w:numbering/>",
    "<!ENTITY x SYSTEM 'file:///private'>",
  ]) {
    expect(() => normalizeDocxLists(root, xml)).toThrow(
      "unsupported numbering XML",
    );
  }
  for (const label of ["&#xA;", "x".repeat(65)]) {
    expect(() => normalizeDocxLists(root, numbering("bullet", label))).toThrow(
      "unsupported list label",
    );
  }
  expect(root.querySelector("style")).toBeNull();
  expect(labels(root)).toEqual([undefined]);
});

test("Word bullets have safe static glyphs and a 'none' level adds no label", () => {
  const bullet = listRoot(1);
  expect(normalizeDocxLists(bullet, numbering("bullet", "&#xF0A7;"))).toBe(1);
  expect(labels(bullet)).toEqual(["▪"]);

  const noNumber = listRoot(1);
  expect(normalizeDocxLists(noNumber, numbering("none", "unused"))).toBe(0);
  expect(labels(noNumber)).toEqual([undefined]);
  expect(noNumber.querySelector("style")).toBeNull();
});

test("Word numbering ignores unrelated paragraphs but refuses missing styles for numbered ones", () => {
  const root = listRoot(1);
  root.querySelector("p")!.className = "ordinary-paragraph";
  expect(normalizeDocxLists(root, numbering("decimal", "%1."))).toBe(0);
  expect(root.querySelector("style")).toBeNull();

  root.querySelector("p")!.className = "docx-num-1-9";
  expect(() => normalizeDocxLists(root, numbering("decimal", "%1."))).toThrow(
    "unsupported numbering",
  );
  root.querySelector("p")!.className = "docx-num-2-0";
  expect(() => normalizeDocxLists(root, numbering("decimal", "%1."))).toThrow(
    "unsupported numbering",
  );
});

test("Word list normalization refuses formatting that would invent a missing parent counter", () => {
  const xml = `<w:numbering xmlns:w="${wordNs}">
    <w:abstractNum w:abstractNumId="1">
      <w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
      <w:lvl w:ilvl="1"><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="%1.%2."/></w:lvl>
    </w:abstractNum>
    <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
  </w:numbering>`;
  const root = listRoot(1);
  root.querySelector("p")!.className = "docx-num-1-1";
  expect(() => normalizeDocxLists(root, xml)).toThrow("unsupported numbering");
  expect(labels(root)).toEqual([undefined]);
  expect(root.querySelector("style")).toBeNull();
});

test("late Office bytes cannot render an obsolete frame or replace the selected revision", async () => {
  const fetchMock = vi.fn(async () => officeResponse());
  vi.stubGlobal("fetch", fetchMock);
  const active = vi.fn();
  const view = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionA}
      format="docx"
      onActiveRevisionChange={active}
    />,
  );
  const first = await renderOffice(revisionA);
  sendFrame(first.frame, {
    ...first.message,
    type: "composer-preview-result",
    status: "ready",
  });
  await waitFor(() =>
    expect(first.frame).toHaveAttribute("aria-hidden", "false"),
  );

  let releaseB!: (response: Response) => void;
  fetchMock.mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        releaseB = resolve;
      }),
  );
  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionB}
      format="docx"
      onActiveRevisionChange={active}
    />,
  );
  const second = (await screen.findByTitle(
    `DOCX revision ${revisionB} preview`,
  )) as HTMLIFrameElement;
  const secondPost = vi.spyOn(second.contentWindow!, "postMessage");
  sendFrame(
    second,
    { type: "composer-preview-ready" },
    "https://attacker.invalid",
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
  sendFrame(second, { type: "composer-preview-ready" });
  await waitFor(() => expect(releaseB).toBeDefined());
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, revisionA, "download"),
  );

  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionC}
      format="docx"
      onActiveRevisionChange={active}
    />,
  );
  releaseB(officeResponse());
  const third = await renderOffice(revisionC);
  sendFrame(third.frame, {
    ...third.message,
    type: "composer-preview-result",
    status: "ready",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("link", { name: "Download this revision" }),
    ).toHaveAttribute(
      "href",
      revisionArtifactPath(draftId, revisionC, "download"),
    ),
  );
  expect(
    secondPost.mock.calls.some(
      ([value]) => value.type === "composer-preview-render",
    ),
  ).toBe(false);
  expect(active).toHaveBeenLastCalledWith({
    draftId,
    revisionId: revisionC,
    format: "docx",
    downloadUrl: revisionArtifactPath(draftId, revisionC, "download"),
  });
});

test("a hostile replacement URL leaves the active Office preview and download paired", async () => {
  const fetchMock = vi.fn(async () => officeResponse());
  vi.stubGlobal("fetch", fetchMock);
  const active = vi.fn();
  const view = render(
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionA}
      format="docx"
      onActiveRevisionChange={active}
    />,
  );
  const first = await renderOffice(revisionA);
  sendFrame(first.frame, {
    ...first.message,
    type: "composer-preview-result",
    status: "ready",
  });
  await waitFor(() =>
    expect(first.frame).toHaveAttribute("aria-hidden", "false"),
  );

  view.rerender(
    <ComposerPreview
      draftId={draftId}
      revisionId={revisionB}
      format="docx"
      previewUrl="https://attacker.invalid/document"
      onActiveRevisionChange={active}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(/artifact|revision|URL/i);
  expect(first.frame).toHaveAttribute("aria-hidden", "false");
  expect(
    screen.getByRole("link", { name: "Download this revision" }),
  ).toHaveAttribute(
    "href",
    revisionArtifactPath(draftId, revisionA, "download"),
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(active).toHaveBeenLastCalledWith({
    draftId,
    revisionId: revisionA,
    format: "docx",
    downloadUrl: revisionArtifactPath(draftId, revisionA, "download"),
  });
});

test("a late PDF decoder cannot replace a newer usable page after a revision switch", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(new Uint8Array([37, 80, 68, 70]), {
          headers: { "Content-Type": "application/pdf" },
        }),
    ),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,AA==",
  );
  const page = {
    getViewport: ({ scale }: { scale: number }) => ({
      width: 200 * scale,
      height: 300 * scale,
    }),
    render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
  };
  const loaded = { numPages: 1, getPage: vi.fn(async () => page) };
  const taskA = { promise: Promise.resolve(loaded), destroy: vi.fn() };
  let releaseB!: (value: typeof loaded) => void;
  const taskB = {
    promise: new Promise<typeof loaded>((resolve) => {
      releaseB = resolve;
    }),
    destroy: vi.fn(),
  };
  const taskC = { promise: Promise.resolve(loaded), destroy: vi.fn() };
  pdf.getDocument
    .mockReturnValueOnce(taskA)
    .mockReturnValueOnce(taskB)
    .mockReturnValueOnce(taskC);
  const onReady = vi.fn();
  const onError = vi.fn();
  const preview = (key: string) => (
    <PdfPreview
      artifactUrl={`/pdf/${key}`}
      downloadUrl={`/pdf/${key}/download`}
      maxBytes={128}
      revisionKey={key}
      zoom={1}
      onReady={onReady}
      onError={onError}
    />
  );
  const view = render(preview("A"));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  view.rerender(preview("B"));
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  view.rerender(preview("C"));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith("C"));
  releaseB(loaded);
  await waitFor(() => expect(taskB.destroy).toHaveBeenCalled());
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A", "C"]);
  expect(onError).not.toHaveBeenCalled();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(taskA.destroy).toHaveBeenCalled();
  view.unmount();
  expect(taskC.destroy).toHaveBeenCalled();
});

test("PDF canvas failure reports the exact revision without announcing a usable page", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(new Uint8Array([37, 80, 68, 70]), {
          headers: { "Content-Type": "application/pdf" },
        }),
    ),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  const task = {
    promise: Promise.resolve({
      numPages: 1,
      getPage: vi.fn(async () => ({
        getViewport: () => ({ width: 100, height: 100 }),
        render: vi.fn(),
      })),
    }),
    destroy: vi.fn(),
  };
  pdf.getDocument.mockReturnValue(task);
  const onReady = vi.fn();
  const onError = vi.fn();
  const view = render(
    <PdfPreview
      artifactUrl="/pdf/canvas-broken"
      downloadUrl="/pdf/canvas-broken/download"
      maxBytes={128}
      revisionKey="canvas-broken"
      zoom={1}
      onReady={onReady}
      onError={onError}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Canvas is unavailable.",
  );
  expect(onReady).not.toHaveBeenCalled();
  expect(onError).toHaveBeenCalledWith(
    "canvas-broken",
    "Canvas is unavailable.",
  );
  expect(screen.queryByLabelText("PDF page 1")).toBeNull();
  view.unmount();
  expect(task.destroy).toHaveBeenCalled();
});

test("PDF thumbnails exceeding the canvas cap are skipped while the full page remains usable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(new Uint8Array([37, 80, 68, 70]), {
          headers: { "Content-Type": "application/pdf" },
        }),
    ),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  const page = {
    getViewport: ({ scale }: { scale: number }) =>
      scale === 0.16
        ? { width: 5_000, height: 5_000 }
        : { width: 200, height: 300 },
    render: vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() })),
  };
  pdf.getDocument.mockReturnValue({
    promise: Promise.resolve({
      numPages: 2,
      getPage: vi.fn(async () => page),
    }),
    destroy: vi.fn(),
  });
  const onReady = vi.fn();
  render(
    <PdfPreview
      artifactUrl="/pdf/huge-thumbnails"
      downloadUrl="/pdf/huge-thumbnails/download"
      maxBytes={128}
      revisionKey="huge-thumbnails"
      zoom={1}
      onReady={onReady}
    />,
  );
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("huge-thumbnails"));
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Show PDF page 1" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a superseded PDF first-page render cannot commit after a newer page is usable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(new Uint8Array([37, 80, 68, 70]), {
          headers: { "Content-Type": "application/pdf" },
        }),
    ),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue(
    "data:image/png;base64,AA==",
  );
  let releaseB!: () => void;
  const blocked = new Promise<void>((resolve) => {
    releaseB = resolve;
  });
  const page = (rendered: Promise<void>) => ({
    getViewport: () => ({ width: 200, height: 300 }),
    render: vi.fn(() => ({ promise: rendered, cancel: vi.fn() })),
  });
  const task = (rendered: Promise<void>) => ({
    promise: Promise.resolve({
      numPages: 1,
      getPage: vi.fn(async () => page(rendered)),
    }),
    destroy: vi.fn(),
  });
  const taskA = task(Promise.resolve());
  const taskB = task(blocked);
  const taskC = task(Promise.resolve());
  pdf.getDocument
    .mockReturnValueOnce(taskA)
    .mockReturnValueOnce(taskB)
    .mockReturnValueOnce(taskC);
  const onReady = vi.fn();
  const onError = vi.fn();
  const preview = (key: string) => (
    <PdfPreview
      artifactUrl={`/pdf/${key}`}
      downloadUrl={`/pdf/${key}/download`}
      maxBytes={128}
      revisionKey={key}
      zoom={1}
      onReady={onReady}
      onError={onError}
    />
  );
  const view = render(preview("A"));
  await waitFor(() => expect(onReady).toHaveBeenCalledWith("A"));
  view.rerender(preview("B"));
  await waitFor(() => expect(pdf.getDocument).toHaveBeenCalledTimes(2));
  view.rerender(preview("C"));
  await waitFor(() => expect(onReady).toHaveBeenLastCalledWith("C"));
  releaseB();
  await waitFor(() => expect(taskB.destroy).toHaveBeenCalled());
  expect(onReady.mock.calls.map(([key]) => key)).toEqual(["A", "C"]);
  expect(onError).not.toHaveBeenCalled();
  expect(screen.getByLabelText("PDF page 1")).toBeVisible();
});

test("PowerPoint thumbnail failures and DOM caps do not replace the usable slide", async () => {
  document.body.innerHTML = `
    <main id="viewport"><div id="document"></div>
    <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
    <span id="slide-position"></span><button id="previous-slide"></button>
    <button id="next-slide"></button></main>`;
  const target = document.getElementById("document")!;
  const main = {
    slideCount: 3,
    presentation: { slides: 3 },
    renderSlide: vi.fn(async (index: number) => {
      target.textContent = `Slide ${index + 1}`;
    }),
    destroy: vi.fn(),
  };
  class PptxViewer {
    constructor(private container: HTMLElement) {}
    static open = vi.fn(async () => {
      target.textContent = "Slide 1";
      return main;
    });
    load = vi.fn();
    renderSlide = vi.fn(async (index: number) => {
      if (index === 1) throw new Error("Thumbnail renderer failed");
      if (index === 2) {
        this.container.innerHTML = "<span>x</span>".repeat(2_001);
        return;
      }
      this.container.textContent = `Slide ${index + 1}`;
    });
    destroy = vi.fn();
  }
  const part = (name: string, xml: string) => ({
    name,
    dir: false,
    _data: { uncompressedSize: xml.length },
    async: async (kind: "uint8array" | "string") =>
      kind === "string" ? xml : new TextEncoder().encode(xml),
  });
  const packageParts = [
    part("[Content_Types].xml", "<Types/>"),
    part("ppt/presentation.xml", "<presentation/>"),
  ];
  Object.assign(window, {
    JSZip: {
      loadAsync: vi.fn(async () => ({
        files: Object.fromEntries(
          packageParts.map((item) => [item.name, item]),
        ),
      })),
    },
    docx: { renderAsync: vi.fn() },
    ComposerPptx: { PptxViewer, RECOMMENDED_ZIP_LIMITS: {} },
  });
  await import("../src/composer/preview/frame-client");
  const post = vi
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => {});
  const identity = {
    token: "pptx-runtime-coverage-token-123456789",
    draftId,
    revisionId: revisionA,
  };
  window.dispatchEvent(
    new MessageEvent("message", {
      source: window.parent,
      origin: new URL(document.URL).origin,
      data: {
        ...identity,
        type: "composer-preview-render",
        format: "pptx",
        bytes: new Uint8Array([1]).buffer,
        position: 0,
      },
    }),
  );
  await vi.waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({
        ...identity,
        type: "composer-preview-result",
        status: "ready",
      }),
      new URL(document.URL).origin,
    ),
  );
  await vi.waitFor(() =>
    expect(document.getElementById("slide-thumbnails")?.textContent).toContain(
      "Slide 2 thumbnail unavailable",
    ),
  );
  expect(target.textContent).toBe("Slide 1");
  document.getElementById("next-slide")!.click();
  await vi.waitFor(() =>
    expect(document.getElementById("slide-position")?.textContent).toBe(
      "Slide 2 of 3",
    ),
  );
  await vi.waitFor(() =>
    expect(document.querySelector('[aria-label="Show slide 3"]')).toBeNull(),
  );
  expect(target.textContent).toBe("Slide 2");
  expect(
    document.querySelectorAll("#slide-thumbnails span").length,
  ).toBeLessThan(2_000);
});

test("Office media validation checks decoded JPEG/GIF dimensions and actual expansion", async () => {
  const client = await import("../src/composer/preview/frame-client");
  const entry = (
    name: string,
    data: Uint8Array,
    advertised = data.byteLength,
  ) => ({
    name,
    dir: false,
    _data: { uncompressedSize: advertised },
    async: async (kind: "uint8array" | "string") =>
      kind === "string" ? new TextDecoder().decode(data) : data,
  });
  const xml = new TextEncoder().encode("<document/>");
  const base = [
    entry("[Content_Types].xml", xml),
    entry("word/document.xml", xml),
  ];
  const loadAsync = vi.fn();
  Object.assign(window, { JSZip: { loadAsync } });
  const jpeg = Uint8Array.from([
    255, 216, 255, 224, 0, 4, 0, 0, 255, 192, 0, 7, 8, 0, 2, 0, 3, 255, 217,
  ]);
  const gif = Uint8Array.from([71, 73, 70, 56, 57, 97, 2, 0, 3, 0]);
  const close = vi.fn();
  const decode = vi.fn(async (blob: Blob) => ({
    width: blob.type === "image/jpeg" ? 3 : 2,
    height: blob.type === "image/jpeg" ? 2 : 3,
    close,
  }));
  vi.stubGlobal("createImageBitmap", decode);
  expect(client.imagePixels("image.jpg", jpeg)).toBe(6);
  expect(client.imagePixels("image.gif", gif)).toBe(6);
  loadAsync.mockResolvedValueOnce({
    files: Object.fromEntries(
      [...base, entry("word/media/photo.jpg", jpeg)].map((item) => [
        item.name,
        item,
      ]),
    ),
  });
  await expect(
    client.validateOffice(new Uint8Array([1]).buffer, "docx"),
  ).resolves.toHaveProperty("files");
  loadAsync.mockResolvedValueOnce({
    files: Object.fromEntries(
      [...base, entry("word/media/icon.gif", gif)].map((item) => [
        item.name,
        item,
      ]),
    ),
  });
  await expect(
    client.validateOffice(new Uint8Array([1]).buffer, "docx"),
  ).resolves.toHaveProperty("files");
  expect(decode.mock.calls.map(([blob]) => blob.type)).toEqual([
    "image/jpeg",
    "image/gif",
  ]);
  expect(close).toHaveBeenCalledTimes(2);

  for (const corrupt of [
    Uint8Array.from([255, 216, 255, 217, 0, 0, 0, 0, 0, 0, 0, 0]),
    Uint8Array.from([255, 216, 255, 224, 0, 1, 0, 0, 0, 0, 0, 0]),
    Uint8Array.from([255, 216, 255, 224, 0, 50, 0, 0, 0, 0, 0, 0]),
  ]) {
    loadAsync.mockResolvedValueOnce({
      files: Object.fromEntries(
        [...base, entry("word/media/corrupt.jpg", corrupt)].map((item) => [
          item.name,
          item,
        ]),
      ),
    });
    await expect(
      client.validateOffice(new Uint8Array([1]).buffer, "docx"),
    ).rejects.toThrow("oversized image");
  }
  expect(decode).toHaveBeenCalledTimes(2);

  const inflated = entry("word/large.bin", new Uint8Array(33 * 1024 * 1024), 1);
  loadAsync.mockResolvedValueOnce({
    files: Object.fromEntries(
      [...base, inflated].map((item) => [item.name, item]),
    ),
  });
  await expect(
    client.validateOffice(new Uint8Array([1]).buffer, "docx"),
  ).rejects.toThrow("expands beyond the preview limit");
});

test("Office frame rejects forged render commands and clamps a requested slide position", async () => {
  vi.resetModules();
  document.body.innerHTML = `
    <main id="viewport"><div id="document"></div>
    <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
    <span id="slide-position"></span><button id="previous-slide"></button>
    <button id="next-slide"></button></main>`;
  const target = document.getElementById("document")!;
  const main = {
    slideCount: 3,
    presentation: { slides: 3 },
    renderSlide: vi.fn(async (index: number) => {
      target.textContent = `Slide ${index + 1}`;
    }),
    destroy: vi.fn(),
  };
  class PptxViewer {
    constructor(private container: HTMLElement) {}
    static open = vi.fn(async () => main);
    load = vi.fn();
    renderSlide = vi.fn(async (index: number) => {
      this.container.textContent = `Thumbnail ${index + 1}`;
    });
    destroy = vi.fn();
  }
  const part = (name: string, xml: string) => ({
    name,
    dir: false,
    _data: { uncompressedSize: xml.length },
    async: async (kind: "uint8array" | "string") =>
      kind === "string" ? xml : new TextEncoder().encode(xml),
  });
  const files = [
    part("[Content_Types].xml", "<Types/>"),
    part("ppt/presentation.xml", "<presentation/>"),
  ];
  Object.assign(window, {
    JSZip: {
      loadAsync: vi.fn(async () => ({
        files: Object.fromEntries(files.map((item) => [item.name, item])),
      })),
    },
    docx: { renderAsync: vi.fn() },
    ComposerPptx: { PptxViewer, RECOMMENDED_ZIP_LIMITS: {} },
  });
  await import("../src/composer/preview/frame-client");
  const post = vi
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => {});
  const origin = new URL(document.URL).origin;
  const identity = {
    token: "slide-position-security-token-123456789",
    draftId,
    revisionId: revisionC,
  };
  const command = {
    ...identity,
    type: "composer-preview-render",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
    position: 99,
  };
  const dispatch = (
    data: unknown,
    source: MessageEventSource | null = window.parent,
    eventOrigin = origin,
  ) =>
    window.dispatchEvent(
      new MessageEvent("message", { source, origin: eventOrigin, data }),
    );
  dispatch(command, window, "https://attacker.invalid");
  dispatch(null);
  dispatch({ ...command, token: "short" });
  dispatch({ ...command, bytes: new Uint8Array([1]) });
  expect(PptxViewer.open).not.toHaveBeenCalled();
  dispatch(command);
  await vi.waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({
        ...identity,
        type: "composer-preview-result",
        status: "ready",
      }),
      origin,
    ),
  );
  expect(main.renderSlide).toHaveBeenCalledWith(2);
  expect(document.getElementById("slide-position")?.textContent).toBe(
    "Slide 3 of 3",
  );
  expect(target.textContent).toBe("Slide 3");
  document.getElementById("next-slide")!.click();
  expect(main.renderSlide).toHaveBeenCalledTimes(1);
  dispatch({
    ...identity,
    type: "composer-preview-zoom",
    token: "stale",
    zoom: 2,
  });
  expect(target.style.zoom).toBe("");
  dispatch({ ...identity, type: "composer-preview-zoom", zoom: 1.5 });
  expect(target.style.zoom).toBe("1.5");
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
    callback(0),
  );
  document.getElementById("viewport")!.dispatchEvent(new Event("scroll"));
  expect(post).toHaveBeenCalledWith(
    expect.objectContaining({
      ...identity,
      type: "composer-preview-position",
      position: 2,
    }),
    origin,
  );
});

test("late PowerPoint slide work cannot restore an obsolete navigation choice", async () => {
  vi.resetModules();
  document.body.innerHTML = `
    <main id="viewport"><div id="document"></div>
    <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
    <span id="slide-position"></span><button id="previous-slide"></button>
    <button id="next-slide"></button></main>`;
  const target = document.getElementById("document")!;
  let rejectFirst!: (reason: Error) => void;
  let releaseLater!: () => void;
  let calls = 0;
  const main = {
    slideCount: 3,
    presentation: { slides: 3 },
    renderSlide: vi.fn(async (index: number) => {
      calls++;
      if (calls === 1)
        return new Promise<void>((_resolve, reject) => {
          rejectFirst = reject;
        });
      if (calls === 3)
        return new Promise<void>((resolve) => {
          releaseLater = resolve;
        });
      target.textContent = `Slide ${index + 1}`;
    }),
    destroy: vi.fn(),
  };
  class PptxViewer {
    constructor(private container: HTMLElement) {}
    static open = vi.fn(async () => {
      target.textContent = "Slide 1";
      return main;
    });
    load = vi.fn();
    renderSlide = vi.fn(async (index: number) => {
      this.container.textContent = `Thumbnail ${index + 1}`;
    });
    destroy = vi.fn();
  }
  const part = (name: string, xml: string) => ({
    name,
    dir: false,
    _data: { uncompressedSize: xml.length },
    async: async (kind: "uint8array" | "string") =>
      kind === "string" ? xml : new TextEncoder().encode(xml),
  });
  const files = [
    part("[Content_Types].xml", "<Types/>"),
    part("ppt/presentation.xml", "<presentation/>"),
  ];
  Object.assign(window, {
    JSZip: {
      loadAsync: vi.fn(async () => ({
        files: Object.fromEntries(files.map((item) => [item.name, item])),
      })),
    },
    docx: { renderAsync: vi.fn() },
    ComposerPptx: { PptxViewer, RECOMMENDED_ZIP_LIMITS: {} },
  });
  await import("../src/composer/preview/frame-client");
  const post = vi
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => {});
  const identity = {
    token: "slide-race-identity-token-123456789",
    draftId,
    revisionId: revisionB,
  };
  const origin = new URL(document.URL).origin;
  window.dispatchEvent(
    new MessageEvent("message", {
      source: window.parent,
      origin,
      data: {
        ...identity,
        type: "composer-preview-render",
        format: "pptx",
        bytes: new Uint8Array([1]).buffer,
      },
    }),
  );
  await vi.waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({ ...identity, status: "ready" }),
      origin,
    ),
  );
  document.getElementById("next-slide")!.click();
  await vi.waitFor(() => expect(rejectFirst).toBeDefined());
  (
    document.querySelector('[aria-label="Show slide 2"]') as HTMLButtonElement
  ).click();
  await vi.waitFor(() =>
    expect(document.getElementById("slide-position")?.textContent).toBe(
      "Slide 2 of 3",
    ),
  );
  rejectFirst(new Error("Late slide failure"));
  await Promise.resolve();
  expect(
    post.mock.calls.some(
      ([message]) => message.type === "composer-preview-runtime-error",
    ),
  ).toBe(false);
  document.getElementById("next-slide")!.click();
  await vi.waitFor(() => expect(releaseLater).toBeDefined());
  document.getElementById("previous-slide")!.click();
  await vi.waitFor(() =>
    expect(document.getElementById("slide-position")?.textContent).toBe(
      "Slide 1 of 3",
    ),
  );
  releaseLater();
  await Promise.resolve();
  expect(document.getElementById("slide-position")?.textContent).toBe(
    "Slide 1 of 3",
  );
  expect(target.textContent).toBe("Slide 1");
});
