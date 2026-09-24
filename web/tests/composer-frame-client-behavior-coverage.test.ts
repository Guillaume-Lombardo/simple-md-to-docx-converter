import { afterEach, expect, test, vi } from "vitest";

type FrameClient = typeof import("../src/composer/preview/frame-client");
type ZipEntry = {
  dir: boolean;
  name: string;
  _data: { uncompressedSize: number };
  async(kind: "uint8array" | "string"): Promise<Uint8Array | string>;
};

const encoder = new TextEncoder();
const docxWindow = vi.hoisted(() => ({
  update: vi.fn(),
  contentPreserved: vi.fn(() => true),
  mountedUnits: vi.fn(() => 1),
  detachedBlockNodes: vi.fn(() => 0),
  serializedMarkupBytes: 0,
  totalUnits: 1,
  totalBlockNodes: 1,
}));
vi.mock("../src/composer/preview/docx-window", () => ({
  normalizeDocxTextBoxes: vi.fn(),
  windowDocx: vi.fn(() => docxWindow),
}));

function part(name: string, contents: string | Uint8Array): ZipEntry {
  const bytes =
    typeof contents === "string" ? encoder.encode(contents) : contents;
  return {
    dir: false,
    name,
    _data: { uncompressedSize: bytes.byteLength },
    async: async (kind) =>
      kind === "string" ? new TextDecoder().decode(bytes) : bytes,
  };
}

function archive(format: "docx" | "pptx", extras: ZipEntry[] = []) {
  const entries = [
    part("[Content_Types].xml", "<Types/>"),
    part(
      format === "docx" ? "word/document.xml" : "ppt/presentation.xml",
      "<document/>",
    ),
    ...extras,
  ];
  return {
    files: Object.fromEntries(entries.map((entry, index) => [index, entry])),
  };
}

function png(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(24);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return bytes;
}

let stopMessageListener: (() => void) | undefined;

async function setup(overrides?: {
  renderAsync?: (target: HTMLElement) => Promise<void>;
  pptxViewer?: unknown;
}) {
  vi.resetModules();
  document.body.innerHTML = `<main id="viewport">
    <div id="document"></div><nav id="slide-controls"></nav>
    <nav id="slide-thumbnails"></nav><span id="slide-position"></span>
    <button id="previous-slide"></button><button id="next-slide"></button>
  </main>`;
  const loadAsync = vi.fn(async () => archive("docx"));
  const renderAsync = vi.fn(
    async (_bytes: ArrayBuffer, target: HTMLElement) => {
      if (overrides?.renderAsync) return overrides.renderAsync(target);
      target.innerHTML =
        '<section class="docx"><article><p>Safe text</p></article></section>';
    },
  );
  Object.assign(window, {
    JSZip: { loadAsync },
    docx: { renderAsync },
    ComposerPptx: {
      PptxViewer: overrides?.pptxViewer ?? {},
      RECOMMENDED_ZIP_LIMITS: {},
    },
  });
  vi.stubGlobal(
    "createImageBitmap",
    vi.fn(async () => ({ width: 1, height: 1, close: vi.fn() })),
  );
  const addListener = vi.spyOn(window, "addEventListener");
  const post = vi
    .spyOn(window.parent, "postMessage")
    .mockImplementation(() => {});
  const client: FrameClient =
    await import("../src/composer/preview/frame-client");
  const listener = addListener.mock.calls.find(
    ([type]) => type === "message",
  )?.[1];
  addListener.mockRestore();
  if (!listener)
    throw new Error("The Office child did not register its message listener.");
  stopMessageListener = () => window.removeEventListener("message", listener);
  return { client, loadAsync, renderAsync, post };
}

function message(
  data: Record<string, unknown>,
  origin = new URL(document.URL).origin,
) {
  window.dispatchEvent(
    new MessageEvent("message", { source: window.parent, origin, data }),
  );
}

