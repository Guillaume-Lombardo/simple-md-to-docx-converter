import { afterEach, test, vi } from "vitest";

type FrameClient = typeof import("../src/composer/preview/frame-client");
type Entry = {
  dir: boolean;
  name: string;
  _data: { uncompressedSize: number };
  async(kind: "string" | "uint8array"): Promise<string | Uint8Array>;
};

const encoder = new TextEncoder();
const drawingNamespace =
  "http://schemas.openxmlformats.org/drawingml/2006/main";
const presentationNamespace =
  "http://schemas.openxmlformats.org/presentationml/2006/main";
const slide = (content: string) =>
  `<p:sld xmlns:p="${presentationNamespace}" xmlns:a="${drawingNamespace}">${content}</p:sld>`;
const portableBullet =
  '<a:pPr><a:buFont typeface="StarBats"/><a:buChar char="\uf095"/></a:pPr>';

function entry(name: string, content: string): Entry {
  const data = encoder.encode(content);
  return {
    dir: false,
    name,
    _data: { uncompressedSize: data.byteLength },
    async: async (kind) =>
      kind === "string" ? new TextDecoder().decode(data) : data,
  };
}

function archive(slideXml = slide(portableBullet)) {
  const files = [
    entry("[Content_Types].xml", "<Types/>"),
    entry("ppt/presentation.xml", "<presentation/>"),
    entry("ppt/slides/slide1.xml", slideXml),
  ];
  return { files: Object.fromEntries(files.map((part) => [part.name, part])) };
}

type PreviewArchive = ReturnType<typeof archive> & {
  file?: (name: string, contents: string) => unknown;
  generateAsync?: () => Promise<ArrayBuffer>;
};

let removeMessageListener: (() => void) | undefined;

async function setup(renderMarkup = "<p>Safe slide</p>") {
  vi.resetModules();
  document.body.innerHTML = `<main id="viewport">
    <div id="document"></div><nav id="slide-controls"></nav>
    <nav id="slide-thumbnails"></nav><span id="slide-position"></span>
    <button id="previous-slide"></button><button id="next-slide"></button>
  </main>`;
  const loadAsync = vi.fn(async (): Promise<PreviewArchive> => archive());
  const open = vi.fn(async (_bytes: ArrayBuffer, target: HTMLElement) => {
    target.innerHTML = renderMarkup;
    return {
      slideCount: 1,
      presentation: null,
      renderSlide: vi.fn(async () => undefined),
      destroy: vi.fn(),
    };
  });
  Object.assign(window, {
    JSZip: { loadAsync },
    docx: { renderAsync: vi.fn() },
    ComposerPptx: { PptxViewer: { open }, RECOMMENDED_ZIP_LIMITS: {} },
  });
  const addListener = vi.spyOn(window, "addEventListener");
  const post = vi
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => undefined);
  const client: FrameClient =
    await import("../src/composer/preview/frame-client");
  const listener = addListener.mock.calls.find(
    ([type]) => type === "message",
  )?.[1];
  addListener.mockRestore();
  if (!listener)
    throw new Error("The Office preview listener was not installed.");
  removeMessageListener = () => window.removeEventListener("message", listener);
  return { client, loadAsync, open, post };
}

function renderPptx() {
  window.dispatchEvent(
    new MessageEvent("message", {
      source: window.parent,
      origin: new URL(document.URL).origin,
      data: {
        type: "composer-preview-render",
        token: "archive-boundary-token-long-enough",
        draftId: "11111111-1111-1111-1111-111111111111",
        revisionId: "22222222-2222-2222-2222-222222222222",
        format: "pptx",
        bytes: new Uint8Array([80, 75]).buffer,
      },
    }),
  );
}

async function expectSafeFailure(post: ReturnType<typeof vi.fn>) {
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([message]) =>
          message.type === "composer-preview-result" &&
          message.status === "error",
      ),
    ).toBe(true),
  );
  expect(document.getElementById("document")?.textContent).toBe(
    "This document cannot be shown safely in the native preview.",
  );
  expect(
    post.mock.calls.some(
      ([message]) =>
        message.type === "composer-preview-result" &&
        message.status === "ready",
    ),
  ).toBe(false);
}

