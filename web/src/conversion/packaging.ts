import JSZip from "jszip";
import MarkdownIt, {
  type MarkdownIt as Parser,
  type StateBlock,
  type Token,
} from "markdown-it";
import footnote from "markdown-it-footnote";
import { isAlias, isMap, isScalar, isSeq, parseDocument } from "yaml";

/**
 * Client-side packaging of a Markdown file with its referenced local images
 * (T104, specification decision D2). The browser places the selected files into
 * the approved archive layout before upload. The service still applies every
 * archive, path, image, scanning, and limit rule; these checks only give early
 * feedback with the service's stable validation messages.
 *
 * Image references are found with markdown-it, the original of the service's
 * markdown-it-py parser, configured like the service: the CommonMark preset with
 * HTML tokens, every link destination tokenized, footnotes, and the service's
 * front-matter rule. YAML metadata scalars are parsed as Markdown like the
 * service does. Intentional difference: the browser's YAML 1.2
 * parser is stricter than the service's PyYAML (for example an unquoted value
 * starting with "![" is an unseparated tag). When it reports errors, image
 * references in the scalars it still recovers, with full YAML semantics, are
 * optional: packaged when the file is selected, never an early error. The client is therefore never
 * stricter than the service and never omits a selected image the service may
 * need; the service stays authoritative. The shared fixture tests/fixtures/markdown-image-references.json
 * proves parity with the service parser.
 */

export const ENTRYPOINT = "document.md";
export const SUPPORTED_IMAGE_SUFFIXES = new Set([
  ".gif",
  ".jpeg",
  ".jpg",
  ".png",
  ".svg",
  ".webp",
]);

// Stable messages shared with the service's archive and Markdown validation.
export const INVALID_IMAGE_PATH = "Markdown image path is invalid.";
export const ESCAPING_IMAGE_PATH = "Markdown image path escapes the archive.";
export const MISSING_IMAGE = "Markdown image is missing or unapproved.";
export const INVALID_ARCHIVE = "Document archive is invalid.";
export const AMBIGUOUS_ENTRYPOINT =
  "Document archive has no unambiguous Markdown entrypoint.";
export const INVALID_UTF8 = "Document Markdown is not valid UTF-8.";
export const MIXED_ARCHIVE_SELECTION =
  "Choose one ZIP package, or one Markdown file with its images.";

const REMOTE_RESOURCE = /^(?:[a-z][a-z0-9+.-]*(?::|%3a)|\/\/|%2f%2f)/i;
const AMBIGUOUS_ESCAPE = /%(?:2e|2f|5c)/i;
const MAX_URL_DECODE_PASSES = 2;

const DOCUMENT = resourceParser().use(frontMatter);
const METADATA = resourceParser();

function resourceParser(): Parser {
  const parser = new MarkdownIt("commonmark", { html: true }).use(footnote);
  // Tokenize every destination so validation, not the parser, decides policy.
  parser.validateLink = () => true;
  return parser;
}

/** The service's front-matter block rule (mdit-py-plugins), ported exactly. */
function frontMatter(parser: Parser): void {
  parser.block.ruler.before("table", "front_matter", frontMatterRule, {
    alt: ["paragraph", "reference", "blockquote", "list"],
  });
}