afterEach(() => {
  stopMessageListener?.();
  stopMessageListener = undefined;
  docxWindow.update.mockReset();
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("archive validation rejects duplicate, active, external, and inflated parts before rendering", async () => {
  const { client, loadAsync, renderAsync } = await setup();
  const bytes = new Uint8Array([1]).buffer;
  for (const [extra, error] of [
    [part("WORD/DOCUMENT.XML", "<document/>"), "duplicate paths"],
    [part("word/embeddings/object.bin", "object"), "embedded content"],
    [part("word/media/movie.mp4", "video"), "unsupported media"],
    [
      part(
        "word/document.xml.rels",
        '<Relationships><Relationship TargetMode="External" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.invalid/pixel.png"/></Relationships>',
      ),
      "external resource",
    ],
    [part("word/styles.xml", "<!DOCTYPE x><styles/>"), "unsupported XML"],
  ] as const) {
    loadAsync.mockResolvedValueOnce(archive("docx", [extra]));
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(error);
  }
  const oversized = part("word/large.bin", "small");
  oversized._data.uncompressedSize = 33 * 1024 * 1024;
  loadAsync.mockResolvedValueOnce(archive("docx", [oversized]));
  await expect(client.validateOffice(bytes, "docx")).rejects.toThrow("expands");
  expect(renderAsync).not.toHaveBeenCalled();
});

test("image validation checks decoded dimensions and cumulative pixel cost", async () => {
  const { client, loadAsync } = await setup();
  const bytes = new Uint8Array([1]).buffer;
  const bitmapClose = vi.fn();
  vi.stubGlobal(
    "createImageBitmap",
    vi.fn(async () => ({ width: 1, height: 1, close: bitmapClose })),
  );
  loadAsync.mockResolvedValueOnce(
    archive("docx", [part("word/media/one.png", png(2, 2))]),
  );
  await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
    "dimensions are inconsistent",
  );
  expect(bitmapClose).toHaveBeenCalledTimes(1);

  vi.stubGlobal(
    "createImageBitmap",
    vi.fn(async () => ({ width: 4_000, height: 4_000, close: bitmapClose })),
  );
  loadAsync.mockResolvedValueOnce(
    archive("docx", [
      part("word/media/one.png", png(4_000, 4_000)),
      part("word/media/two.png", png(4_000, 4_000)),
      part("word/media/three.png", png(4_000, 4_000)),
    ]),
  );
  await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
    "too many image pixels",
  );
  expect(bitmapClose).toHaveBeenCalledTimes(4);
});

test("the child ignores forged and duplicate render messages and redacts scroll failures", async () => {
  const { loadAsync, post } = await setup({
    renderAsync: async (target) => {
      target.innerHTML =
        '<section class="docx"><article><p><a href="https://example.invalid">Link</a></p></article></section>';
    },
  });
  let releaseArchive!: (value: ReturnType<typeof archive>) => void;
  loadAsync.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        releaseArchive = resolve;
      }),
  );
  const request = {
    type: "composer-preview-render",
    token: "a-revision-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "docx",
    bytes: new Uint8Array([1]).buffer,
  };
  message(request, "https://attacker.invalid");
  message({ ...request, token: "short" });
  expect(loadAsync).not.toHaveBeenCalled();
  message(request);
  message(request);
  expect(loadAsync).toHaveBeenCalledTimes(1);
  releaseArchive(archive("docx"));
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "ready",
      ),
    ).toBe(true),
  );
  const link = document.querySelector("#document a")!;
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  link.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  const submit = new Event("submit", { bubbles: true, cancelable: true });
  document.dispatchEvent(submit);
  expect(submit.defaultPrevented).toBe(true);

  docxWindow.update.mockImplementationOnce(() => {
    throw new Error("private document bytes");
  });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  document.getElementById("viewport")!.dispatchEvent(new Event("scroll"));
  expect(document.getElementById("document")?.textContent).toContain(
    "cannot be shown safely",
  );
  expect(document.body.textContent).not.toContain("private document bytes");
  expect(
    post.mock.calls.some(
      ([value]) => value.type === "composer-preview-runtime-error",
    ),
  ).toBe(true);
});

