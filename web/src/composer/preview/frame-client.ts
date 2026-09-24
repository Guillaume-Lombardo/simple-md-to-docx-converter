import {
  normalizeDocxTextBoxes,
  windowDocx,
  type DocxWindow,
} from "./docx-window";
import { normalizeDocxLists } from "./docx-lists";

type ZipEntry = {
  dir: boolean;
  name: string;
  _data?: { uncompressedSize?: number };
  async(kind: "uint8array" | "string"): Promise<Uint8Array | string>;
};

type ZipArchive = {
  files: Record<string, ZipEntry>;
  file?(name: string, contents: string): unknown;
  generateAsync?(options: {
    type: "arraybuffer";
    compression: "DEFLATE";
  }): Promise<ArrayBuffer>;
};
type ZipLoader = { loadAsync(bytes: ArrayBuffer): Promise<ZipArchive> };
const validatedExpansion = new WeakMap<ZipArchive, number>();
type DocxRenderer = {
  renderAsync(
    bytes: ArrayBuffer,
    target: HTMLElement,
    styleContainer: undefined,
    options: Record<string, unknown>,
  ): Promise<void>;
};
type PptxRenderer = {
  PptxViewer: {
    new (target: HTMLElement, options?: Record<string, unknown>): PptxInstance;
    open(
      bytes: ArrayBuffer,
      target: HTMLElement,
      options: Record<string, unknown>,
    ): Promise<PptxInstance>;
  };
  RECOMMENDED_ZIP_LIMITS: Record<string, unknown>;
};
type PptxInstance = {
  slideCount: number;
  presentation: unknown;
  load(model: unknown): void;
  renderSlide(index: number): Promise<void>;
  destroy(): void;
};

const libraries = window as unknown as {
  JSZip: ZipLoader;
  docx: DocxRenderer;
  ComposerPptx: PptxRenderer;
};
const parentOrigin = new URL(document.URL).origin;
const viewport = document.getElementById("viewport")!;
const target = document.getElementById("document")!;
const slideControls = document.getElementById("slide-controls")!;
const thumbnailStrip = document.getElementById("slide-thumbnails")!;
const slidePosition = document.getElementById("slide-position")!;
const previousSlide = document.getElementById(
  "previous-slide",
) as HTMLButtonElement;
const nextSlide = document.getElementById("next-slide") as HTMLButtonElement;
const MAX_ARCHIVE_BYTES = 64 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 128 * 1024 * 1024;
const MAX_MEMBER_BYTES = 32 * 1024 * 1024;
const MAX_MEMBERS = 2_000;
const MAX_IMAGE_PIXELS = 16_000_000;
const MAX_TOTAL_IMAGE_PIXELS = 40_000_000;
const MAX_OBSERVER_BATCHES = MAX_MEMBERS / 10;
const MAX_OBSERVED_MUTATIONS = MAX_MEMBERS * 4;
let identity: { token: string; draftId: string; revisionId: string } | null =
  null;
let rendering = false;
let scrollScheduled = false;
let docxWindow: DocxWindow | null = null;
let slideIndex = 0;
let slideCount = 0;
let slideRequest = 0;
const thumbnails = new Map<
  number,
  {
    button: HTMLButtonElement;
    viewer: PptxInstance;
    observer: MutationObserver;
  }
>();
let mainSlideObserver: MutationObserver | null = null;
let pptxFailed = false;
let pptxReadySent = false;

function thumbnailMarkupBytes(): number {
  return [...thumbnails.values()].reduce(
    (total, item) => total + item.button.outerHTML.length * 2,
    0,
  );
}

function totalPreviewMarkupBytes(): number {
  return target.outerHTML.length * 2 + thumbnailMarkupBytes();
}

function assertSlideDomBudget(): void {
  if (
    totalPreviewMarkupBytes() > MAX_MEMBER_BYTES ||
    target.querySelectorAll("*").length +
      thumbnailStrip.querySelectorAll("*").length >
      MAX_MEMBERS
  )
    throw new Error("The presentation preview exceeds its DOM budget.");
}

/** Renderer DOM is observed but never rewritten: vendor redraws on DOM edits. */
export function assertSupportedPptxGlyphs(root: HTMLElement): void {
  if (/[\ue000-\uf8ff]/.test(root.textContent ?? ""))
    throw new Error("The presentation contains an unsupported symbol glyph.");
}

const DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main";
const PPTX_BULLETS = new Map([
  ["starbats:\uf095", "•"],
  ["wingdings:\uf06c", "•"],
  ["symbol:\uf02d", "−"],
]);

/** Normalize only an explicit DrawingML bullet/font pair, before vendor render. */
export function normalizePptxBulletXml(xml: string): string {
  const parsed = new DOMParser().parseFromString(xml, "application/xml");
  if (parsed.querySelector("parsererror"))
    throw new Error("The presentation contains invalid slide XML.");
  let changed = false;
  for (const bullet of parsed.getElementsByTagNameNS(DRAWING_NS, "buChar")) {
    const character = bullet.getAttribute("char") ?? "";
    if (!/[\ue000-\uf8ff]/.test(character)) continue;
    const paragraph = bullet.parentElement;
    if (
      paragraph?.namespaceURI !== DRAWING_NS ||
      !/^(?:pPr|defPPr|lvl[1-9]pPr)$/.test(paragraph.localName)
    )
      throw new Error("The presentation contains an unsupported symbol glyph.");
    const fonts = [...paragraph.children].filter(
      (element) =>
        element.namespaceURI === DRAWING_NS && element.localName === "buFont",
    );
    if (fonts.length !== 1)
      throw new Error("The presentation contains an unsupported symbol glyph.");
    const typeface =
      fonts[0]!.getAttribute("typeface")?.trim().toLowerCase() ?? "";
    const portable = PPTX_BULLETS.get(`${typeface}:${character}`);
    if (!portable)
      throw new Error("The presentation contains an unsupported symbol glyph.");
    bullet.setAttribute("char", portable);
    fonts[0]!.setAttribute("typeface", "Arial");
    changed = true;
  }
  const serialized = new XMLSerializer().serializeToString(parsed);
  if (/[\ue000-\uf8ff]/.test(serialized))
    throw new Error("The presentation contains an unsupported symbol glyph.");
  return changed ? serialized : xml;
}

export function assertNormalizedPptxBudget(
  expandedBytes: number,
  previousBytes: number,
  normalizedBytes: number,
): number {
  const nextExpanded = expandedBytes + normalizedBytes - previousBytes;
  if (normalizedBytes > MAX_MEMBER_BYTES || nextExpanded > MAX_EXPANDED_BYTES)
    throw new Error("The normalized presentation exceeds the preview limit.");
  return nextExpanded;
}

async function pptxPreviewBytes(
  archive: ZipArchive,
  original: ArrayBuffer,
  expandedBytes: number,
): Promise<ArrayBuffer> {
  let changed = false;
  for (const entry of Object.values(archive.files)) {
    if (
      entry.dir ||
      !/^ppt\/(?:slides|slideLayouts|slideMasters)\/[^/]+\.xml$/i.test(
        entry.name,
      )
    )
      continue;
    const xml = (await entry.async("string")) as string;
    const normalized = normalizePptxBulletXml(xml);
    if (normalized === xml) continue;
    if (!archive.file || !archive.generateAsync)
      throw new Error("The presentation cannot normalize its bullet glyphs.");
    const previousBytes = (await entry.async("uint8array")) as Uint8Array;
    const normalizedBytes = new TextEncoder().encode(normalized).byteLength;
    expandedBytes = assertNormalizedPptxBudget(
      expandedBytes,
      previousBytes.byteLength,
      normalizedBytes,
    );
    archive.file(entry.name, normalized);
    changed = true;
  }
  if (!changed) return original;
  const bytes = await archive.generateAsync!({
    type: "arraybuffer",
    compression: "DEFLATE",
  });
  if (bytes.byteLength > MAX_ARCHIVE_BYTES)
    throw new Error("The normalized presentation exceeds the preview limit.");
  return bytes;
}