function frontMatterRule(
  state: StateBlock,
  startLine: number,
  endLine: number,
  silent: boolean,
): boolean {
  const marker = "-";
  let start = state.bMarks[startLine]! + state.tShift[startLine]!;
  let maximum = state.eMarks[startLine]!;
  if (startLine !== 0 || state.src[0] !== marker) return false;
  let position = start + 1;
  while (position <= maximum && position < state.src.length) {
    if (state.src[position] !== marker) break;
    position += 1;
  }
  const markerCount = position - start;
  if (markerCount < 3) return false;
  if (silent) return true;
  let nextLine = startLine;
  let autoClosed = false;
  for (;;) {
    nextLine += 1;
    if (nextLine >= endLine) return false;
    if (state.src.slice(start, maximum) === "...") break;
    start = state.bMarks[nextLine]! + state.tShift[nextLine]!;
    maximum = state.eMarks[nextLine]!;
    if (start < maximum && state.sCount[nextLine]! < state.blkIndent) break;
    if (state.src[start] !== marker) continue;
    if (state.sCount[nextLine]! - state.blkIndent >= 4) continue;
    position = start + 1;
    while (position < maximum) {
      if (state.src[position] !== marker) break;
      position += 1;
    }
    if (position - start < markerCount) continue;
    position = state.skipSpaces(position);
    if (position < maximum) continue;
    autoClosed = true;
    break;
  }
  const oldParent = state.parentType;
  const oldLineMax = state.lineMax;
  state.parentType = "container" as typeof state.parentType;
  state.lineMax = nextLine;
  const token = state.push("front_matter", "", 0);
  token.hidden = true;
  token.markup = marker.repeat(3);
  token.content = state.src.slice(
    state.bMarks[startLine + 1],
    state.eMarks[nextLine - 1],
  );
  token.block = true;
  state.parentType = oldParent;
  state.lineMax = oldLineMax;
  state.line = nextLine + (autoClosed ? 1 : 0);
  token.map = [startLine, state.line];
  return true;
}

function walk(tokens: Token[]): Token[] {
  const all: Token[] = [];
  for (const token of tokens) {
    all.push(token);
    if (token.children) all.push(...walk(token.children));
  }
  return all;
}

function imageSources(tokens: Token[]): string[] {
  return tokens
    .filter((token) => token.type === "image")
    .map((token) => String(token.attrGet("src") ?? ""));
}

/**
 * Yield YAML scalar values in the service's traversal order. Like PyYAML's
 * compose, this reads the node graph without constructing tagged values, keeps
 * duplicate keys, and visits an aliased node once. When the parser reports
 * errors, the recovered nodes still carry real YAML scalar semantics (block,
 * folded, quoted, escaped), but the result is marked incomplete.
 */
function metadataScalars(metadata: string): {
  scalars: string[];
  complete: boolean;
} {
  const document = parseDocument(metadata, { uniqueKeys: false });
  const scalars: string[] = [];
  const pending: unknown[] = [document.contents];
  const seen = new Set<unknown>();
  while (pending.length) {
    let node = pending.pop();
    if (isAlias(node)) node = node.resolve(document);
    if (!node || seen.has(node)) continue;
    seen.add(node);
    if (isScalar(node)) {
      if (typeof node.value === "string") scalars.push(node.value);
    } else if (isSeq(node)) {
      pending.push(...node.items);
    } else if (isMap(node)) {
      for (const pair of node.items) pending.push(pair.key, pair.value);
    }
  }
  return { scalars, complete: document.errors.length === 0 };
}

export class PackageError extends Error {
  readonly code = "validation";
}

export type SelectedFile = { file: File; path: string };

export type PreparedSource = {
  file: File;
  imageCount: number;
  markdownName?: string;
};

/**
 * Return every image src the service validates, in the service's order: body
 * images first, then images in YAML metadata scalars.
 */
export function imageDestinations(markdown: string): string[] {
  return imageReferences(markdown).required;
}

/**
 * Split references into those the service certainly validates (`required`)
 * and best-effort candidates (`optional`). Optional references come only from
 * metadata in which the browser's YAML parser reports errors although the
 * service may accept it: they are packaged when present, never block upload.
 */
export function imageReferences(markdown: string): {
  required: string[];
  optional: string[];
} {
  const tokens = walk(DOCUMENT.parse(markdown, {}));
  const required = imageSources(tokens);
  const optional: string[] = [];
  for (const token of tokens) {
    if (token.type !== "front_matter") continue;
    const { scalars, complete } = metadataScalars(token.content);
    for (const scalar of scalars)
      (complete ? required : optional).push(
        ...imageSources(walk(METADATA.parse(scalar, {}))),
      );
  }
  return { required, optional };
}

/**
 * Resolve one destination from the archive-root entrypoint. Remote destinations
 * return undefined: the service rejects them and the client never fetches them.
 */
