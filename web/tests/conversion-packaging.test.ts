import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import JSZip from "jszip";
import {
  AMBIGUOUS_ENTRYPOINT,
  ENTRYPOINT,
  ESCAPING_IMAGE_PATH,
  INVALID_ARCHIVE,
  INVALID_UTF8,
  MISSING_IMAGE,
  MIXED_ARCHIVE_SELECTION,
  PackageError,
  buildPackage,
  droppedFiles,
  imageDestinations,
  imageReferences,
  prepareSelection,
  resolveImagePath,
  selectedFiles,
  type SelectedFile,
} from "../src/conversion/packaging";

type References = {
  markdown: {
    markdown: string;
    destinations?: string[];
    error?: string;
    body_destinations?: string[];
  }[];
  resolutions: {
    destination: string;
    path?: string;
    error?: string;
    remote?: boolean;
  }[];
};

// The service's own answers, shared with the Python client and service tests.
const references = JSON.parse(
  readFileSync(
    resolve(__dirname, "../../tests/fixtures/markdown-image-references.json"),
    "utf-8",
  ),
) as References;

describe.each(references.markdown)(
  "image destinations for %#",
  ({ markdown, destinations, body_destinations }) => {
    it("match the service Markdown parser", () => {
      const service = destinations ?? body_destinations ?? [];
      const { required, optional } = imageReferences(markdown);
      expect(imageDestinations(markdown)).toEqual(required);
      if (optional.length === 0) {
        // Exact parity, including order, whenever the browser parsed everything.
        expect(required).toEqual(service);
      } else {
        // Never stricter (required is a subset) and never omitting (the union
        // covers every reference the service validates).
        for (const value of required) expect(service).toContain(value);
        for (const value of service)
          expect([...required, ...optional]).toContain(value);
      }
    });
  },
);

describe.each(references.resolutions)(
  "resolution of $destination",
  ({ destination, path, error, remote }) => {
    it("matches the service path rules and stable messages", () => {
      if (remote) {
        expect(resolveImagePath(destination)).toBeUndefined();
      } else if (error || path === "") {
        // An empty path names no file; the service reports it as missing.
        expect(() => resolveImagePath(destination)).toThrow(
          new PackageError(error ?? MISSING_IMAGE),
        );
      } else {
        expect(resolveImagePath(destination)).toBe(path);
      }
    });
  },
);

test.each([
  [
    "multiline definition",
    "![logo][ref]\n\n[ref]:\n  assets/logo.png\n",
    "assets/logo.png",
  ],
  [
    "container definition",
    "![logo][ref]\n\n> [ref]: assets/logo.png\n",
    "assets/logo.png",
  ],
  ["HTML entity", "![logo](assets/&copy;.png)", "assets/\u00a9.png"],
])(
  "review regression: a %s packages the referenced image",
  async (_name, markdown, image) => {
    const prepared = await prepareSelection([
      entry("doc.md", markdown),
      entry(image),
    ]);
    expect(Object.keys(await members(prepared.file))).toEqual([
      ENTRYPOINT,
      image,
    ]);
  },
);

test("review regression: list-indented code is not an image reference", async () => {
  const markdown = entry("doc.md", "- list\n\n      ![literal](missing.png)");
  expect(await prepareSelection([markdown, entry("other.png")])).toEqual({
    file: markdown.file,
    imageCount: 0,
  });
});

test("metadata images are packaged and unloadable metadata adds nothing", async () => {
  const prepared = await prepareSelection([
    entry("doc.md", "---\nlogo: '![l](meta.png)'\n---\n# Title\n"),
    entry("meta.png"),
  ]);
  expect(Object.keys(await members(prepared.file))).toEqual([
    ENTRYPOINT,
    "meta.png",
  ]);
  expect(imageDestinations("---\na: [\n---\n# Title\n")).toEqual([]);
});

const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3]);

function entry(path: string, content: BlobPart = png): SelectedFile {
  return { file: new File([content], path.split("/").at(-1)!), path };
}

async function members(file: File): Promise<Record<string, Uint8Array>> {
  const archive = await JSZip.loadAsync(await file.arrayBuffer());
  const result: Record<string, Uint8Array> = {};
  for (const name of Object.keys(archive.files))
    result[name] = await archive.file(name)!.async("uint8array");
  return result;
}