test("a presentation with no slides fails closed", async () => {
  const target = document.createElement("div");
  const viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.innerHTML = target.innerHTML;
      return {
        slideCount: target.childElementCount ? 1 : 0,
        presentation: {},
        renderSlide: vi.fn(),
        destroy: vi.fn(),
      };
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "presentation-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "error",
      ),
    ).toBe(true),
  );
  expect(document.getElementById("document")?.textContent).toContain(
    "cannot be shown safely",
  );
});

test("unknown PPTX source glyph fails before the renderer receives bytes", async () => {
  const viewer = { open: vi.fn() };
  const { loadAsync, post } = await setup({ pptxViewer: viewer });
  loadAsync.mockResolvedValueOnce(
    archive("pptx", [
      part(
        "ppt/slides/slide1.xml",
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:pPr><a:buFont typeface="StarBats"/><a:buChar char="\uf099"/></a:pPr></p:sld>',
      ),
    ]),
  );
  message({
    type: "composer-preview-render",
    token: "unknown-glyph-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "error",
      ),
    ).toBe(true),
  );
  expect(viewer.open).not.toHaveBeenCalled();
  expect(document.getElementById("document")?.textContent).toContain(
    "cannot be shown safely",
  );
});

test("a presentation with excessive visible DOM fails before becoming ready", async () => {
  const viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.innerHTML = `<div>${"<span>shape</span>".repeat(2_001)}</div>`;
      return {
        slideCount: 1,
        presentation: {},
        renderSlide: vi.fn(),
        destroy: vi.fn(),
      };
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "large-slide-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "error",
      ),
    ).toBe(true),
  );
  expect(
    document.getElementById("document")?.querySelectorAll("span"),
  ).toHaveLength(0);
});

test("late vendor slide and thumbnail repaint preserves source-normalized bullets", async () => {
  const viewers: Array<{
    container: HTMLElement;
    destroy: ReturnType<typeof vi.fn>;
  }> = [];
  const bullet = '<span style="font-family: Arial">• </span>';
  const Viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.innerHTML = `<p>${bullet}Visible slide</p>`;
      return {
        slideCount: 3,
        presentation: { slides: 3 },
        renderSlide: vi.fn(async (index: number) => {
          container.innerHTML = `<p>${bullet}Visible slide ${index + 1}</p>`;
        }),
        destroy: vi.fn(),
      };
    });
    destroy = vi.fn();
    constructor(public container: HTMLElement) {
      viewers.push(this);
    }
    load = vi.fn();
    renderSlide = vi.fn(async (index: number) => {
      this.container.innerHTML = `${bullet}Thumbnail ${index + 1}`;
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: Viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "late-repaint-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "ready",
      ),
    ).toBe(true),
  );
  const visible = document.getElementById("document")!;
  const thumbnail = viewers[0]!.container;
  expect(visible.textContent).toContain("•");
  await vi.waitFor(() => expect(thumbnail.textContent).toContain("•"));

  // The pinned renderer can replace both trees after its initial ready result.
  visible.innerHTML = `<p>${bullet}Repainted slide</p>`;
  thumbnail.innerHTML = `${bullet}Repainted thumbnail`;
  await vi.waitFor(() => {
    expect(visible.textContent).toContain("• Repainted slide");
    expect(thumbnail.textContent).toContain("• Repainted thumbnail");
  });
  expect(visible.textContent).not.toContain("\uf095");
  expect(thumbnail.textContent).not.toContain("\uf095");
  expect(
    post.mock.calls.some(
      ([value]) => value.type === "composer-preview-runtime-error",
    ),
  ).toBe(false);

  thumbnail.innerHTML = "<script>window.thumbnailOwned = true</script>";
  await vi.waitFor(() => expect(viewers[0]!.destroy).toHaveBeenCalledTimes(1));
  expect(thumbnail.isConnected).toBe(false);
  thumbnail.innerHTML = `${bullet}Detached repaint`;
  expect(document.getElementById("document")?.textContent).toContain("•");
  expect(
    post.mock.calls.some(
      ([value]) => value.type === "composer-preview-runtime-error",
    ),
  ).toBe(false);
});