export function resolveImagePath(destination: string): string | undefined {
  if (isRemote(destination)) return undefined;
  const fragmentAt = destination.indexOf("#");
  const beforeFragment =
    fragmentAt < 0 ? destination : destination.slice(0, fragmentAt);
  const queryAt = beforeFragment.indexOf("?");
  const path = queryAt < 0 ? beforeFragment : beforeFragment.slice(0, queryAt);
  const query = queryAt < 0 ? "" : beforeFragment.slice(queryAt + 1);
  const fragment = fragmentAt < 0 ? "" : destination.slice(fragmentAt + 1);
  if (query || fragment || AMBIGUOUS_ESCAPE.test(destination))
    throw new PackageError(INVALID_IMAGE_PATH);
  const decoded = percentDecode(path, true);
  if (
    decoded === undefined ||
    percentDecode(decoded, false) !== decoded ||
    !decoded ||
    decoded.includes("\0") ||
    decoded.includes("\\") ||
    decoded.startsWith("/") ||
    decoded.includes("?") ||
    decoded.includes("#")
  )
    throw new PackageError(INVALID_IMAGE_PATH);
  const parts = decoded.split("/").filter((part) => part !== "");
  if (parts[0]?.includes(":")) throw new PackageError(INVALID_IMAGE_PATH);
  const resolved: string[] = [];
  for (const part of parts) {
    if (part === ".") continue;
    if (part === "..") {
      if (resolved.length === 0) throw new PackageError(ESCAPING_IMAGE_PATH);
      resolved.pop();
      continue;
    }
    resolved.push(part);
  }
  if (resolved.length === 0) throw new PackageError(MISSING_IMAGE);
  return resolved.join("/");
}

/**
 * Turn a browser selection into one upload. A single file is returned
 * unchanged. Several files, or a folder, are packaged when the selected
 * Markdown references local images.
 */
export async function prepareSelection(
  entries: SelectedFile[],
): Promise<PreparedSource> {
  if (entries.length === 1) return { file: entries[0]!.file, imageCount: 0 };
  const selection = rootedSelection(entries);
  if (selection.some((entry) => hasSuffix(entry.path, ".zip")))
    throw new PackageError(MIXED_ARCHIVE_SELECTION);
  const markdown =
    selection.find((entry) => entry.path === ENTRYPOINT) ??
    soleMarkdown(selection);
  const markdownBytes = new Uint8Array(await markdown.file.arrayBuffer());
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(markdownBytes);
  } catch {
    throw new PackageError(INVALID_UTF8);
  }
  const markdownDirectory = markdown.path.split("/").slice(0, -1);
  const scoped = new Map<string, File>();
  for (const entry of selection) {
    const parts = entry.path.split("/");
    if (
      entry !== markdown &&
      markdownDirectory.every((part, index) => parts[index] === part)
    )
      scoped.set(parts.slice(markdownDirectory.length).join("/"), entry.file);
  }
  const flat = [...scoped.keys()].every((path) => !path.includes("/"));
  const images = new Map<string, File>();
  const keys = new Set<string>();
  const references = imageReferences(text);
  const add = (destination: string, required: boolean) => {
    let path: string | undefined;
    try {
      path = resolveImagePath(destination);
    } catch (error) {
      if (required) throw error;
      return;
    }
    if (path === undefined || images.has(path)) return;
    const file = hasImageSuffix(path)
      ? (scoped.get(path) ?? (flat ? byName(scoped, path) : undefined))
      : undefined;
    if (!file) {
      if (required) throw new PackageError(MISSING_IMAGE);
      return;
    }
    const key = path.normalize("NFC").toLowerCase();
    if (keys.has(key)) {
      if (required) throw new PackageError(INVALID_ARCHIVE);
      return;
    }
    keys.add(key);
    images.set(path, file);
  };
  for (const destination of references.required) add(destination, true);
  for (const destination of references.optional) add(destination, false);
  if (images.size === 0) return { file: markdown.file, imageCount: 0 };
  const stem = markdown.file.name.replace(/\.md$/i, "") || "document";
  const content = await buildPackage(markdownBytes, images);
  return {
    file: new File([content], `${stem}.zip`, { type: "application/zip" }),
    imageCount: images.size,
    markdownName: markdown.file.name,
  };
}

