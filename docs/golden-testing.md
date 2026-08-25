# Reference corpus and golden-test infrastructure

The repository provides reusable, bounded corpus and comparison infrastructure for the document
engine suites. The helpers inspect fixtures and outputs; they do not themselves perform Markdown
conversion, invoke an engine, sanitize resources, install fonts, or define production limits. Each
case records its owning delivery ticket in `tests/corpus/manifest.json`; the historical field name
is `future_owner`, but all listed owners are now delivered.

## Corpus manifest

Every case has a stable identifier, sorted categories, a purpose, an owning ticket, an
entrypoint, a complete file list, expected observations, and provenance. Static project-authored
fixtures are text. Generated DOCX and adversarial ZIP fixtures are built deterministically and the
manifest pins their generator, license, byte length, and SHA-256 digest. Manifest loading verifies
those values and rejects missing files, symlinks, unsafe or non-normalized paths, and normalized
path collisions. A generated case's `generator` value names its concrete
`tests.golden.corpus.BUILDERS[...]` registry entry; use that case's `builder` key with
`build_case_bytes` to reproduce and verify the artifact.

Tests can use the session-scoped `corpus_manifest` fixture and the function returned by
`materialize_corpus_case`. Materialization always uses an isolated temporary directory. Archive
security tests inspect central-directory metadata in memory and never call `extractall` or extract
members individually. Every archive helper requires an immutable `ArchiveLimits` value covering
entry count, member and total uncompressed bytes, and compression ratio. These test-harness bounds
are metadata-checked before any member is read and are not production limits.

## DOCX comparisons

`inspect_docx` compares normalized ZIP part sets, canonical XML with namespace-prefix rewriting,
relationship metadata, ordered document text, style identifiers, page sizes, and SHA-256 hashes of
binary media. It rejects malformed, duplicate, encrypted, symbolic-link, traversal, DTD, and entity
inputs. Relationship targets are observations only and are never dereferenced. `inspect_docx`
requires the same explicit `ArchiveLimits`; it has no hidden resource defaults. Parts are read in
bounded chunks while their actual decompressed member and archive totals are counted. Declared-size
overruns, CRC failures, truncated streams, and decompression failures become `OpenXmlError` rather
than escaping as backend-specific exceptions, including corrupt Deflate, BZIP2, and LZMA streams.

`compare_docx` requires callers to pass `ignored_parts` explicitly. This makes any allowance for a
volatile OpenXML part visible at each test call; there is no implicit ignore list. ZIP timestamps,
member order, compression choice, and XML namespace-prefix spelling are intentionally normalized.

## PDF raster comparisons

`compare_pdf_rasters` accepts pages already rendered as unpremultiplied 8-bit sRGB RGBA bytes. It
does not choose or invoke a PDF renderer. Callers must supply all three tolerances explicitly:
maximum channel delta, changed-pixel ratio, and mean channel delta. Results report page-count,
dimension, DPI, per-page pixel metrics, and failing page indexes. A changed alpha channel counts as
a changed pixel. Empty page sequences are invalid. Callers must also provide `RasterLimits` for
page count, per-page pixels, and total pixels; channel metrics are accumulated in one pass without
allocating a second full-size delta buffer.

`render_pdf` uses the locked PDFium binding to render only after caller-supplied
PDF-byte, page-count, per-page-pixel, and total-pixel limits pass. The PDF corpus stores one
canonical PNG page and a canonical provenance manifest that pins the source, reference DOCX,
font manifest, Pandoc, LibreOffice, PDFium, DPI, dimensions, and PNG digest. Regenerate it only in
the approved rootless toolchain with:

```bash
uv run python -m scripts.generate_t11_pdf_golden OUTPUT_DIRECTORY WORKSPACE_DIRECTORY
```

The integration comparison is exact because the renderer, document engines, reference archive,
font artifacts, locale, and DPI are locked. The reference DOCX ZIP metadata is normalized before
hashing so Pandoc's extraction timestamps cannot create false provenance changes.

## Test classification

Pure raster arithmetic is marked `unit`. Real filesystem materialization, ZIP inspection, XML
parsing, and locked document-engine behavior are marked `integration` under
`tests/integration/document_engines`. Corpus, helper, and document-adapter changes select the active
`document-engines` CI domain, whose committed command runs that directory with the integration
marker. The standalone and distributed final-image suites separately exercise the delivered
browser and asynchronous conversion workflows; they do not replace the corpus suite's detailed
archive, OpenXML, raster, and engine failure evidence.
