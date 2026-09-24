# Composer revision preview: implementation and qualification status

T90's browser preview always requests the `preview` artifact of the selected draft and revision from
the same-origin FastAPI API. Its download link addresses that revision's separate `download`
artifact. The browser checks the artifact path, media type, streamed byte limit, and the revision's
SHA-256 when metadata supplies it. A newer request aborts the older fetch. The displayed frame
remains until a newer frame reports a completed render with the expected source window, opaque
origin, random token, draft ID, and revision ID. A failed replacement leaves the previous frame
visible, including DOCX/PPTX-to-PDF and PDF-to-DOCX/PPTX transitions. A PDF replacement first
renders its initial page on a separate canvas; the visible page, active revision label, and
download switch together after that render succeeds. The component reports the committed
revision through `onActiveRevisionChange`; selecting a different draft clears the reported
identity. The active preview's download stays paired with its visible revision. Before any usable
artifact is displayed, a separately labeled requested-revision download is available during
loading or after failure. A failed replacement retains only the displayed revision's download in
the viewer. Zoom and DOCX scroll offset or PPTX slide number carry across revisions.

## Isolation

Only the exact `/composer` parent response adds `frame-src 'self'` to the existing strict CSP.
The dedicated `/composer-preview` response is dynamically generated with a fresh script nonce and
a separate CSP. It sets `sandbox allow-scripts`, including on direct navigation; the parent also
sets `iframe sandbox="allow-scripts"`. Neither allows `allow-same-origin`. The child uses only
self-hosted nonce-bearing classic bundles. Its CSP blocks connections, workers, forms, and objects
and permits embedded `data:` or `blob:` images and fonts. The sandbox restricts top-level
navigation; child action handlers suppress tested document-link activation. The CSP does not
categorically prevent same-frame navigation. The authenticated parent fetches document bytes; the
child has no API fetch path or access to the parent's DOM.

The child checks archive paths, duplicate entries, member count and sizes, declared and actual
expanded bytes, XML DTD/entities, external non-hyperlink relationships, active package parts,
image signatures, dimensions, and decoded pixel totals before native rendering. SVG, HTML,
ActiveX, OLE embeddings, and unsupported media are rejected. These checks are a narrow preview
subset, not a general OOXML sanitizer. The downloaded revision remains the exact original artifact.
The browser's per-artifact input limit defaults to 16 MiB and is exposed as the component's
`maxBytes` setting. The child also enforces absolute safety ceilings of 64 MiB compressed,
128 MiB expanded, 32 MiB per member, 2,000 members, 16 million pixels per image, and 40 million
image pixels total. These are provisional safety ceilings pending media-heavy qualification, not
documented production capacity guarantees.

## Rendering behavior

`docx-preview` 0.4.1 renders native DOCX into the child. It downloads and parses the complete file
and builds the complete DOM. It recognizes explicit page breaks but does not infer natural Word
pagination. After rendering, the child divides each section's article into approximate flow units
of at most 40 top-level blocks or about 900 CSS pixels. It mounts the current unit and its
neighbors, replacing distant units with measured-height placeholders. Offscreen units retain XML
DOM serialization rather than detached nodes; XML parsing preserves renderer DOM structures that
ordinary HTML parsing would rewrite. The child rejects unsafe tags, attributes, image sources,
and CSS both before serialization and after restoration. It checks exact serialized content and
top-level order on each eviction and restoration. The total serialized cache is bounded by
`maxSerializedMarkupBytes`, which defaults to the existing 32 MiB Office-member ceiling and can
be lowered by the caller. At most three units mount; eviction precedes restoration to avoid a
temporary fourth. An indivisible unit or non-flow content above 2,000 nodes fails safely. The
initial complete parse and render still cause transient memory pressure. For supported numbering
formats (decimal, upper/lower Roman, and upper/lower letters), source OOXML materializes static
labels before windowing so distant-unit eviction does not reset them. Counters are separate for
each concrete `numId`, including instances that share one abstract definition; a `startOverride`
applies to its own instance and nested levels reset when that instance's parent advances. Known
Symbol bullets map to visible Unicode bullets. Unsupported numbering formats fail the native
preview explicitly rather than fabricating a label. The renderer's constrained SVG text-box
fallback becomes validated, inert, in-flow HTML; document-provided SVG
parts remain rejected. This preserves text without allowing a floating shape to obscure content,
but changes placement. Approximate pagination, floating-object layout, footnote/endnote placement,
and fonts can differ from Office. Users are told to inspect the downloaded revision for final layout.

