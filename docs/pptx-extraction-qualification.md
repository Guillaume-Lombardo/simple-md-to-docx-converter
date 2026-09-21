# Structured PPTX extraction qualification

T83 provides opt-in slide-oriented Markdown and Marp extraction inside the existing disposable,
credentialless reverse attempt. It does not change ordinary anydoc extraction, recover the original
Markdown of an edited presentation, or promise visual round-trip fidelity.

## Pinned engine evidence

The local `firecrawl-anydoc==0.2.4` binding was tested with the repository's
`spikes/anydoc/corpus/pptx/pres.pptx` and a deterministic synthetic presentation containing an embedded
PNG. No external document or hosted service was used.

The corpus contains two slides, a nested list, a table, grouped text, and two presenter notes.
The pinned model returns eight ordinary blocks without slide delimiters; its `Document.notes`
collection is empty. Presenter notes appear as ordinary `block_quote` blocks. The model cannot
identify those notes independently of ordinary quoted slide text, nor recover slide boundaries.

The synthetic image remains available in `Document.assets`, including its media type, exact bytes
and package origin. The stock anydoc Markdown renderer emits the image description without a local
image link. This confirms the existing asset-aware adapter remains necessary for ordinary
extraction, but does not supply the missing slide identity.

Executable qualification and actual attempt-boundary regressions live in
`tests/integration/anydoc/test_pptx_structured.py`. They cover the original behavior, structured
Markdown/Marp, deterministic image normalization and packaging, unavailable images, and safe
malformed XML, external-image, invalid-image and configured-budget failures.

## Reader and traceability

The structured path uses the pinned anydoc native detector for format admission and the bounded
internal `markweave-pptx-v1` reader for content extraction. It follows the presentation's ordered
slide relationships, never filename order, and reads package members without extracting them to
the filesystem. No second engine, executable format interpreter, network request, OCR, or renderer
process is introduced.

Existing schema-1 manifest bytes remain unchanged for ordinary extraction. Structured asset
packages add the content-free optional field `"extractor": "markweave-pptx-v1"`; the existing
`engine` field identifies the pinned native admission detector. Persisted result traceability
also identifies the extractor, including for plain Markdown results. Strict supervisor validation
binds this field to the frozen job options before publication.

Notes and images default to enabled within the explicitly selected structured workflow.
Unsupported meaningful chart, diagram, embedded-object, media and drawing content receives
positioned warnings or placeholders. Inherited layout/master objects and background images are
identified as unsupported instead of being silently presented as faithfully extracted.
The reader retains hidden slides and identifies them with a warning. It excludes date, footer,
header, slide-number and slide-thumbnail note placeholders. It escapes document text before
generating slide breaks, Marp frontmatter or note wrappers.

Images use the existing bounded normalizer and deterministic `assets/image-NNNN.png` paths.
Duplicate image bytes share the normalized asset. Unsupported image types retain their source
position as unavailable and use the existing ZIP/manifest contract. Selecting image omission
produces explicit omission markers and retains no image bytes.

## Configurable limits

The existing reverse input, Markdown and asset budgets remain the default operating envelope.
Six optional positive reverse-specific overrides control:

| Setting suffix after `MARKWEAVE_REVERSION_` | Derived default |
| --- | --- |
| `PPTX_MAX_ARCHIVE_ENTRIES` | At least one, otherwise maximum input bytes divided by the 46-byte minimum ZIP central-directory entry |
| `PPTX_MAX_MEMBER_BYTES` | Maximum reverse input bytes |
| `PPTX_MAX_UNCOMPRESSED_BYTES` | Maximum reverse input bytes plus total source-asset bytes plus Markdown bytes |
| `PPTX_MAX_XML_ELEMENTS` | Maximum reverse Markdown bytes, used as an element-count ceiling |
| `PPTX_MAX_XML_ATTRIBUTES` | Maximum reverse Markdown bytes, used as an attribute-count ceiling |
| `PPTX_MAX_XML_DEPTH` | 64, a provisional reader safety ceiling; configurable to a lower positive depth |

These are conservative reader defaults, not a production performance guarantee or new normative
resource policy. Operators may set the explicit overrides for their measured workload; XML depth may be lowered within the reader safety ceiling. The
existing isolated runtime's memory, CPU, process, workspace and autonomous deadline controls still
apply.

The repository corpus measured 15,734 input bytes, 25 ZIP entries, 11,107 bytes in its largest part,
54,214 total uncompressed bytes, 1,505 XML elements, 1,366 XML attributes, and maximum XML depth 15.
This measurement supports the provisional depth headroom; it does not qualify arbitrary decks.
Tests exercise each override independently. XML counters are enforced during parsing, entities and
DTDs are rejected, and ragged table padding is sized against the Markdown budget before allocation.

## Remaining qualification boundary

The focused local reader tests do not substitute for the HTTP/CLI/browser contracts, shared
SQLite/PostgreSQL persistence tests or final rootless image workflows required by T83.
The broader cross-format, three-image release qualification remains T73 and still depends on T50.
No package version, public image pin, release workflow or deployment is changed by this evidence.