/** Build a deterministic stored ZIP with root document.md and images. */
export async function buildPackage(
  markdown: Uint8Array,
  images: Map<string, File>,
): Promise<Uint8Array<ArrayBuffer>> {
  const archive = new JSZip();
  const options = {
    createFolders: false,
    date: new Date(1980, 0, 1, 0, 0, 0),
    unixPermissions: 0o100644,
  };
  archive.file(ENTRYPOINT, markdown, options);
  for (const path of [...images.keys()].sort()) {
    const file = images.get(path)!;
    archive.file(path, new Uint8Array(await file.arrayBuffer()), options);
  }
  const bytes = await archive.generateAsync({
    compression: "STORE",
    platform: "UNIX",
    type: "uint8array",
  });
  return new Uint8Array(bytes);
}

/** Collect files with their relative paths from a drop, including folders. */
export async function droppedFiles(
  transfer: DataTransfer,
): Promise<SelectedFile[]> {
  const entries = [...(transfer.items ?? [])]
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => Boolean(entry));
  if (entries.length === 0)
    return [...transfer.files].map((file) => ({ file, path: file.name }));
  const collected: SelectedFile[] = [];
  for (const entry of entries) await collectEntry(entry, "", collected);
  return collected;
}

/** Map an input selection, keeping folder-relative paths when present. */
export function selectedFiles(files: FileList | File[]): SelectedFile[] {
  return [...files].map((file) => ({
    file,
    path: file.webkitRelativePath || file.name,
  }));
}

async function collectEntry(
  entry: FileSystemEntry,
  parent: string,
  collected: SelectedFile[],
): Promise<void> {
  const path = parent ? `${parent}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    collected.push({ file, path });
    return;
  }
  if (!entry.isDirectory) return;
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (batch.length === 0) return;
    for (const child of batch) await collectEntry(child, path, collected);
  }
}

function rootedSelection(entries: SelectedFile[]): SelectedFile[] {
  const split = entries.map((entry) =>
    entry.path.split("/").filter((part) => part !== ""),
  );
  const first = split[0]?.[0];
  const sharedFolder =
    split.every((parts) => parts.length > 1 && parts[0] === first) &&
    first !== undefined;
  return entries.map((entry, index) => ({
    file: entry.file,
    path: (sharedFolder ? split[index]!.slice(1) : split[index]!).join("/"),
  }));
}

function soleMarkdown(selection: SelectedFile[]): SelectedFile {
  const candidates = selection.filter((entry) => hasSuffix(entry.path, ".md"));
  if (candidates.length !== 1) throw new PackageError(AMBIGUOUS_ENTRYPOINT);
  return candidates[0]!;
}

function byName(scoped: Map<string, File>, path: string): File | undefined {
  const name = path.split("/").at(-1);
  const matches = [...scoped.entries()].filter(
    ([candidate]) => candidate === name,
  );
  return matches.length === 1 ? matches[0]![1] : undefined;
}

function hasSuffix(path: string, suffix: string): boolean {
  return path.toLowerCase().endsWith(suffix);
}

function hasImageSuffix(path: string): boolean {
  const name = path.split("/").at(-1)!;
  const dot = name.lastIndexOf(".");
  return dot > 0 && SUPPORTED_IMAGE_SUFFIXES.has(name.slice(dot).toLowerCase());
}

function isRemote(destination: string): boolean {
  let value = destination;
  for (let pass = 0; pass <= MAX_URL_DECODE_PASSES; pass += 1) {
    if (REMOTE_RESOURCE.test(value)) return true;
    const decoded = percentDecode(value, false)!;
    if (decoded === value) return false;
    value = decoded;
  }
  return false;
}

function percentDecode(value: string, fatal: boolean): string | undefined {
  const encoder = new TextEncoder();
  const bytes: number[] = [];
  for (let index = 0; index < value.length; ) {
    const escape = /^%[0-9a-f]{2}/i.exec(value.slice(index, index + 3));
    if (escape) {
      bytes.push(Number.parseInt(value.slice(index + 1, index + 3), 16));
      index += 3;
      continue;
    }
    const codePoint = value.codePointAt(index)!;
    const character = String.fromCodePoint(codePoint);
    bytes.push(...encoder.encode(character));
    index += character.length;
  }
  try {
    return new TextDecoder("utf-8", { fatal }).decode(new Uint8Array(bytes));
  } catch {
    return undefined;
  }
}