`@aiden0z/pptx-renderer` 1.3.0 parses the complete PPTX but mounts one editable-source slide
preview at a time. Previous/next navigation replaces the visible slide and keeps the slide number
across revisions. Its built-in windowed list produced empty slots in this production child during
qualification, so the one-slide mode is used. The main viewer plus thumbnail viewers for the
current slide and up to two neighbors can hold four renderer instances in a deck of at least three
slides. The neighbor views populate a progressive thumbnail strip, and stale neighbors are
destroyed as navigation moves. The visible slide and thumbnail DOM
markup together are limited to the existing 32 MiB member ceiling and 2,000 DOM descendants.
The source corpus uses StarBats U+F095, Wingdings U+F06C, and Symbol U+F02D bullet glyphs.
Only these exact DrawingML font/glyph pairs are mapped to portable Unicode in a bounded,
preview-only archive clone before rendering. The original artifact and its digest are unchanged.
Other private-use glyphs fail a visible slide render or mark its thumbnail unavailable rather
than displaying an unknown symbol. The post-ready DOM observer validates vendor mutations without
writing into the vendor tree; unsafe late content fails closed. Child-level capture handlers make
document links inert, including keyboard, auxiliary-click, and context-menu activation. The
rendered markup cap does not bound the renderer's internal model, decoded images, or object-URL
contents. PPTX parsing can still load the complete archive before the first slide appears.

PDF.js 6.3.289 renders PDF pages on a canvas in the parent, with a main-thread handler because
the parent CSP forbids workers. It shows one page, requests adjacent pages, and progressively
builds a maximum of ten nearby thumbnail data URLs. The thumbnail strings together cannot exceed
the caller's `maxBytes` artifact budget; a page canvas is limited to 16 million pixels. The
complete PDF is fetched and parsed. Main-thread responsiveness and complex-file fidelity still
need browser and final-image proof.

The self-hosted dependency versions are exact in `pnpm-lock.yaml`: `docx-preview` 0.4.1,
`@aiden0z/pptx-renderer` 1.3.0, PDF.js 6.3.289, and JSZip 3.10.2. The first three declare
Apache-2.0; JSZip declares MIT or GPL-3.0-or-later. The PPTX package also includes MPL-2.0
decompressor and ECMA text notices. Build-time esbuild 0.28.2 produces classic child bundles and
copies the full package notices into ignored `web/public/composer-preview/licenses/`, indexed by
`THIRD_PARTY_NOTICES.txt`. The frontend image build copies the generated assets and notices into
the final runtime image. The qualification probe rebuilds the child in memory and compares its
SHA-256 with the generated file and bytes delivered to Chromium; a stale child fails closed.

## Current candidate receipt and reproducibility

The evidence below is for the reviewed preview implementation on 2026-09-23. It is a local,
production-mode Next build, not a final rootless-image E2E result. The independent source review
passed on the frozen 26-file manifest. That manifest's SHA-256 is
`e0b4e97f3622c08279da2b38eb1b1e7fcaa34e0872ac93b1f47a300107ca972e`; the same
manifest **excluding this documentation file** has SHA-256
`1c3e7880371b0960985fa0a1989cd26b599f78859b689d0e349531d9c4ca851e`.
The reviewed `frame-client.ts` source SHA-256 is
`a0a866ca34e1bf1075e15d7fdedd5653e22e0e39352ead8670ff0fba6a4d3393`.
These are source/manifest hashes, not a claim that source bytes equal generated JavaScript bytes.

The integrated Next build ID was `KByMdRLrNFMUZGHM0wZOC`. The generated child bundle SHA-256
and the actual served-byte SHA-256 both were
`26ceec49250489e975f25ac081f255f3498c3a592b373c7452e2e962a9e5eb43`.
The browser loaded that bundle from the new server on port 33785 with `Cache-Control: no-store`;
the qualification probe checks the regenerated bundle, generated file, and fetched response
byte-for-byte and rejects a stale server. The served route-policy template SHA-256 was
`93cfe8cd6497427cec4e62ab2f4579c19d2b4f668ef60753c48d054b5b5dbbda`.
The probe ran in Chromium 151.0.7922.34 with Node 24.19.0 on `codex-dev`.
The uncontended machine-readable receipt is
`/home/g1lom/dev/scratch/t90-preview-final-candidate-uncontended.jsonl`.
The current-build screenshot receipts are
`/home/g1lom/dev/scratch/t90-preview-final-captures.jsonl` and
`/home/g1lom/dev/scratch/t90-preview-final-inbounds-captures.jsonl`.