test("late passive anchor stays inert without triggering a vendor redraw loop", async () => {
  const Viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.textContent = "Safe slide";
      return {
        slideCount: 1,
        presentation: null,
        renderSlide: vi.fn(),
        destroy: vi.fn(),
      };
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: Viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "passive-anchor-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "ready",
      ),
    ).toBe(true),
  );
  const href = "https://example.invalid/preview-link";
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.textContent = "Source link";
  const redraw = vi.fn();
  const vendorObserver = new MutationObserver(() => {
    if (!anchor.hasAttribute("href")) {
      redraw();
      anchor.setAttribute("href", href);
    }
  });
  vendorObserver.observe(anchor, {
    attributes: true,
    attributeFilter: ["href"],
  });
  document.getElementById("document")!.append(anchor);
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(anchor.getAttribute("href")).toBe(href);
  expect(redraw).not.toHaveBeenCalled();
  for (const type of ["click", "auxclick", "contextmenu"])
    expect(
      anchor.dispatchEvent(
        new MouseEvent(type, { bubbles: true, cancelable: true }),
      ),
    ).toBe(false);
  expect(
    anchor.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Enter",
        bubbles: true,
        cancelable: true,
      }),
    ),
  ).toBe(false);
  expect(
    post.mock.calls.some(
      ([value]) => value.type === "composer-preview-runtime-error",
    ),
  ).toBe(false);
  vendorObserver.disconnect();
});

test.each([
  ["unknown glyph", '<span style="font-family: StarBats">\uf099 </span>'],
  ["active link", '<a href="javascript:alert(1)">Unsafe</a>'],
  ["active element", "<script>window.documentOwned = true</script>"],
  [
    "external style URL",
    '<div style="background-image:url(https://example.invalid/track)">Unsafe</div>',
  ],
  ["DOM overflow", `<div>${"<span>node</span>".repeat(2_001)}</div>`],
])("late vendor %s fails closed after ready", async (_label, unsafeMarkup) => {
  const Viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.textContent = "Safe slide";
      return {
        slideCount: 1,
        presentation: null,
        renderSlide: vi.fn(),
        destroy: vi.fn(),
      };
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: Viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "late-unsafe-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "ready",
      ),
    ).toBe(true),
  );
  document.getElementById("document")!.innerHTML = unsafeMarkup;
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) => value.type === "composer-preview-runtime-error",
      ),
    ).toBe(true),
  );
  expect(document.getElementById("document")?.textContent).toContain(
    "cannot be shown safely",
  );
  expect(
    document.getElementById("document")?.querySelectorAll("script,span"),
  ).toHaveLength(0);
});

test.each<[string, (element: HTMLElement) => void]>([
  [
    "attribute-only external URL",
    (element) => {
      element.style.backgroundImage = "url(https://example.invalid/track)";
    },
  ],
  [
    "mutation flood",
    (element) => {
      for (let index = 0; index <= 8_000; index++)
        element.dataset.update = String(index);
    },
  ],
])("late vendor %s is bounded and fails closed", async (_label, mutate) => {
  const Viewer = class {
    static open = vi.fn(async (_bytes: ArrayBuffer, container: HTMLElement) => {
      container.innerHTML = '<div id="slide-shape">Safe slide</div>';
      return {
        slideCount: 1,
        presentation: null,
        renderSlide: vi.fn(),
        destroy: vi.fn(),
      };
    });
  };
  const { loadAsync, post } = await setup({ pptxViewer: Viewer });
  loadAsync.mockResolvedValueOnce(archive("pptx"));
  message({
    type: "composer-preview-render",
    token: "late-attribute-token-long-enough",
    draftId: "11111111-1111-1111-1111-111111111111",
    revisionId: "22222222-2222-2222-2222-222222222222",
    format: "pptx",
    bytes: new Uint8Array([1]).buffer,
  });
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) =>
          value.type === "composer-preview-result" && value.status === "ready",
      ),
    ).toBe(true),
  );
  mutate(document.getElementById("slide-shape")!);
  await vi.waitFor(() =>
    expect(
      post.mock.calls.some(
        ([value]) => value.type === "composer-preview-runtime-error",
      ),
    ).toBe(true),
  );
  expect(document.getElementById("document")?.textContent).toContain(
    "cannot be shown safely",
  );
});