test("a folder is rooted, referenced images are packaged and others ignored", async () => {
  const markdown =
    "# Report\n\n![a](assets/a.png)\n![b](./assets/a.png)\n![r](https://example.com/r.png)\n![c](assets/n/c.svg)\n";
  const prepared = await prepareSelection([
    entry("project/report.md", markdown),
    entry("project/assets/a.png"),
    entry("project/assets/n/c.svg", "<svg/>"),
    entry("project/assets/unused.png"),
    entry("project/notes.txt", "ignored"),
  ]);

  expect(prepared.file.name).toBe("report.zip");
  expect(prepared.file.type).toBe("application/zip");
  expect(prepared.imageCount).toBe(2);
  expect(prepared.markdownName).toBe("report.md");
  const packaged = await members(prepared.file);
  expect(Object.keys(packaged)).toEqual([
    ENTRYPOINT,
    "assets/a.png",
    "assets/n/c.svg",
  ]);
  expect(new TextDecoder().decode(packaged[ENTRYPOINT])).toBe(markdown);
  expect(packaged["assets/a.png"]).toEqual(png);
  const archive = await JSZip.loadAsync(await prepared.file.arrayBuffer());
  expect(archive.file("assets/a.png")!.unixPermissions).toBe(0o100644);
});

test("packaging is deterministic for idempotent submissions", async () => {
  const selection = [entry("doc.md", "![x](x.png)"), entry("x.png")];
  const first = await prepareSelection(selection);
  const second = await prepareSelection(selection);

  expect(new Uint8Array(await first.file.arrayBuffer())).toEqual(
    new Uint8Array(await second.file.arrayBuffer()),
  );
});

test("a root document.md wins and nested Markdown keeps its own folder", async () => {
  const rooted = await prepareSelection([
    entry("document.md", "![x](x.png)"),
    entry("other.md", "# other"),
    entry("x.png"),
  ]);
  expect(rooted.markdownName).toBe("document.md");
  expect(rooted.file.name).toBe("document.zip");

  const nested = await prepareSelection([
    entry("docs/guide.md", "![x](img/x.png)"),
    entry("docs/img/x.png"),
    entry("img/x.png", "outside"),
  ]);
  expect(Object.keys(await members(nested.file))).toEqual([
    ENTRYPOINT,
    "img/x.png",
  ]);
});

test("a flat selection matches an image by its unique file name", async () => {
  const prepared = await prepareSelection([
    entry("doc.md", "![x](images/x.png)"),
    entry("x.png"),
  ]);
  expect(Object.keys(await members(prepared.file))).toEqual([
    ENTRYPOINT,
    "images/x.png",
  ]);

  await expect(
    prepareSelection([
      entry("doc.md", "![x](images/x.png)"),
      entry("x.png"),
      entry("other/x.png"),
    ]),
  ).rejects.toThrow(new PackageError(MISSING_IMAGE));
});

test("a single file and Markdown without local images are uploaded unchanged", async () => {
  const single = entry("bundle.zip", "zip");
  expect(await prepareSelection([single])).toEqual({
    file: single.file,
    imageCount: 0,
  });
  const markdown = entry("doc.md", "# Text ![r](https://example.com/r.png)");
  expect(await prepareSelection([markdown, entry("x.png")])).toEqual({
    file: markdown.file,
    imageCount: 0,
  });
});

