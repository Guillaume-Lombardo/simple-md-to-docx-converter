# Secure archives and local images

The synchronous input-package boundary is used by conversion workers after upload scanning.

## Accepted inputs

A standalone Markdown string is accepted only when it has no image dependency. A ZIP package may
contain Markdown plus PNG, JPEG, SVG, static GIF, and WebP images. The package selects root
`document.md` when present; otherwise it requires exactly one `.md` file. Markdown is decoded as
strict UTF-8.

Every ZIP limit is supplied explicitly through `ArchiveLimits`, and every image limit is supplied
through `ImageLimits`. Image limits cover source bytes, dimensions, pixels, SVG element count, and
SVG nesting depth. Configured SVG depth is additionally constrained by a hard safety ceiling of 64
elements so XML serialization and rasterization cannot reach Python's recursion limit. The code
intentionally provides no production defaults because deployment owns the approved upload, decompression,
file-count, image-count, and resource values.

## Archive boundary

The ZIP implementation performs a complete preflight before reading any member and never calls
`extract()` or `extractall()`. It rejects:

- absolute, backslash-containing, escaping, non-canonical, NUL, drive-prefixed, and Unicode/case
  colliding paths;
- file/directory prefix collisions, symlinks, special Unix files, encryption, and compression
  methods other than stored or Deflate;
- member types outside Markdown and the approved image formats;
- configured archive, entry, member, total-uncompressed, compression-ratio, Markdown, and image
  count limits.

Member reads are chunked and bounded. Declared and actual sizes, the cumulative size, CRC, and
decompression errors are checked. Failures use stable messages that contain neither source content
nor member paths.

## Image boundary

Raster inputs are decoded according to their content and must match their declared extension.
Animated inputs are rejected. EXIF orientation is applied; metadata is removed; and the result is a
deterministic PNG bounded by configured width, height, pixel, and source-byte limits.

SVG is parsed as untrusted XML. DTD and entity declarations are rejected. Scripts, event handlers,
foreign XML, style blocks, external `href`/`xlink:href`, `xml:base`, and unsafe CSS declarations are
removed before the sanitized tree reaches CairoSVG. Safe inline presentation declarations and
local fragment references are preserved. SVG element/depth and inline CSS node/depth traversal are
bounded before serialization. Parser recursion failures are converted into safe declaration
removal. Rasterization uses fixed pixel dimensions, `unsafe=False`, and the locally installed Cairo
engine. No remote or host-file resource is passed to the renderer.

## Markdown and Pandoc binding

Every Markdown image token, including tokens found in footnotes and YAML metadata scalars, must
resolve relative to the selected entrypoint and match one normalized resource in the immutable
`ApprovedDocument` manifest. Absolute, escaping, query/fragment, ambiguous percent-encoded,
missing, and remote destinations fail before Pandoc is invoked.

The Pandoc adapter defensively validates the manifest again. Each resource is decoded, checked
against its carried image limits, and required to reproduce the exact metadata-free normalized PNG
before materialization. It materializes only the selected Markdown and normalized resources under a
disposable `package` directory, keeps the reference and output documents at fixed separate paths,
and retains the fixed reader, arguments, environment, process-group deadline handling, and cleanup
guarantees documented in `docs/pandoc-docx.md`.

Focused rootless real-engine integration tests exercise ZIP corruption and encryption failures and
the complete ZIP → sanitized SVG → local Cairo rasterization → Pandoc 3.10.2 → OpenXML media path.
The standalone and distributed final-image E2E suites repeat that primary ZIP/SVG path through the
asynchronous HTTP workflow, require exactly one normalized PNG in the resulting OpenXML, and verify
stable failed jobs with no published result for corrupt ZIP data, encrypted member metadata, and
invalid image bytes.