To repeat the local browser probe after a production build and a fresh server restart, run:

```bash
uv run --with python-docx==1.2.0 --with pillow==12.0.0 --with lxml \
  python web/src/composer/preview/generate-fixtures.py /home/g1lom/dev/scratch
MARKWEAVE_PREVIEW_BASE_URL=http://127.0.0.1:3000 \
MARKWEAVE_PREVIEW_LONG_DOCX=/home/g1lom/dev/scratch/composer-preview-long.docx \
MARKWEAVE_PREVIEW_STRUCTURED_DOCX=/home/g1lom/dev/scratch/composer-preview-structured.docx \
MARKWEAVE_PREVIEW_MEDIA_DOCX=/home/g1lom/dev/scratch/composer-preview-media.docx \
  node web/tests/composer-preview-qualification.mjs
```

The real corpus DOCX fixture SHA-256 was
`6b674297884f9ed57809763c9f60ea3a849d5cc6fb28c9837c714e322eceddcf`;
the real PPTX fixture SHA-256 was
`c96aa52da19f273f602040490203d9319872f512707e1a6c5a3fc53251b6d050`.
The generated long, structured, and media DOCX fixture SHA-256 values were respectively
`89437c1c9424b8be3c79dea3341df966e88cf974be1175ed6494420d6c3e1e50`,
`b212c0193c5efd609081dfd155f58628da3635ec00c59e32ccbababda2838d46`, and
`8dd7e2a2d422ce939799376a081cc5d80e8d3c23d08cb59a6cb927b9652b6081`.
The probe's `trace` record binds its results to the build, bundle, route, fixtures, and browser.

## Observed security and resource behavior

The fresh-browser probe found an opaque child origin (`null`) both inside the iframe and on
direct navigation. The served CSP did not contain `strict-dynamic`; an attempted external dynamic
script was blocked by `script-src-elem` and produced no network request. The real-file and
hostile-document runs recorded zero external requests or popups. Inserting a late link and
trying keyboard Enter, auxiliary click, right click, and Shift+F10 did not navigate the child or
open a popup. An external OOXML relationship, an SVG part, and a deliberately low markup budget
all produced a safe error. Focused tests also cover late vendor redraws, unknown private-use
glyphs, post-ready mutation limits, stale revision messages, replacement failure, cross-format
retention, revision-bound downloads, and 401 handling. This is evidence for the tested paths,
not a proof against every browser or document implementation.

| Fixture              |       Input | Local render | Full to live DOM nodes |  Serialized flow cache | Sampled child heap high-water |
| -------------------- | ----------: | -----------: | ---------------------: | ---------------------: | ----------------------------: |
| Real DOCX            |    10,159 B |        53 ms |             172 to 172 |               16,430 B |                      10.26 MB |
| Real two-slide PPTX  |    15,734 B |        44 ms |    42 live slide nodes | 550 B thumbnail markup |                      10.96 MB |
| 2,000-paragraph DOCX |    42,235 B |       123 ms |           4,024 to 207 |              413,780 B |                      12.90 MB |
| Structured DOCX      |    11,774 B |       101 ms |           1,767 to 443 |              197,288 B |                      10.84 MB |
| 30-image DOCX        | 5,958,454 B |       351 ms |              264 to 79 |           15,801,016 B |                 **195.86 MB** |

