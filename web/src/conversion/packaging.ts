import JSZip from "jszip";

/**
 * Client-side packaging of a Markdown file with its referenced local images
 * (T104, specification decision D2). The browser places the selected files into
 * the approved archive layout before upload. The service still applies every
 * archive, path, image, scanning, and limit rule; these checks only give early
 * feedback with the service's stable validation messages.
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
const FENCE = /^( {0,3})(`{3,}|~{3,})(.*)$/;
const CONTAINER_PREFIX = /^(?:\s{0,3}(?:>|[-+*]|\d{1,9}[.)])(?:\s+|$))+/;
const LIST_ITEM = /^\s{0,3}(?:[-+*]|\d{1,9}[.)])(?:\s|$)/;
const DEFINITION = /^ {0,3}\[((?:[^\]\\]|\\.){1,999})\]:[ \t]*(\S.*)?$/;
const ASCII_PUNCTUATION = new Set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");
const INDENTED_CODE_COLUMNS = 4;
const ASCII_CONTROL_END = 32;
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  apos: "'",
  gt: ">",
  lt: "<",
  nbsp: " ",
  quot: '"',
};

export class PackageError extends Error {
  readonly code = "validation";
}

export type SelectedFile = { file: File; path: string };

export type PreparedSource = {
  file: File;
  imageCount: number;
  markdownName?: string;
};

/** Return image destinations in source order, excluding code. */
export function imageDestinations(markdown: string): string[] {
  const text = stripInlineCode(withoutCodeBlocks(markdown));
  const definitions = referenceDefinitions(text);
  const destinations: string[] = [];
  let index = 0;
  while (index < text.length) {
    if (text[index] === "\\") {
      index += 2;
      continue;
    }
    if (!text.startsWith("![", index)) {
      index += 1;
      continue;
    }
    const labelEnd = matchingBracket(text, index + 1);
    if (labelEnd === undefined) {
      index += 2;
      continue;
    }
    const [destination, next] = imageDestination(
      text,
      labelEnd,
      text.slice(index + 2, labelEnd),
      definitions,
    );
    if (destination === undefined) {
      index += 2;
    } else {
      destinations.push(destination);
      index = next;
    }
  }
  return destinations;
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
  for (const destination of imageDestinations(text)) {
    const path = resolveImagePath(destination);
    if (path === undefined || images.has(path)) continue;
    if (!hasImageSuffix(path)) throw new PackageError(MISSING_IMAGE);
    const file = scoped.get(path) ?? (flat ? byName(scoped, path) : undefined);
    if (!file) throw new PackageError(MISSING_IMAGE);
    const key = path.normalize("NFC").toLowerCase();
    if (keys.has(key)) throw new PackageError(INVALID_ARCHIVE);
    keys.add(key);
    images.set(path, file);
  }
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

function withoutCodeBlocks(markdown: string): string {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const kept: string[] = [];
  let fence: [string, number] | undefined;
  let previousBlank = true;
  let inList = false;
  for (const line of lines) {
    const content = line.replace(CONTAINER_PREFIX, "");
    if (fence) {
      const closing = FENCE.exec(content);
      if (
        closing &&
        closing[2]![0] === fence[0] &&
        closing[2]!.length >= fence[1] &&
        !closing[3]!.trim()
      )
        fence = undefined;
      kept.push("");
      previousBlank = true;
      continue;
    }
    const opening = FENCE.exec(content);
    if (opening && !(opening[2]![0] === "`" && opening[3]!.includes("`"))) {
      fence = [opening[2]![0]!, opening[2]!.length];
      kept.push("");
      previousBlank = true;
      continue;
    }
    const blank = !line.trim();
    if (LIST_ITEM.test(line)) inList = true;
    else if (!blank && previousBlank && !/^\s/.test(line)) inList = false;
    const expanded = line.replace(/\t/g, "    ");
    const indented = expanded.length - expanded.replace(/^ +/, "").length;
    if (
      !blank &&
      !inList &&
      previousBlank &&
      indented >= INDENTED_CODE_COLUMNS
    ) {
      kept.push("");
      continue;
    }
    kept.push(line);
    previousBlank = blank;
  }
  return kept.join("\n");
}

function stripInlineCode(text: string): string {
  const output: string[] = [];
  let index = 0;
  while (index < text.length) {
    const character = text[index]!;
    if (character === "\\" && index + 1 < text.length) {
      output.push(text.slice(index, index + 2));
      index += 2;
      continue;
    }
    if (character !== "`") {
      output.push(character);
      index += 1;
      continue;
    }
    let runEnd = index;
    while (runEnd < text.length && text[runEnd] === "`") runEnd += 1;
    const length = runEnd - index;
    const closing = closingBackticks(text, runEnd, length);
    if (closing === undefined) {
      output.push(text.slice(index, runEnd));
      index = runEnd;
      continue;
    }
    output.push(" ".repeat(closing + length - index));
    index = closing + length;
  }
  return output.join("");
}

function closingBackticks(
  text: string,
  start: number,
  length: number,
): number | undefined {
  const paragraphEnd = text.indexOf("\n\n", start);
  const limit = paragraphEnd < 0 ? text.length : paragraphEnd;
  let index = start;
  while (index < limit) {
    if (text[index] !== "`") {
      index += 1;
      continue;
    }
    let runEnd = index;
    while (runEnd < limit && text[runEnd] === "`") runEnd += 1;
    if (runEnd - index === length) return index;
    index = runEnd;
  }
  return undefined;
}

function matchingBracket(text: string, opening: number): number | undefined {
  let depth = 0;
  let index = opening;
  while (index < text.length) {
    const character = text[index];
    if (character === "\\") {
      index += 2;
      continue;
    }
    if (character === "\n" && text[index + 1] === "\n") return undefined;
    if (character === "[") depth += 1;
    else if (character === "]") {
      depth -= 1;
      if (depth === 0) return index;
    }
    index += 1;
  }
  return undefined;
}

function imageDestination(
  text: string,
  labelEnd: number,
  label: string,
  definitions: Map<string, string>,
): [string | undefined, number] {
  const following = labelEnd + 1;
  if (text[following] === "(") {
    const inline = inlineDestination(text, following + 1);
    if (inline) return inline;
  }
  if (text[following] === "[") {
    const referenceEnd = matchingBracket(text, following);
    if (referenceEnd !== undefined) {
      const reference = text.slice(following + 1, referenceEnd);
      const key = labelKey(reference.trim() ? reference : label);
      return [definitions.get(key), referenceEnd + 1];
    }
  }
  return [definitions.get(labelKey(label)), following];
}

function inlineDestination(
  text: string,
  start: number,
): [string, number] | undefined {
  let index = skipSpace(text, start);
  const parsed =
    text[index] === "<"
      ? angleDestination(text, index)
      : bareDestination(text, index);
  if (!parsed) return undefined;
  const [raw, afterDestination] = parsed;
  index = skipSpace(text, afterDestination);
  const opener = text[index];
  if (
    index > afterDestination &&
    opener !== undefined &&
    "\"'(".includes(opener)
  ) {
    const closing = opener === "(" ? ")" : opener;
    index += 1;
    while (index < text.length && text[index] !== closing)
      index += text[index] === "\\" ? 2 : 1;
    if (index >= text.length) return undefined;
    index = skipSpace(text, index + 1);
  }
  if (text[index] !== ")") return undefined;
  return [normalizeDestination(raw), index + 1];
}

function angleDestination(
  text: string,
  index: number,
): [string, number] | undefined {
  let end = index + 1;
  while (end < text.length && !"<>\n".includes(text[end]!))
    end += text[end] === "\\" ? 2 : 1;
  if (end >= text.length || text[end] !== ">") return undefined;
  return [text.slice(index + 1, end), end + 1];
}

function bareDestination(text: string, index: number): [string, number] {
  let end = index;
  let depth = 0;
  while (end < text.length) {
    const character = text[end]!;
    if (character === "\\" && end + 1 < text.length) {
      end += 2;
      continue;
    }
    if (/\s/.test(character) || character.charCodeAt(0) < ASCII_CONTROL_END)
      break;
    if (character === "(") depth += 1;
    else if (character === ")") {
      if (depth === 0) break;
      depth -= 1;
    }
    end += 1;
  }
  return [text.slice(index, end), end];
}

function skipSpace(text: string, start: number): number {
  let index = start;
  let newlines = 0;
  while (index < text.length && " \t\n".includes(text[index]!)) {
    if (text[index] === "\n") {
      newlines += 1;
      if (newlines > 1) break;
    }
    index += 1;
  }
  return index;
}

function referenceDefinitions(text: string): Map<string, string> {
  const definitions = new Map<string, string>();
  for (const line of text.split("\n")) {
    const match = DEFINITION.exec(line);
    if (!match || match[2] === undefined) continue;
    const rest = match[2].trim();
    let raw: string;
    if (rest.startsWith("<")) {
      const end = rest.indexOf(">");
      if (end < 0) continue;
      raw = rest.slice(1, end);
    } else {
      raw = rest.split(/\s+/)[0]!;
    }
    const key = labelKey(match[1]!);
    if (key && !definitions.has(key))
      definitions.set(key, normalizeDestination(raw));
  }
  return definitions;
}

function labelKey(label: string): string {
  return label.trim().split(/\s+/).join(" ").toLowerCase();
}

function normalizeDestination(raw: string): string {
  const unescaped = raw.replace(/\\(.)/gs, (escape, character: string) =>
    ASCII_PUNCTUATION.has(character) ? character : escape,
  );
  return unescaped.replace(
    /&(#x[0-9a-f]{1,6}|#[0-9]{1,7}|[a-z]+);/gi,
    (entity, body: string) => {
      if (body.startsWith("#")) {
        const hex = body[1] === "x" || body[1] === "X";
        const codePoint = Number.parseInt(
          body.slice(hex ? 2 : 1),
          hex ? 16 : 10,
        );
        return codePoint > 0 && codePoint <= 0x10ffff
          ? String.fromCodePoint(codePoint)
          : "�";
      }
      return NAMED_ENTITIES[body] ?? entity;
    },
  );
}
