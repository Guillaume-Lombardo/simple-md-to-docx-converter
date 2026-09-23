import { beforeAll, describe, expect, it, vi } from "vitest";

type FrameClient = typeof import("../src/composer/preview/frame-client");
type Part = {
  dir: boolean;
  name: string;
  _data: { uncompressedSize: number };
  async(kind: "uint8array" | "string"): Promise<Uint8Array | string>;
};

const encoder = new TextEncoder();
const bytes = new Uint8Array([1]).buffer;

function part(name: string, value: string | Uint8Array): Part {
  const content = typeof value === "string" ? encoder.encode(value) : value;
  return {
    dir: false,
    name,
    _data: { uncompressedSize: content.byteLength },
    async: async (kind) =>
      kind === "string" ? new TextDecoder().decode(content) : content,
  };
}

function office(parts: Part[] = [], format: "docx" | "pptx" = "docx") {
  const base = [
    part("[Content_Types].xml", "<Types/>"),
    part(
      format === "docx" ? "word/document.xml" : "ppt/presentation.xml",
      "<document/>",
    ),
  ];
  return {
    files: Object.fromEntries(
      [...base, ...parts].map((item) => [item.name, item]),
    ),
  };
}

function png(width: number, height: number): Uint8Array {
  const result = new Uint8Array(24);
  result.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const view = new DataView(result.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return result;
}

describe("Office preview security and recovery", () => {
  let client: FrameClient;
  let loadAsync: ReturnType<typeof vi.fn>;
  let renderAsync: ReturnType<typeof vi.fn>;
  let bitmap: ReturnType<typeof vi.fn>;

  beforeAll(async () => {
    document.body.innerHTML = `
      <main id="viewport"><div id="document"></div>
      <nav id="slide-controls"></nav><nav id="slide-thumbnails"></nav>
      <span id="slide-position"></span><button id="previous-slide"></button>
      <button id="next-slide"></button></main>`;
    loadAsync = vi.fn().mockResolvedValue(office());
    renderAsync = vi.fn(async (_source: ArrayBuffer, target: HTMLElement) => {
      target.innerHTML =
        '<section class="docx"><p>Trusted preview</p></section>';
    });
    bitmap = vi.fn();
    vi.stubGlobal("createImageBitmap", bitmap);
    Object.assign(window, {
      JSZip: { loadAsync },
      docx: { renderAsync },
      ComposerPptx: { PptxViewer: {}, RECOMMENDED_ZIP_LIMITS: {} },
    });
    client = await import("../src/composer/preview/frame-client");
  });

  it("rejects malformed XML, untrusted media and archive limits before rendering", async () => {
    const invalidUtf8 = part("word/header.xml", Uint8Array.from([0xff]));
    const entity = part(
      "word/header.xml",
      "<!ENTITY x SYSTEM 'file:///etc/passwd'>",
    );
    const malformedRelationship = part(
      "word/_rels/document.xml.rels",
      "<Relationships><Relationship",
    );
    const unknownSize = part("word/header.xml", "<header/>");
    unknownSize._data.uncompressedSize = Number.NaN;
    const cases: Array<[ReturnType<typeof office>, RegExp]> = [
      [office([invalidUtf8]), /encoded|UTF-8|valid/i],
      [office([entity]), /unsupported XML/],
      [office([malformedRelationship]), /invalid relationships/],
      [office([part("word/images/evil.webp", "RIFF")]), /unsupported media/],
      [office([unknownSize]), /expands beyond/],
      [office([part("word/media/zero.png", png(0, 5))]), /oversized image/],
      [
        office([part("word/media/huge.png", png(5_000, 5_000))]),
        /oversized image/,
      ],
    ];
    for (const [archive, reason] of cases) {
      loadAsync.mockResolvedValueOnce(archive);
      await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
        reason,
      );
    }
    expect(bitmap).not.toHaveBeenCalled();
    await expect(
      client.validateOffice(new ArrayBuffer(64 * 1024 * 1024 + 1), "docx"),
    ).rejects.toThrow("preview limit");
    expect(renderAsync).not.toHaveBeenCalled();
  });

  it("rejects raster metadata that disagrees with decoding and closes the bitmap", async () => {
    const close = vi.fn();
    bitmap.mockResolvedValueOnce({ width: 2, height: 2, close });
    loadAsync.mockResolvedValueOnce(
      office([part("word/media/image.png", png(1, 1))]),
    );
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "dimensions are inconsistent",
    );
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("caps aggregate raster pixels even when individual images are allowed", async () => {
    const closes = [vi.fn(), vi.fn(), vi.fn()];
    for (const close of closes)
      bitmap.mockResolvedValueOnce({ width: 3_000, height: 5_000, close });
    loadAsync.mockResolvedValueOnce(
      office(
        [1, 2, 3].map((index) =>
          part(`word/media/image${index}.png`, png(3_000, 5_000)),
        ),
      ),
    );
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "too many image pixels",
    );
    expect(closes.every((close) => close.mock.calls.length === 1)).toBe(true);
  });

  it("rejects a corrupt JPEG image instead of trusting its file extension", async () => {
    const jpeg = Uint8Array.from([255, 216, 255, 217]);
    expect(client.validImageSignature("image.jpg", jpeg)).toBe(true);
    expect(client.imagePixels("image.jpg", jpeg)).toBe(0);
    loadAsync.mockResolvedValueOnce(
      office([part("word/media/image.jpg", jpeg)]),
    );
    await expect(client.validateOffice(bytes, "docx")).rejects.toThrow(
      "oversized image",
    );
  });

  it("ignores forged and stale frame commands, then confines a renderer error", async () => {
    const post = vi
      .spyOn(window.parent, "postMessage")
      .mockImplementation(() => {});
    const identity = {
      token: "security-recovery-token-123456789",
      draftId: "draft-security",
      revisionId: "revision-security",
    };
    const render = {
      ...identity,
      type: "composer-preview-render",
      format: "docx",
      bytes,
      zoom: 1,
    };
    const dispatch = (
      data: Record<string, unknown>,
      source: MessageEventSource | null = window.parent,
    ) =>
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: new URL(document.URL).origin,
          source,
          data,
        }),
      );
    dispatch(render, window);
    dispatch({ ...render, bytes: new Uint8Array([1]) });
    dispatch({ ...render, format: "pdf" });
    expect(renderAsync).not.toHaveBeenCalled();

    renderAsync.mockRejectedValueOnce(new Error("private renderer failure"));
    dispatch(render);
    await vi.waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "composer-preview-result",
          ...identity,
          status: "error",
        }),
        new URL(document.URL).origin,
      ),
    );
    expect(document.getElementById("document")?.textContent).toBe(
      "This document cannot be shown safely in the native preview.",
    );
    expect(document.body.textContent).not.toContain("private renderer failure");
    dispatch({ ...render, token: "another-valid-token-123456789" });
    dispatch({
      ...identity,
      type: "composer-preview-zoom",
      revisionId: "stale",
      zoom: 2,
    });
    dispatch({
      ...identity,
      type: "composer-preview-diagnostics",
      draftId: "forged",
    });
    expect(renderAsync).toHaveBeenCalledTimes(1);
    expect(document.getElementById("document")?.style.zoom).toBe("");
    expect(
      post.mock.calls.filter(
        ([value]) => value.type === "composer-preview-diagnostics",
      ),
    ).toHaveLength(0);
    post.mockRestore();
  });
});
