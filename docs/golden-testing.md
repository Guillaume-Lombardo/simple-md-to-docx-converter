# Reference corpus and golden-test infrastructure

T04 provides reusable test infrastructure; it does not perform Markdown conversion, invoke a
document engine, sanitize resources, install fonts, or define production limits. Those behaviors
remain owned by T07–T11 and T18 as recorded per case in `tests/corpus/manifest.json`.

## Corpus manifest

Every case has a stable identifier, sorted categories, a purpose, a future owning ticket, an
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
are metadata-checked before any member is read and are not T18 production limits.

## DOCX comparisons

`inspect_docx` compares normalized ZIP part sets, canonical XML with namespace-prefix rewriting,
relationship metadata, ordered document text, style identifiers, page sizes, and SHA-256 hashes of
binary media. It rejects malformed, duplicate, encrypted, symbolic-link, traversal, DTD, and entity
inputs. Relationship targets are observations only and are never dereferenced. `inspect_docx`
requires the same explicit `ArchiveLimits`; it has no hidden resource defaults. Parts are read in
bounded chunks while their actual decompressed member and archive totals are counted. Declared-size
overruns, CRC failures, truncated streams, and decompression failures become `OpenXmlError` rather
than escaping as backend-specific exceptions.

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

## Test classification

Pure raster arithmetic is marked `unit`. Real filesystem materialization, ZIP inspection, and XML
parsing are marked `integration` under `tests/integration/document_engines`. Corpus and helper
changes select the active `document-engines` CI domain, which runs T04's filesystem, archive, and
XML boundary suite now. T07 can extend this active domain with real-engine tests without weakening
the T04 command. T04 does not deliver a user-visible or operational workflow, so final-image E2E
coverage is not applicable.