afterEach(() => {
  removeMessageListener?.();
  removeMessageListener = undefined;
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

test("malformed slide XML and private symbols outside bullets cannot become a portable presentation", async () => {
  const { client } = await setup();
  expect(() => client.normalizePptxBulletXml("<p:sld><a:pPr>")).toThrow(
    "invalid slide XML",
  );
  expect(() =>
    client.normalizePptxBulletXml(
      slide(`${portableBullet}<a:r><a:t>Private \uf099 text</a:t></a:r>`),
    ),
  ).toThrow("unsupported symbol glyph");
  expect(client.normalizePptxBulletXml(slide("<a:t>Normal text</a:t>"))).toBe(
    slide("<a:t>Normal text</a:t>"),
  );
});

test.each([
  [
    "bullet under a PresentationML parent",
    '<p:pPr><a:buFont typeface="StarBats"/><a:buChar char="\uf095"/></p:pPr>',
  ],
  ["bullet without a font", '<a:pPr><a:buChar char="\uf095"/></a:pPr>'],
  [
    "bullet with competing fonts",
    '<a:pPr><a:buFont typeface="StarBats"/><a:buFont typeface="Symbol"/><a:buChar char="\uf095"/></a:pPr>',
  ],
  [
    "bullet with an unrecognized typeface",
    '<a:pPr><a:buFont typeface="Unknown"/><a:buChar char="\uf095"/></a:pPr>',
  ],
  [
    "bullet with an unrecognized private glyph",
    '<a:pPr><a:buFont typeface="StarBats"/><a:buChar char="\uf099"/></a:pPr>',
  ],
])(
  "%s is rejected instead of silently changing its meaning",
  async (_name, content) => {
    const { client } = await setup();
    expect(() => client.normalizePptxBulletXml(slide(content))).toThrow(
      "unsupported symbol glyph",
    );
  },
);

test("normalization changes only an explicitly supported bullet and enforces both byte caps", async () => {
  const { client } = await setup();
  const normalized = client.normalizePptxBulletXml(
    slide(`${portableBullet}<a:t>Normal text</a:t>`),
  );
  expect(normalized).toContain('<a:buFont typeface="Arial"/>');
  expect(normalized).toContain('<a:buChar char="•"/>');
  expect(normalized).toContain("Normal text");
  expect(normalized).not.toContain("\uf095");

  const memberCap = 32 * 1024 * 1024;
  const expandedCap = 128 * 1024 * 1024;
  expect(client.assertNormalizedPptxBudget(0, 0, memberCap)).toBe(memberCap);
  expect(() => client.assertNormalizedPptxBudget(0, 0, memberCap + 1)).toThrow(
    "preview limit",
  );
  expect(client.assertNormalizedPptxBudget(expandedCap - 5, 100, 105)).toBe(
    expandedCap,
  );
  expect(() =>
    client.assertNormalizedPptxBudget(expandedCap - 5, 100, 106),
  ).toThrow("preview limit");
});

test.each(["missing file writer", "missing archive generator"])(
  "%s prevents normalized bullet bytes from reaching the presentation renderer",
  async (missing) => {
    const { loadAsync, open, post } = await setup();
    loadAsync.mockResolvedValueOnce({
      ...archive(),
      ...(missing === "missing file writer" ? {} : { file: vi.fn() }),
      ...(missing === "missing archive generator"
        ? {}
        : { generateAsync: vi.fn(async () => new ArrayBuffer(2)) }),
    });
    renderPptx();
    await expectSafeFailure(post);
    expect(open).not.toHaveBeenCalled();
  },
);

test("normalization refuses a regenerated PPTX larger than the compressed preview budget", async () => {
  const { loadAsync, open, post } = await setup();
  const write = vi.fn();
  const generateAsync = vi.fn(
    async () => new ArrayBuffer(64 * 1024 * 1024 + 1),
  );
  loadAsync.mockResolvedValueOnce({
    ...archive(),
    file: write,
    generateAsync,
  });
  renderPptx();
  await expectSafeFailure(post);
  expect(write).toHaveBeenCalledWith(
    "ppt/slides/slide1.xml",
    expect.stringContaining('typeface="Arial"'),
  );
  expect(generateAsync).toHaveBeenCalledOnce();
  expect(open).not.toHaveBeenCalled();
});

test.each([
  ["active script", "<script>window.unsafe = true</script>"],
  ["external image", '<img src="https://example.invalid/track.png">'],
  ["active link", '<a href="javascript:alert(1)">Open</a>'],
])(
  "initial vendor %s markup is removed before preview readiness",
  async (_name, markup) => {
    const { loadAsync, open, post } = await setup(markup);
    loadAsync.mockResolvedValueOnce(archive(slide("<a:t>Safe source</a:t>")));
    renderPptx();
    await expectSafeFailure(post);
    expect(open).toHaveBeenCalledOnce();
    expect(document.querySelector("script,img,a")).toBeNull();
  },
);