test.each([
  [[entry("doc.md", "![m](missing.png)"), entry("x.png")], MISSING_IMAGE],
  [[entry("doc.md", "![t](../x.png)"), entry("x.png")], ESCAPING_IMAGE_PATH],
  [
    [entry("docs/doc.md", "![t](../x.png)"), entry("x.png")],
    ESCAPING_IMAGE_PATH,
  ],
  [[entry("doc.md", "![u](notes.txt)"), entry("notes.txt")], MISSING_IMAGE],
  [
    [
      entry("doc.md", "![a](A.png) ![b](a.png)"),
      entry("A.png"),
      entry("a.png"),
    ],
    INVALID_ARCHIVE,
  ],
  [[entry("a.md", "# a"), entry("b.md", "# b")], AMBIGUOUS_ENTRYPOINT],
  [[entry("x.png"), entry("y.png")], AMBIGUOUS_ENTRYPOINT],
  [
    [entry("doc.md", "# d"), entry("bundle.zip", "zip")],
    MIXED_ARCHIVE_SELECTION,
  ],
  [
    [entry("doc.md", new Uint8Array([0xff, 0xfe])), entry("x.png")],
    INVALID_UTF8,
  ],
])("selection %# fails with the stable message", async (selection, message) => {
  await expect(prepareSelection(selection)).rejects.toThrow(
    new PackageError(message),
  );
});

test("the package builder uses stored entries in path order", async () => {
  const bytes = await buildPackage(
    new TextEncoder().encode("# T"),
    new Map([
      ["z.png", new File([png], "z.png")],
      ["a.png", new File([png], "a.png")],
    ]),
  );
  const archive = await JSZip.loadAsync(bytes);
  expect(Object.keys(archive.files)).toEqual([ENTRYPOINT, "a.png", "z.png"]);
  // Stored entries keep the compression ratio at one for the service limits.
  expect(bytes.length).toBeGreaterThan(png.length * 2);
});

test("input selections keep folder-relative paths", () => {
  const plain = new File(["a"], "a.md");
  const nested = new File(["b"], "b.png");
  Object.defineProperty(nested, "webkitRelativePath", {
    value: "folder/img/b.png",
  });
  expect(selectedFiles([plain, nested])).toEqual([
    { file: plain, path: "a.md" },
    { file: nested, path: "folder/img/b.png" },
  ]);
});

function fileEntry(name: string, file: File): FileSystemFileEntry {
  return {
    file: (resolve: (file: File) => void) => resolve(file),
    isDirectory: false,
    isFile: true,
    name,
  } as unknown as FileSystemFileEntry;
}

function directoryEntry(
  name: string,
  children: FileSystemEntry[],
): FileSystemDirectoryEntry {
  return {
    createReader: () => {
      let read = false;
      return {
        readEntries: (resolve: (entries: FileSystemEntry[]) => void) => {
          resolve(read ? [] : children);
          read = true;
        },
      };
    },
    isDirectory: true,
    isFile: false,
    name,
  } as unknown as FileSystemDirectoryEntry;
}

test("dropped folders are traversed recursively", async () => {
  const markdown = new File(["![x](img/x.png)"], "doc.md");
  const image = new File([png], "x.png");
  const other = { isDirectory: false, isFile: false, name: "special" };
  const transfer = {
    files: [],
    items: [
      {
        webkitGetAsEntry: () =>
          directoryEntry("project", [
            fileEntry("doc.md", markdown),
            directoryEntry("img", [fileEntry("x.png", image)]),
            other as unknown as FileSystemEntry,
          ]),
      },
      { webkitGetAsEntry: () => null },
    ],
  } as unknown as DataTransfer;

  expect(await droppedFiles(transfer)).toEqual([
    { file: markdown, path: "project/doc.md" },
    { file: image, path: "project/img/x.png" },
  ]);
});

test("drops without entry support fall back to the file list", async () => {
  const file = new File(["# d"], "doc.md");
  expect(
    await droppedFiles({ files: [file] } as unknown as DataTransfer),
  ).toEqual([{ file, path: "doc.md" }]);
});

test("optional metadata references are packaged when present and never block", async () => {
  const markdown = [
    "---",
    "list:",
    "  - ![y](item.png)",
    "  - ![z](../escape.png)",
    "  - ![w](absent.png)",
    "  - ![v](ITEM.png)",
    "  - ![t](notes.txt)",
    "---",
    "# Title",
  ].join("\n");
  expect(imageReferences(markdown).required).toEqual([]);
  const prepared = await prepareSelection([
    entry("doc.md", markdown),
    entry("item.png"),
    entry("ITEM.png"),
    entry("notes.txt", "text"),
  ]);
  expect(Object.keys(await members(prepared.file))).toEqual([
    ENTRYPOINT,
    "item.png",
  ]);
});