function assertSafePptxDom(root: HTMLElement): void {
  for (const element of [root, ...root.querySelectorAll("*")]) {
    if (
      /^(SCRIPT|IFRAME|OBJECT|EMBED|FORM|INPUT|BUTTON|LINK|META|BASE|TEMPLATE|FOREIGNOBJECT)$/i.test(
        element.tagName,
      )
    )
      throw new Error("The presentation contains unsupported active markup.");
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim();
      if (
        name.startsWith("on") ||
        ["srcdoc", "srcset", "ping", "formaction", "action", "data"].includes(
          name,
        ) ||
        /@import|expression\s*\(|behavior\s*:|-moz-binding/i.test(value)
      )
        throw new Error("The presentation contains an unsafe attribute.");
      if (name === "href" && element instanceof HTMLAnchorElement) {
        if (
          /[\u0000-\u001f\u007f]/.test(value) ||
          !["http:", "https:", "mailto:"].includes(
            new URL(value, document.baseURI).protocol,
          )
        )
          throw new Error("The presentation contains an unsafe link.");
        continue;
      }
      if (
        ["src", "href", "xlink:href"].includes(name) &&
        !/^(?:#|blob:|data:image\/(?:png|jpeg|gif);base64,)/i.test(value)
      )
        throw new Error("The presentation contains an unsafe resource.");
      const withoutSafeUrls = value.replace(
        /url\s*\(\s*(['"]?)(#|blob:|data:image\/(?:png|jpeg|gif);base64,)[^)]*\1\s*\)/gi,
        "",
      );
      if (/url\s*\(/i.test(withoutSafeUrls))
        throw new Error("The presentation contains an unsafe style URL.");
    }
  }
}

function observePptxDom(
  root: HTMLElement,
  onUnsafe: () => void,
): MutationObserver {
  let batches = 0;
  let mutations = 0;
  const observer = new MutationObserver((records) => {
    batches += 1;
    mutations += records.length;
    try {
      if (batches > MAX_OBSERVER_BATCHES || mutations > MAX_OBSERVED_MUTATIONS)
        throw new Error("The presentation exceeds its update budget.");
      assertSlideDomBudget();
      assertSafePptxDom(root);
      assertSupportedPptxGlyphs(root);
    } catch {
      observer.disconnect();
      onUnsafe();
    }
  });
  observer.observe(root, {
    childList: true,
    characterData: true,
    attributes: true,
    subtree: true,
  });
  return observer;
}

function failVisiblePptx(): void {
  if (pptxFailed) return;
  pptxFailed = true;
  ++slideRequest;
  mainSlideObserver?.disconnect();
  mainSlideObserver = null;
  for (const item of thumbnails.values()) {
    item.observer.disconnect();
    item.button.remove();
    try {
      item.viewer.destroy();
    } catch {
      // The visible target is detached below even if vendor cleanup fails.
    }
  }
  thumbnails.clear();
  try {
    presentation?.destroy();
  } catch {
    // Detaching the target prevents a broken renderer from painting again.
  }
  presentation = null;
  const safe = document.createElement("div");
  safe.id = "document";
  safe.className = "preview-error";
  safe.textContent =
    "This document cannot be shown safely in the native preview.";
  target.replaceWith(safe);
  if (pptxReadySent) send("composer-preview-runtime-error");
}

function send(type: string, extra: Record<string, unknown> = {}): void {
  window.parent.postMessage({ type, ...identity, ...extra }, parentOrigin);
}

export function assertArchivePath(path: string): void {
  if (
    !path ||
    path.startsWith("/") ||
    path.includes("\\") ||
    path.split("/").some((part) => part === ".." || part === ".") ||
    /[\u0000-\u001f]/.test(path)
  ) {
    throw new Error("The Office package contains an unsafe path.");
  }
}

function isImage(path: string): boolean {
  return /\.(png|jpe?g|gif)$/i.test(path);
}

function imageType(path: string): string {
  const suffix = path.split(".").at(-1)?.toLowerCase();
  return suffix === "jpg" || suffix === "jpeg"
    ? "image/jpeg"
    : `image/${suffix}`;
}

export function validImageSignature(path: string, data: Uint8Array): boolean {
  const suffix = path.split(".").at(-1)?.toLowerCase();
  if (suffix === "png")
    return [137, 80, 78, 71, 13, 10, 26, 10].every(
      (byte, index) => data[index] === byte,
    );
  if (suffix === "jpg" || suffix === "jpeg")
    return data[0] === 255 && data[1] === 216 && data[2] === 255;
  if (suffix === "gif")
    return new TextDecoder().decode(data.subarray(0, 6)) === "GIF89a";
  return false;
}

export function imagePixels(path: string, data: Uint8Array): number {
  const suffix = path.split(".").at(-1)?.toLowerCase();
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (suffix === "png" && data.byteLength >= 24)
    return view.getUint32(16) * view.getUint32(20);
  if (suffix === "gif" && data.byteLength >= 10)
    return view.getUint16(6, true) * view.getUint16(8, true);
  if (suffix === "jpg" || suffix === "jpeg") {
    let offset = 2;
    while (offset + 9 < data.byteLength) {
      if (data[offset] !== 255) break;
      const marker = data[offset + 1]!;
      if (marker === 255) {
        offset += 1;
        continue;
      }
      if (marker === 0xd9 || marker === 0xda) break;
      const length = view.getUint16(offset + 2);
      if (length < 2 || offset + 2 + length > data.byteLength) break;
      if (
        [
          0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd,
          0xce, 0xcf,
        ].includes(marker)
      )
        return view.getUint16(offset + 7) * view.getUint16(offset + 5);
      offset += 2 + length;
    }
  }
  return 0;
}

async function validateImage(path: string, data: Uint8Array): Promise<number> {
  if (!validImageSignature(path, data))
    throw new Error("The Office package contains an invalid image.");
  const pixels = imagePixels(path, data);
  if (pixels < 1 || pixels > MAX_IMAGE_PIXELS)
    throw new Error("The Office package contains an oversized image.");
  const blob = new Blob([new Uint8Array(data)], { type: imageType(path) });
  const bitmap = await createImageBitmap(blob);
  try {
    if (bitmap.width * bitmap.height !== pixels)
      throw new Error("The Office image dimensions are inconsistent.");
    if (bitmap.width * bitmap.height > MAX_IMAGE_PIXELS)
      throw new Error("The Office package contains an oversized image.");
  } finally {
    bitmap.close();
  }
  return pixels;
}

export function validateRelationships(xml: string): void {
  const parsed = new DOMParser().parseFromString(xml, "application/xml");
  if (parsed.querySelector("parsererror"))
    throw new Error("The Office package has invalid relationships.");
  for (const relation of parsed.getElementsByTagNameNS("*", "Relationship")) {
    if (
      relation.getAttribute("TargetMode")?.toLowerCase() === "external" &&
      !relation.getAttribute("Type")?.endsWith("/hyperlink")
    ) {
      throw new Error("The Office package contains an external resource.");
    }
  }
}

export async function validateOffice(
  bytes: ArrayBuffer,
  format: string,
): Promise<ZipArchive> {
  if (bytes.byteLength === 0 || bytes.byteLength > MAX_ARCHIVE_BYTES)
    throw new Error("The Office file exceeds the preview limit.");
  const archive = await libraries.JSZip.loadAsync(bytes);
  const files = Object.values(archive.files).filter((entry) => !entry.dir);
  if (files.length === 0 || files.length > MAX_MEMBERS)
    throw new Error("The Office package has too many parts.");
  const seen = new Set<string>();
  let expanded = 0;
  let imagePixelsTotal = 0;
  for (const entry of files) {
    assertArchivePath(entry.name);
    const normalized = entry.name.toLowerCase();
    if (seen.has(normalized))
      throw new Error("The Office package contains duplicate paths.");
    seen.add(normalized);
    if (/\.(svg|svgz|html?|xhtml|js|mjs|wasm)$/i.test(entry.name))
      throw new Error(
        "The Office package contains unsupported active content.",
      );
    if (/\/(embeddings|activex|oleobject|macros)\//i.test(entry.name))
      throw new Error(
        "The Office package contains unsupported embedded content.",
      );
    const advertised = entry._data?.uncompressedSize;
    if (
      !Number.isSafeInteger(advertised) ||
      advertised! < 0 ||
      advertised! > MAX_MEMBER_BYTES ||
      expanded + advertised! > MAX_EXPANDED_BYTES
    )
      throw new Error("The Office package expands beyond the preview limit.");
    const data = (await entry.async("uint8array")) as Uint8Array;
    expanded += data.byteLength;
    if (data.byteLength > MAX_MEMBER_BYTES || expanded > MAX_EXPANDED_BYTES)
      throw new Error("The Office package expands beyond the preview limit.");
    if (/\.(xml|rels)$/i.test(entry.name)) {
      const xml = new TextDecoder("utf-8", { fatal: true }).decode(data);
      if (/<!\s*(DOCTYPE|ENTITY)/i.test(xml))
        throw new Error("The Office package contains unsupported XML.");
      if (/\.rels$/i.test(entry.name)) validateRelationships(xml);
    }
    if (/\/(media|images)\//i.test(entry.name)) {
      if (!isImage(entry.name))
        throw new Error("The Office package contains unsupported media.");
      imagePixelsTotal += await validateImage(entry.name, data);
      if (imagePixelsTotal > MAX_TOTAL_IMAGE_PIXELS)
        throw new Error("The Office package contains too many image pixels.");
    }
  }
  if (
    !seen.has("[content_types].xml") ||
    !seen.has(format === "docx" ? "word/document.xml" : "ppt/presentation.xml")
  ) {
    throw new Error("The Office package is missing its main document.");
  }
  validatedExpansion.set(archive, expanded);
  return archive;
}

function matchesIdentity(message: Record<string, unknown>): boolean {
  return Boolean(
    identity &&
    message.token === identity.token &&
    message.draftId === identity.draftId &&
    message.revisionId === identity.revisionId,
  );
}

function documentOwnedAction(event: Event): boolean {
  const element =
    event.target instanceof Element
      ? event.target
      : event.target instanceof Node
        ? event.target.parentElement
        : null;
  return Boolean(
    element?.closest(
      "#document a, #document button, #document form, #slide-thumbnails a",
    ),
  );
}

function stopDocumentAction(event: Event): void {
  if (!documentOwnedAction(event)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
}

for (const type of ["click", "auxclick", "contextmenu"])
  document.addEventListener(type, stopDocumentAction, true);
document.addEventListener(
  "keydown",
  (event) => {
    if (event instanceof KeyboardEvent && ["Enter", " "].includes(event.key))
      stopDocumentAction(event);
  },
  true,
);
document.addEventListener("submit", (event) => event.preventDefault(), true);
viewport.addEventListener("scroll", () => {
  if (scrollScheduled || !identity) return;
  scrollScheduled = true;
  requestAnimationFrame(() => {
    scrollScheduled = false;
    try {
      docxWindow?.update();
    } catch {
      target.replaceChildren();
      target.textContent =
        "This document cannot be shown safely in the native preview.";
      send("composer-preview-runtime-error");
      return;
    }
    send("composer-preview-position", {
      position: presentation ? slideIndex : viewport.scrollTop,
    });
  });
});

function updateSlideControls(): void {
  slideControls.style.display = "flex";
  slidePosition.textContent = `Slide ${slideIndex + 1} of ${slideCount}`;
  previousSlide.disabled = slideIndex === 0;
  nextSlide.disabled = slideIndex >= slideCount - 1;
  for (const [index, item] of thumbnails)
    item.button.setAttribute(
      "aria-current",
      index === slideIndex ? "page" : "false",
    );
}

function preloadSlides(): void {
  if (!presentation?.presentation) return;
  thumbnailStrip.style.display = "flex";
  const desired = new Set(
    [slideIndex - 1, slideIndex, slideIndex + 1].filter(
      (index) => index >= 0 && index < slideCount,
    ),
  );
  for (const [index, item] of thumbnails) {
    if (desired.has(index)) continue;
    item.observer.disconnect();
    item.viewer.destroy();
    item.button.remove();
    thumbnails.delete(index);
  }
  for (const index of desired) {
    if (thumbnails.has(index)) continue;
    try {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "slide-thumbnail";
      button.setAttribute("aria-label", `Show slide ${index + 1}`);
      const container = document.createElement("div");
      container.className = "thumbnail-render";
      button.appendChild(container);
      button.addEventListener("click", () => void moveSlide(index));
      thumbnailStrip.appendChild(button);
      const viewer = new libraries.ComposerPptx.PptxViewer(container, {
        width: 1058,
        lazyMedia: true,
        lazySlides: true,
      });
      viewer.load(presentation.presentation);
      const observer = observePptxDom(container, () => {
        if (thumbnails.get(index)?.viewer !== viewer) return;
        viewer.destroy();
        button.remove();
        thumbnails.delete(index);
      });
      thumbnails.set(index, { button, viewer, observer });
      void viewer
        .renderSlide(index)
        .then(() => {
          if (thumbnails.get(index)?.viewer !== viewer) return;
          assertSafePptxDom(container);
          assertSupportedPptxGlyphs(container);
          if (
            totalPreviewMarkupBytes() > MAX_MEMBER_BYTES ||
            target.querySelectorAll("*").length +
              thumbnailStrip.querySelectorAll("*").length >
              MAX_MEMBERS
          ) {
            viewer.destroy();
            observer.disconnect();
            button.remove();
            thumbnails.delete(index);
          }
        })
        .catch(() => {
          if (thumbnails.get(index)?.viewer === viewer)
            container.textContent = `Slide ${index + 1} thumbnail unavailable`;
        });
    } catch {
      thumbnails.get(index)?.observer.disconnect();
      thumbnails.get(index)?.viewer.destroy();
      thumbnails.get(index)?.button.remove();
      thumbnails.delete(index);
    }
  }
  updateSlideControls();
}

async function moveSlide(index: number): Promise<void> {
  if (!presentation || index < 0 || index >= slideCount) return;
  const request = ++slideRequest;
  try {
    await presentation.renderSlide(index);
    assertSafePptxDom(target);
    assertSupportedPptxGlyphs(target);
    assertSlideDomBudget();
  } catch {
    if (request === slideRequest) send("composer-preview-runtime-error");
    return;
  }
  if (request !== slideRequest) return;
  slideIndex = index;
  updateSlideControls();
  preloadSlides();
  viewport.scrollTop = 0;
  send("composer-preview-position", { position: index });
}

let presentation: PptxInstance | null = null;
previousSlide.addEventListener("click", () => void moveSlide(slideIndex - 1));
nextSlide.addEventListener("click", () => void moveSlide(slideIndex + 1));

window.addEventListener("message", async (event: MessageEvent) => {
  if (event.source !== window.parent || event.origin !== parentOrigin) return;
  const message = event.data as Record<string, unknown> | null;
  if (!message || typeof message !== "object") return;
  if (message.type === "composer-preview-ping" && !identity) {
    window.parent.postMessage({ type: "composer-preview-ready" }, parentOrigin);
    return;
  }
  if (
    message.type === "composer-preview-diagnostics" &&
    matchesIdentity(message)
  ) {
    send("composer-preview-diagnostics", {
      contentPreserved: docxWindow?.contentPreserved(),
      mountedFlowUnits: docxWindow?.mountedUnits(),
      detachedBlockNodes: docxWindow?.detachedBlockNodes(),
      serializedMarkupBytes: docxWindow?.serializedMarkupBytes,
      liveDomNodes: document.getElementsByTagName("*").length,
      position: presentation ? slideIndex : viewport.scrollTop,
      thumbnails: thumbnails.size,
      thumbnailMarkupBytes: thumbnailMarkupBytes(),
      totalPreviewMarkupBytes: totalPreviewMarkupBytes(),
    });
    return;
  }
  if (message.type === "composer-preview-zoom" && matchesIdentity(message)) {
    if (
      typeof message.zoom === "number" &&
      message.zoom >= 0.5 &&
      message.zoom <= 2
    )
      target.style.zoom = String(message.zoom);
    return;
  }
  if (message.type !== "composer-preview-render" || rendering || identity)
    return;
  if (
    typeof message.token !== "string" ||
    message.token.length < 20 ||
    typeof message.draftId !== "string" ||
    typeof message.revisionId !== "string" ||
    !["docx", "pptx"].includes(String(message.format)) ||
    !(message.bytes instanceof ArrayBuffer)
  )
    return;
  identity = {
    token: message.token,
    draftId: message.draftId,
    revisionId: message.revisionId,
  };
  rendering = true;
  const started = performance.now();
  const memory = performance as Performance & {
    memory?: { usedJSHeapSize: number };
  };
  const heapBefore = memory.memory?.usedJSHeapSize ?? null;
  let sampledHeapHighWater = heapBefore;
  const sample = setInterval(() => {
    const current = memory.memory?.usedJSHeapSize;
    if (current !== undefined)
      sampledHeapHighWater = Math.max(sampledHeapHighWater ?? 0, current);
  }, 5);
  let fullDomNodes: number | undefined;
  let heapAfterFull: number | null = null;
  try {
    const archive = await validateOffice(message.bytes, String(message.format));
    if (message.format === "docx") {
      await libraries.docx.renderAsync(message.bytes, target, undefined, {
        useBase64URL: true,
        renderAltChunks: false,
      });
      normalizeDocxTextBoxes(target);
      const numbering = archive.files["word/numbering.xml"];
      if (numbering)
        normalizeDocxLists(target, (await numbering.async("string")) as string);
      fullDomNodes = document.getElementsByTagName("*").length;
      heapAfterFull = memory.memory?.usedJSHeapSize ?? null;
      if (heapAfterFull !== null)
        sampledHeapHighWater = Math.max(
          sampledHeapHighWater ?? 0,
          heapAfterFull,
        );
      const configuredBudget = message.maxSerializedMarkupBytes;
      const markupBudget =
        typeof configuredBudget === "number" &&
        Number.isSafeInteger(configuredBudget) &&
        configuredBudget > 0
          ? Math.min(configuredBudget, MAX_MEMBER_BYTES)
          : MAX_MEMBER_BYTES;
      docxWindow = windowDocx(target, viewport, markupBudget);
    } else {
      const expandedBytes = validatedExpansion.get(archive);
      if (expandedBytes === undefined)
        throw new Error("The presentation was not validated.");
      const previewBytes = await pptxPreviewBytes(
        archive,
        message.bytes,
        expandedBytes,
      );
      presentation = await libraries.ComposerPptx.PptxViewer.open(
        previewBytes,
        target,
        {
          width: Math.max(1, viewport.clientWidth - 40),
          zipLimits: libraries.ComposerPptx.RECOMMENDED_ZIP_LIMITS,
          lazySlides: true,
          lazyMedia: true,
          scrollContainer: viewport,
          renderMode: "slide",
        },
      );
      slideCount = presentation.slideCount;
      if (slideCount < 1) throw new Error("The presentation has no slides.");
      slideIndex = Math.min(
        slideCount - 1,
        typeof message.position === "number"
          ? Math.max(0, Math.trunc(message.position))
          : 0,
      );
      if (slideIndex > 0) await presentation.renderSlide(slideIndex);
      assertSlideDomBudget();
      assertSafePptxDom(target);
      assertSupportedPptxGlyphs(target);
      mainSlideObserver = observePptxDom(target, failVisiblePptx);
      updateSlideControls();
      preloadSlides();
    }
    if (
      typeof message.zoom === "number" &&
      message.zoom >= 0.5 &&
      message.zoom <= 2
    )
      target.style.zoom = String(message.zoom);
    if (
      message.format === "docx" &&
      typeof message.position === "number" &&
      message.position >= 0
    )
      viewport.scrollTop = message.position;
    docxWindow?.update();
    if (pptxFailed) throw new Error("The presentation preview is unavailable.");
    const heapAfterWindow = memory.memory?.usedJSHeapSize ?? null;
    if (heapAfterWindow !== null)
      sampledHeapHighWater = Math.max(
        sampledHeapHighWater ?? 0,
        heapAfterWindow,
      );
    send("composer-preview-result", {
      status: "ready",
      renderMs: Math.round(performance.now() - started),
      visibleUnits:
        message.format === "docx"
          ? target.querySelectorAll("section").length
          : 1,
      totalUnits: message.format === "pptx" ? slideCount : undefined,
      flowUnits: docxWindow?.totalUnits,
      mountedFlowUnits: docxWindow?.mountedUnits(),
      contentPreserved: docxWindow?.contentPreserved(),
      fullDomNodes,
      liveDomNodes: document.getElementsByTagName("*").length,
      retainedBlockNodes: docxWindow?.totalBlockNodes,
      detachedBlockNodes: docxWindow?.detachedBlockNodes(),
      serializedMarkupBytes: docxWindow?.serializedMarkupBytes,
      heapBefore,
      heapAfterFull,
      heapAfterWindow,
      sampledHeapHighWater,
      thumbnails: thumbnails.size,
      thumbnailMarkupBytes: thumbnailMarkupBytes(),
      totalPreviewMarkupBytes: totalPreviewMarkupBytes(),
    });
    if (message.format === "pptx") pptxReadySent = true;
  } catch {
    target.replaceChildren();
    target.className = "preview-error";
    target.textContent =
      "This document cannot be shown safely in the native preview.";
    send("composer-preview-result", { status: "error" });
  }
  clearInterval(sample);
});

window.parent.postMessage({ type: "composer-preview-ready" }, parentOrigin);