The long DOCX had 69 flow units with two mounted after scrolling; the media DOCX had 11 with
two mounted. Both reported zero retained detached block nodes and preserved the source-derived
content/order signature. Scrolling to the final paragraph and remounting the initial unit in
the long file gave zero pixel difference; distant content was visible. A forced CDP garbage
collection after the long and media runs reported about 7.21 MB and 15.10 MB child heap,
respectively. The sampled **195.86 MB** media transient is material despite that lower steady
reading. The probe sampled `performance.memory` every 5 ms with Chromium precise-memory mode;
it may have missed the true peak, and the readings are browser-local rather than whole-process or
end-to-end user latency. Its media input contains thirty distinct 256×256 PNGs; this one case
cannot establish a safe device capacity. The 32 MiB serialized cache ceiling and archive/pixel
ceilings reject oversized inputs, but the renderer's complete parse and decoded media still need
varied, media-heavy DOCX/PPTX/PDF high-water and cache-eviction measurements.

The real PPTX showed portable bullets, stable slide-2 text after at least 1.2 seconds, and a
zero-pixel difference between first and delayed slide-2 screenshots. The main slide stayed within
its 1,000-pixel child viewport. Two progressive thumbnails used 550 bytes of HTML; that does not
measure their decoded media or the renderer's model. Main-thread PDF page and thumbnail behavior
has focused component tests but was not covered by this real-file resource table. No measured
production load-time, memory, or fidelity threshold has been established.

## Same-source visual limits

The current-build native DOCX page-1 capture is
`/home/g1lom/dev/scratch/composer-lo-reference/docx-final-build-page-1.png`
(SHA-256 `5671f064714037aa408267a55f3da494a8cf277c986a25e703d284d34bf44645`).
A same-source LibreOffice PDF raster is at
`/home/g1lom/dev/scratch/composer-lo-reference/docx-lo-page-1.png`.
Lists and text remain readable, but pagination, font metrics, note placement, and the in-flow
text box differ from LibreOffice's floating box. Source OOXML gives two Roman-numbered paragraphs
separate concrete `numId` instances sharing an abstract definition; their source-derived labels
are IV/I. LibreOffice displays IV/V. This reference disagreement does not make IV/V the source
semantics or justify joining counters. Unsupported numbering formats fail visibly instead of
fabricating a label. Complete DOCX content is parsed before flow windowing; Word-exact page
fidelity is outside the current implementation.

The current-build native PPTX captures are
`/home/g1lom/dev/scratch/composer-lo-reference/pptx-final-build-page-1.png` and
`/home/g1lom/dev/scratch/composer-lo-reference/pptx-final-build-page-2.png`
(SHA-256 `4c9a0403e135ab0faea0ded43f541ef9961164c4937a7590384071c83eecbfca` and
`878153ecc75d94234960939881be5a44f593fe4ff7e0f6bf343e2e41abc49815`).
The real fixture's grouped text has source Y = 6,480,000 EMU with height 720,000 EMU while the
slide height is 5,670,550 EMU. The native element occupies viewport Y 706–733 below the slide
bottom at 636; LibreOffice also clips the authored off-slide group. It remains in native DOM but
is not readable in the user viewport. The preview preserves that authored geometry and discloses
clipping rather than silently moving content.

To test in-bounds group placement, a documented derivative moves that group's Y to 4,780,000 EMU
and separates the overlapping sibling label. Its fixture SHA-256 is
`a277981f0cee2f1abc3dc03c63200a7b9f4c85fa562e18de8d44aa5297e3b0eb`; the derivation
receipt is `/home/g1lom/dev/scratch/t90-inbounds-group-receipt.json`. The same-source
LibreOffice-derived PDF SHA-256 is
`76e3685afae4977b9b8c632633b9bbce97c47e535ced0419643df92737f4c383`.
In the current-build native slide-2 capture, the group text occupies Y 527–554 within the
636-pixel slide. The LibreOffice raster shows the full group at content-relative Y 502–578;
after removing the child's 20-pixel top padding, the native placement and title/table geometry
are close. The native capture is
`/home/g1lom/dev/scratch/composer-lo-reference/pptx-final-inbounds-page-2.png`
(SHA-256 `60a0444017f3fe71caca3fb8a845608e10216aac5b4843a1f511fd3c0cc2f5b5`).
This establishes visibility for one in-bounds grouped example, not general PowerPoint parity.
The original off-slide group remains a documented limitation.

Final rootless-image, both-profile browser E2E and release checks belong to the integrated T90
qualification; local Next results do not substitute for them. Complex office layout, unsupported
shapes/media, PDF performance, true process peak memory, and varied document fidelity remain
explicitly unqualified. The preview is a convenience for inspection; the exact downloaded
revision remains the definitive artifact.
