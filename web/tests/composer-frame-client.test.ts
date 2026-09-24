import { beforeAll, describe, expect, it, vi } from "vitest";

type FrameClient = typeof import("../src/composer/preview/frame-client");
type ZipPart = {
  dir: boolean;
  name: string;
  _data: { uncompressedSize: number };
  async(kind: "uint8array" | "string"): Promise<Uint8Array | string>;
};

const encoder = new TextEncoder();
const png = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/xAAAAABJRU5ErkJggg==",
  ),
  (character) => character.charCodeAt(0),
);

function part(name: string, content: string | Uint8Array): ZipPart {
  const data = typeof content === "string" ? encoder.encode(content) : content;
  return {
    dir: false,
    name,
    _data: { uncompressedSize: data.byteLength },
    async: async (kind) =>
      kind === "string" ? new TextDecoder().decode(data) : data,
  };
}

function archive(extra: ZipPart[] = []) {
  const files = [
    part("[Content_Types].xml", "<Types/>"),
    part("word/document.xml", "<document/>"),
    ...extra,
  ];
  return {
    files: Object.fromEntries(files.map((entry) => [entry.name, entry])),
  };
}

describe("isolated Office frame client", () => {
  let client: FrameClient;
  let loadAsync: ReturnType<typeof vi.fn>;
  let renderAsync: ReturnType<typeof vi.fn>;

  beforeAll(async () => {
    document.body.innerHTML = `
      <main id="viewport"><div id="document"></div>
      <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
      <span id="slide-position"></span><button id="previous-slide"></button>
      <button id="next-slide"></button></main>`;
    loadAsync = vi.fn().mockResolvedValue(archive());
    renderAsync = vi
      .fn()
      .mockImplementation(async (_bytes, target: HTMLElement) => {
        target.innerHTML =
          '<section class="docx"><article><p>Safe document <a href="https://example.invalid">inert link</a></p></article></section>';
      });
    Object.assign(window, {
      JSZip: { loadAsync },
      docx: { renderAsync },
      ComposerPptx: { PptxViewer: {}, RECOMMENDED_ZIP_LIMITS: {} },
    });
    vi.stubGlobal(
      "createImageBitmap",
      vi.fn().mockResolvedValue({ width: 1, height: 1, close: vi.fn() }),
    );
    client = await import("../src/composer/preview/frame-client");
  });

  it("normalizes exact DrawingML bullet pairs before render and rejects lookalikes", () => {
    const xml = (font: string, glyph: string) =>
      `<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:pPr><a:buFont typeface="${font}"/><a:buChar char="${glyph}"/></a:pPr></p:sld>`;
    expect(client.normalizePptxBulletXml(xml("StarBats", "\uf095"))).toContain(
      'typeface="Arial"/><a:buChar char="•"',
    );
    expect(client.normalizePptxBulletXml(xml("Wingdings", "\uf06c"))).toContain(
      'typeface="Arial"/><a:buChar char="•"',
    );
    expect(client.normalizePptxBulletXml(xml("Symbol", "\uf02d"))).toContain(
      'typeface="Arial"/><a:buChar char="−"',
    );
    expect(() =>
      client.normalizePptxBulletXml(xml("StarBats", "\uf099")),
    ).toThrow("unsupported symbol glyph");
    expect(() =>
      client.normalizePptxBulletXml(xml("UntrustedGlyphs", "\uf095")),
    ).toThrow("unsupported symbol glyph");
    expect(() =>
      client.normalizePptxBulletXml(xml("StarBats", "&#xF099;")),
    ).toThrow("unsupported symbol glyph");
    expect(() =>
      client.normalizePptxBulletXml(
        xml("StarBats", "\uf095").replace(
          '<a:buFont typeface="StarBats"/>',
          '<a:buFont typeface="StarBats"/><a:buFont typeface="UntrustedGlyphs"/>',
        ),
      ),
    ).toThrow("unsupported symbol glyph");
    expect(() =>
      client.normalizePptxBulletXml(
        xml("StarBats", "\uf095")
          .replace("<a:pPr>", "<a:other>")
          .replace("</a:pPr>", "</a:other>"),
      ),
    ).toThrow("unsupported symbol glyph");
    expect(client.assertNormalizedPptxBudget(128 * 1024 * 1024, 100, 100)).toBe(
      128 * 1024 * 1024,
    );
    expect(() =>
      client.assertNormalizedPptxBudget(128 * 1024 * 1024, 100, 101),
    ).toThrow("preview limit");
    expect(() =>
      client.assertNormalizedPptxBudget(0, 0, 32 * 1024 * 1024 + 1),
    ).toThrow("preview limit");
    const root = document.createElement("div");
    root.textContent = "• Point";
    client.assertSupportedPptxGlyphs(root);
    expect(root.textContent).toBe("• Point");
    root.textContent = "\uf099 Point";
    expect(() => client.assertSupportedPptxGlyphs(root)).toThrow(
      "unsupported symbol glyph",
    );
  });

  it("rejects unsafe paths, external resources, active parts and archive inflation", async () => {
    for (const name of [
      "/word/document.xml",
      "../escape",
      "word/./part.xml",
      "word\\part.xml",
      "word/\u0000.xml",
    ])
      expect(() => client.assertArchivePath(name)).toThrow("unsafe path");
    expect(() => client.assertArchivePath("word/document.xml")).not.toThrow();
    expect(() => client.validateRelationships("<broken")).toThrow(
      "invalid relationships",
    );
    expect(() =>
      client.validateRelationships(
        '<Relationships><Relationship Type="image" TargetMode="External" Target="https://example.invalid"/></Relationships>',
      ),
    ).toThrow("external resource");
    expect(() =>
      client.validateRelationships(
        '<Relationships><Relationship Type="http://x/hyperlink" TargetMode="External" Target="https://example.invalid"/></Relationships>',
      ),
    ).not.toThrow();

    const bytes = new Uint8Array([1]).buffer;
    for (const extra of [
      [part("word/media/evil.svg", "<svg/>")],
      [part("word/activeX/item.xml", "<x/>")],
      [
        part(
          "word/_rels/document.xml.rels",
          '<Relationships><Relationship Type="image" TargetMode="External"/></Relationships>',
        ),
      ],
      [part("word/header.xml", "<!DOCTYPE doc><doc/>")],
      [part("WORD/DOCUMENT.XML", "<other/>")],
    ]) {
      loadAsync.mockResolvedValueOnce(archive(extra));
      await expect(client.validateOffice(bytes, "docx")).rejects.toThrow();
    }
    const huge = part("word/large.xml", "<x/>");
    huge._data.uncompressedSize = 33 * 1024 * 1024;
    loadAsync.mockResolvedValueOnce(archive([huge]));
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "expands beyond",
    );
    loadAsync.mockResolvedValueOnce({
      files: { "word/document.xml": part("word/document.xml", "<x/>") },
    });
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "missing its main",
    );
    loadAsync.mockResolvedValueOnce({ files: {} });
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "too many parts",
    );
    loadAsync.mockResolvedValueOnce(
      archive([part("word/media/unsupported.bin", new Uint8Array([1]))]),
    );
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "unsupported media",
    );
    await expect(
      client.validateOffice(new ArrayBuffer(0), "docx"),
    ).rejects.toThrow("preview limit");
  });

  it("checks raster signatures and total decoded image dimensions", async () => {
    expect(client.validImageSignature("x.png", png)).toBe(true);
    expect(client.imagePixels("x.png", png)).toBe(1);
    expect(
      client.validImageSignature("x.jpg", new Uint8Array([255, 216, 255])),
    ).toBe(true);
    expect(client.validImageSignature("x.gif", encoder.encode("GIF89a"))).toBe(
      true,
    );
    expect(
      client.imagePixels(
        "x.gif",
        Uint8Array.from([71, 73, 70, 56, 57, 97, 2, 0, 3, 0]),
      ),
    ).toBe(6);
    expect(client.validImageSignature("x.png", encoder.encode("bad"))).toBe(
      false,
    );
    loadAsync.mockResolvedValueOnce(
      archive([part("word/media/image.png", png)]),
    );
    await expect(
      client.validateOffice(new Uint8Array([1]).buffer, "docx"),
    ).resolves.toHaveProperty("files");
    loadAsync.mockResolvedValueOnce(
      archive([part("word/media/image.png", encoder.encode("bad"))]),
    );
    await expect(
      client.validateOffice(new Uint8Array([1]).buffer, "docx"),
    ).rejects.toThrow("invalid image");
  });

  it("ignores forged messages and renders only the selected identity", async () => {
    const post = vi
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    const identity = {
      token: "qualification-token-1234567890",
      draftId: "11111111-1111-1111-1111-111111111111",
      revisionId: "22222222-2222-2222-2222-222222222222",
    };
    const render = {
      ...identity,
      type: "composer-preview-render",
      format: "docx",
      bytes: new Uint8Array([1, 2, 3]).buffer,
      position: 0,
      zoom: 1,
    };
    const dispatch = (
      data: Record<string, unknown>,
      origin = new URL(document.URL).origin,
    ) =>
      window.dispatchEvent(
        new MessageEvent("message", { source: window.parent, origin, data }),
      );
    dispatch({ type: "composer-preview-ping" });
    expect(
      post.mock.calls.some(
        ([message]) => message.type === "composer-preview-ready",
      ),
    ).toBe(true);
    dispatch(render, "https://attacker.invalid");
    dispatch({ ...render, token: "short" });
    expect(renderAsync).not.toHaveBeenCalled();
    dispatch(render);
    await vi.waitFor(() =>
      expect(
        post.mock.calls.some(
          ([message]) =>
            message.type === "composer-preview-result" &&
            message.status === "ready",
        ),
      ).toBe(true),
    );
    expect(renderAsync).toHaveBeenCalledTimes(1);
    expect(document.getElementById("document")?.textContent).toContain(
      "Safe document",
    );
    const link = document.querySelector("#document a")!;
    expect(link.hasAttribute("href")).toBe(false);
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
    const submit = new Event("submit", { bubbles: true, cancelable: true });
    document.dispatchEvent(submit);
    expect(submit.defaultPrevented).toBe(true);
    dispatch({
      ...identity,
      type: "composer-preview-zoom",
      token: "forged",
      zoom: 2,
    });
    expect(document.getElementById("document")?.style.zoom).toBe("1");
    dispatch({ ...identity, type: "composer-preview-zoom", zoom: 1.25 });
    expect(document.getElementById("document")?.style.zoom).toBe("1.25");
    dispatch({ ...identity, type: "composer-preview-zoom", zoom: 8 });
    expect(document.getElementById("document")?.style.zoom).toBe("1.25");
    dispatch({ ...identity, type: "composer-preview-diagnostics" });
    expect(
      post.mock.calls.some(
        ([message]) =>
          message.type === "composer-preview-diagnostics" &&
          message.contentPreserved,
      ),
    ).toBe(true);
    post.mockRestore();
  });

  it("loads one PPTX slide with adjacent thumbnails and navigates by revision identity", async () => {
    vi.resetModules();
    document.body.innerHTML = `
      <main id="viewport"><div id="document"></div>
      <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
      <span id="slide-position"></span><button id="previous-slide"></button>
      <button id="next-slide"></button></main>`;
    const target = document.getElementById("document")!;
    const main = {
      slideCount: 4,
      presentation: { slides: 4 },
      renderSlide: vi.fn(async (index: number) => {
        target.innerHTML = `<p><span style="font-family: Arial">• </span>Slide ${index + 1}</p>`;
      }),
      destroy: vi.fn(),
    };
    const thumbnailViewers: PptxViewer[] = [];
    class PptxViewer {
      constructor(private container: HTMLElement) {
        thumbnailViewers.push(this);
      }
      static open = vi.fn(async () => {
        target.innerHTML =
          '<p><span style="font-family: Arial">• </span>Slide 1</p>';
        return main;
      });
      load = vi.fn();
      renderSlide = vi.fn(async (index: number) => {
        this.container.innerHTML = `<span style="font-family: Arial">• </span>Slide ${index + 1}`;
        target.dataset.lastThumbnail = String(index);
      });
      destroy = vi.fn();
    }
    Object.assign(window, {
      ComposerPptx: { PptxViewer, RECOMMENDED_ZIP_LIMITS: {} },
    });
    const normalizedFile = vi.fn();
    const generatedBytes = new Uint8Array([42]).buffer;
    const generateAsync = vi.fn(async () => generatedBytes);
    loadAsync.mockResolvedValueOnce({
      files: Object.fromEntries(
        [
          part("[Content_Types].xml", "<Types/>"),
          part("ppt/presentation.xml", "<presentation/>"),
          part(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:pPr><a:buFont typeface="StarBats"/><a:buChar char="\uf095"/></a:pPr></p:sld>',
          ),
        ].map((entry) => [entry.name, entry]),
      ),
      file: normalizedFile,
      generateAsync,
    });
    await import("../src/composer/preview/frame-client");
    const post = vi
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    const identity = {
      token: "pptx-qualification-token-1234567890",
      draftId: "33333333-3333-3333-3333-333333333333",
      revisionId: "44444444-4444-4444-4444-444444444444",
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
          position: 0,
        },
      }),
    );
    await vi.waitFor(() =>
      expect(
        post.mock.calls.some(
          ([message]) =>
            message.type === "composer-preview-result" &&
            message.status === "ready" &&
            message.token === identity.token,
        ),
      ).toBe(true),
    );
    expect(normalizedFile).toHaveBeenCalledWith(
      "ppt/slides/slide1.xml",
      expect.stringContaining('typeface="Arial"/><a:buChar char="•"'),
    );
    expect(PptxViewer.open).toHaveBeenCalledWith(
      generatedBytes,
      target,
      expect.objectContaining({ renderMode: "slide" }),
    );
    expect(document.getElementById("slide-position")?.textContent).toBe(
      "Slide 1 of 4",
    );
    expect(document.querySelectorAll("#slide-thumbnails button")).toHaveLength(
      2,
    );
    expect(target.textContent).toBe("• Slide 1");
    await vi.waitFor(() =>
      expect(
        [...document.querySelectorAll(".thumbnail-render")].every(
          (item) => !item.textContent?.includes("\uf095"),
        ),
      ).toBe(true),
    );
    document.getElementById("next-slide")!.click();
    await vi.waitFor(() =>
      expect(document.getElementById("slide-position")?.textContent).toBe(
        "Slide 2 of 4",
      ),
    );
    document.getElementById("next-slide")!.click();
    await vi.waitFor(() =>
      expect(document.getElementById("slide-position")?.textContent).toBe(
        "Slide 3 of 4",
      ),
    );
    expect(target.textContent).toBe("• Slide 3");
    expect(main.renderSlide).toHaveBeenCalledWith(2);
    expect(
      thumbnailViewers.some((viewer) => viewer.destroy.mock.calls.length > 0),
    ).toBe(true);
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window.parent,
        origin,
        data: { ...identity, type: "composer-preview-diagnostics" },
      }),
    );
    expect(
      post.mock.calls.some(
        ([message]) =>
          message.type === "composer-preview-diagnostics" &&
          message.position === 2 &&
          message.thumbnails === 3,
      ),
    ).toBe(true);
    main.renderSlide.mockRejectedValueOnce(new Error("Unsupported slide"));
    document.getElementById("next-slide")!.click();
    await vi.waitFor(() =>
      expect(
        post.mock.calls.some(
          ([message]) =>
            message.type === "composer-preview-runtime-error" &&
            message.token === identity.token,
        ),
      ).toBe(true),
    );
    expect(document.getElementById("slide-position")?.textContent).toBe(
      "Slide 3 of 4",
    );
    post.mockRestore();
  });

  it("fails a configured flow-cache budget without exposing document content", async () => {
    vi.resetModules();
    document.body.innerHTML = `
      <main id="viewport"><div id="document"></div>
      <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
      <span id="slide-position"></span><button id="previous-slide"></button>
      <button id="next-slide"></button></main>`;
    loadAsync.mockResolvedValueOnce(archive());
    renderAsync.mockImplementationOnce(async (_bytes, target: HTMLElement) => {
      target.innerHTML =
        '<section class="docx"><article><p>Private content</p></article></section>';
    });
    await import("../src/composer/preview/frame-client");
    const post = vi
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    const token = "budget-qualification-token-1234567890";
    window.dispatchEvent(
      new MessageEvent("message", {
        source: window.parent,
        origin: new URL(document.URL).origin,
        data: {
          type: "composer-preview-render",
          token,
          draftId: "55555555-5555-5555-5555-555555555555",
          revisionId: "66666666-6666-6666-6666-666666666666",
          format: "docx",
          bytes: new Uint8Array([1]).buffer,
          maxSerializedMarkupBytes: 1,
        },
      }),
    );
    await vi.waitFor(() =>
      expect(
        post.mock.calls.some(
          ([message]) =>
            message.type === "composer-preview-result" &&
            message.token === token &&
            message.status === "error",
        ),
      ).toBe(true),
    );
    expect(document.getElementById("document")?.textContent).toBe(
      "This document cannot be shown safely in the native preview.",
    );
    expect(document.getElementById("document")?.textContent).not.toContain(
      "Private content",
    );
    post.mockRestore();
  });
});
