# T88 Composer document and preview qualification

This bounded spike tests whether controlled edits and private previews are plausible on the
repository's existing real OOXML corpus. It does **not** qualify a production editor, visual
fidelity, security isolation, or the final image. Generated documents and browser output stay
outside the repository. No application dependency or root pnpm lockfile changes are needed.

## Reproduce

From the repository root, using the reviewed local pnpm bootstrap described in
`docs/package-management.md`:

```bash
uv run --with python-docx==1.2.0 --with python-pptx==1.0.2 --with pypdf==6.1.1 \
  python spikes/composer/office_edit_probe.py \
  --fixture-dir "$HOME/dev/scratch/composer-renderer-qualification/fixtures"
uv run --with python-docx==1.2.0 --with python-pptx==1.0.2 --with pypdf==6.1.1 \
  python spikes/composer/office_family_probe.py
cd spikes/composer
pnpm install --frozen-lockfile --ignore-scripts
cd ../..
node spikes/composer/browser_preview_probe.mjs \
  "$HOME/dev/scratch/composer-renderer-qualification/fixtures"
node spikes/composer/browser_preview_probe.mjs \
  "$HOME/dev/scratch/composer-renderer-qualification/fixtures" allow-styles
node spikes/composer/browser_preview_probe.mjs \
  "$HOME/dev/scratch/composer-renderer-qualification/fixtures" allow-worker
node spikes/composer/browser_isolated_preview_probe.mjs \
  "$HOME/dev/scratch/composer-renderer-qualification/fixtures"
```

The browser probe serves only local fixture/package bytes on `127.0.0.1`, blocks attempted
external requests, records CSP violations, and closes Chromium and the server. Its strict mode
copies every directive from the current `web/proxy.ts` policy, generates a fresh nonce, and marks
the three bootstrap scripts with that nonce. The diagnostic modes change only `style-src` or
`worker-src`. They are evidence probes, not recommended production policies.

## Measured results (2026-09-23, Chromium via repository Playwright, Node 24.19.0)

| Case                                                             | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Meaning                                                                                                                                                                                                     |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Real corpus DOCX, one run changed with `python-docx` 1.2.0       | Reopened with replacement and original run formatting; 8 of 14 uncompressed package entries changed, including styles, numbering, settings, metadata, and relationships; media hash unchanged                                                                                                                                                                                                                                                                  | A successful visible edit does not prove unrelated OOXML preservation after high-level save.                                                                                                                |
| Same DOCX, single validated XML text-node edit                   | Reopened successfully; only `word/document.xml` changed, all other 13 entries byte-identical                                                                                                                                                                                                                                                                                                                                                                   | Narrow package-level edits can preserve non-target parts; this is not a general editing implementation.                                                                                                     |
| Whole DOCX paragraph assignment                                  | Seven runs, with explicit Bold/Italic/Struck character styles, collapsed to one unstyled run                                                                                                                                                                                                                                                                                                                                                                   | The LLM must never drive whole-paragraph replacement as an implicit generic text edit.                                                                                                                      |
| Real corpus PPTX, one title run changed with `python-pptx` 1.0.2 | Reopened with replacement and original run formatting; 21 of 25 entries changed, including non-target slide, notes, layouts, master and relationships                                                                                                                                                                                                                                                                                                          | High-level saving also changes non-target package parts for this real presentation.                                                                                                                         |
| Same PPTX, single validated XML text-node edit                   | Reopened successfully; only `ppt/slides/slide1.xml` changed, all other 24 entries byte-identical                                                                                                                                                                                                                                                                                                                                                               | Target-only package preservation is feasible for this narrow operation.                                                                                                                                     |
| Synthetic long editing inputs                                    | 2,000 DOCX paragraphs: 43,733-byte file, edit/load/save about 17–19 ms; 120 PPTX slides: 133,453-byte file, about 24–35 ms in two runs                                                                                                                                                                                                                                                                                                                         | These small, repetitive files test structural scale only; they do not establish safe limits for media-heavy documents.                                                                                      |
| `docx-preview` 0.4.1 under strict CSP                            | Source text appeared but injected style elements were blocked; first section width was 944.9 px, versus 793.7 px when inline styles were diagnostically allowed                                                                                                                                                                                                                                                                                                | The existing CSP yields demonstrably different layout. Direct use needs an isolated rendering design or a reviewed policy change.                                                                           |
| DOCX pagination and long DOM                                     | A 2,000-paragraph file produced one section and 2,000 paragraph nodes; a document with an explicit page break produced two sections                                                                                                                                                                                                                                                                                                                            | `docx-preview` does not infer Word pagination from natural flow; hiding offscreen pages does not avoid full parsing/DOM construction in this path.                                                          |
| `@aiden0z/pptx-renderer` 1.3.0 under strict CSP                  | Real two-slide corpus opened. A synthetic 120-slide deck opened in about 1.1 s, created 120 slots, but materialized slide text from only two slides with `lazySlides`, `lazyMedia`, and windowing enabled; inline style CSP violations remained                                                                                                                                                                                                                | Its lazy/windowed mode is promising for a private preview, but output fidelity and CSP compatibility are not qualified. Slots are not equivalent to fully rendered slides.                                  |
| PDF.js 6.3.289 under strict CSP                                  | Two-page real PDF and page 50 of a synthetic 100-page, 85,807-byte PDF rendered; fetch/load/page-render for the latter took about 52–67 ms. CSP blocked the exact self-hosted `/vendor/pdf.worker.mjs` attempt, yet rendering completed. A worker rendered successfully when `worker-src 'self'` was diagnostically enabled                                                                                                                                    | Simple PDFs render under the exact current CSP, but responsiveness and bounded main-thread work remain unproven. A worker exception would require a separate security decision.                             |
| Opaque-origin native Office frame                                | With only `frame-src 'self'` added to the parent CSP, a child `<iframe sandbox="allow-scripts">` rendered the real DOCX at 793.7 px and the real two-slide PPTX with local inline styles allowed only in the child. The child could not read parent DOM or fetch its own document or an external URL; its `connect-src 'none'` blocked both attempts, and no external request reached Chromium                                                                 | This proves a narrow native preview **candidate** can separate document rendering from the main page. It does not qualify document sanitization, visual fidelity, complete features, or deployment safety.  |
| Representative targeted Office edits                             | The separate family probe changed one named member for each of DOCX table cell, section margin, style color and same-dimension 1×1 PNG; and PPTX table cell, shape position, slide order and note text. Each changed only its requested uncompressed ZIP member, with no additions/removals, and reopened or inspected the target.                                                                                                                             | These are fixture-specific feasibility examples. They do not qualify a general parser, object model, visual fidelity, or safe untrusted-input executor.                                                     |
| Hostile DOM and actual document links in isolated frame          | The real DOCX rendered three links (two same-origin, one external). The frame's capture handler blocked all three click navigations. Deliberately inserted external and same-origin images and remote CSS were blocked by the child CSP; an inline `onerror` handler was blocked and no external request reached the network.                                                                                                                                  | The inserted `<script>` was inert because it came from `innerHTML`, so its non-execution is not evidence of CSP script protection. Document-derived hostile OOXML/HTML remains a separate gate.             |
| Message source and simulated revision race                       | One intended revision was accepted; two messages from a sibling opaque frame, one wrong-token result, and two stale-revision results were rejected. A delayed older request returned a stale result before starting its renderer after a newer revision arrived.                                                                                                                                                                                               | This proves a narrow message filter. Two genuinely overlapping renderer calls, cancellation, old-preview retention and cross-user authorization remain untested.                                            |
| Isolated long native rendering                                   | 43,733-byte/2,000-paragraph DOCX: full parse and DOM build ~61–101 ms across runs, 2,000 paragraph nodes, one section. 133,453-byte/120-slide PPTX: archive load and initial mount ~1.1 s; 120 slots, text from two slides; scrolling to slide 60 materialized it while visible text fragments fell from 40 to 20. One Chromium run sampled heap from ~7.0 MB to ~16.4 MB with ~26.0 MB sampled high-water, and page-level DOM counters reported 10,665 nodes. | The full files were fetched before rendering. The memory sample is not a true peak or production budget; the repetitive, tiny-media files do not represent complex long documents. DOCX is not virtualized. |

All baseline browser requests stayed on the local origin. The hostile probes deliberately produced
expected CSP violation messages; `external_requests` remained empty. These results do not test
every malicious OOXML relationship, image-heavy decks, fonts, embedded objects, signed packages,
tracked changes, charts, exact Office/LibreOffice visual correspondence, or two-profile/final-image
behavior. The fixture PDF is independent of the fixture DOCX/PPTX, so it cannot establish visual
equivalence between an Office document and its converted PDF. The 100-page PDF repeats two simple
pages; it does not exercise varied fonts, images, forms, or complex layouts. The browser probe
does not implement a production scroller, neighbor prefetch, cache eviction, thumbnails,
in-progress stale-render cancellation, or measure true peak memory or main-thread responsiveness. The browser times
are one-run, machine-local observations, not total application-load times or latency budgets.

Two untracked local screenshots of the real corpus expose visual questions beyond the DOM checks.
Several DOCX paragraphs carrying `w:numPr` in `word/document.xml` have no visible marker in the
capture, while other list labels appear inconsistent; footnote and endnote bodies are plain ASCII
in the source XML, but mojibake-like glyphs appear before them in the capture. The presentation
source includes a grouped shape near the bottom of slide 2, but the captured PPTX container does
not establish whether it renders completely because the frame viewport and windowed list can clip
the capture. These are observed or unresolved discrepancies, not proof of a renderer defect.
T90 needs reference-image regressions for list numbering, footnote/endnote text and font handling,
and complete slide bounds and group shapes at several viewport sizes before claiming fidelity.

## Approved native boundary and remaining acceptance gates

The user requires native DOCX and PPTX previews in the first Composer delivery; PDF-only preview
is insufficient. The measured candidate is a dedicated, opaque-origin iframe with `sandbox="allow-scripts"`
and **without** `allow-same-origin`. The authenticated parent fetches each authorized revision
through FastAPI and transfers the DOCX/PPTX `ArrayBuffer` to that exact frame with `postMessage`.
The prototype checks the frame source, parent origin, a token, and a numeric revision; production
must reject old/replayed responses and keep the previous preview visible until the new result is
ready. The child cannot read the parent DOM and has no document-fetch capability.

The user approved a route-scoped `frame-src 'self'` exception for Composer on 2026-09-23. This source expression
allows same-origin frames generally on that route, so the frame URL and router still need exact
route validation. The child
gets a separate CSP: nonce-bearing scripts with `strict-dynamic`, `connect-src 'none'`,
`object-src 'none'`, `worker-src 'none'`, `style-src 'unsafe-inline'`, and only `data:`/`blob:`
images and fonts. This permits the two current renderers' injected styles **inside the sandbox
only**. The probe bundles the pinned PPTX renderer into a self-contained classic script with
isolated `esbuild` 0.28.2 and loads all child scripts with nonces. No CORS header or `blob:` script
permission is needed, and document responses are never served to the child. The parent and child
policy differences, the frame route, and static asset loading are now authorized design inputs,
not proof of production containment. The main page's `style-src` remains unchanged.

`@aiden0z/pptx-renderer` can materialize visible slides while keeping other slide slots, but
the application still needs bounded cache/thumbnail disposal, adjacent-slide preloading,
zoom/position retention, stale-render rejection, and measured media-heavy limits. `docx-preview`
builds the full 2,000-paragraph DOM and does not infer Word pagination. Native DOCX preview must
therefore disclose that pagination is approximate, add application-level page/DOM virtualization
or a bounded alternate native rendering strategy, and compare against the actual Office/PDF layout
before claiming fidelity. Downloadable original files remain the authority. Do not enable the
PPTX renderer's optional EMF/PDF fallback yet: upstream uses `blob:` workers, contrary to the
proposed child CSP.

The probe blocks clicks on the three links from the real DOCX, denies tested image/CSS URLs and
inline handlers, and rejects selected spoof/stale messages. It does not establish resistance to
every navigation path, malicious OOXML relationship, script injection, or overlapping in-progress
render completion. Production must suppress all child navigation, validate relationships before
transfer, test document-derived HTML/script injection, and bind each async result to its captured
revision and token before any preview swap.
It must render in an immutable, replaceable frame so old documents cannot retain authority.

PDF.js remains relevant only for actual PDF revisions. A future reviewed `worker-src` exception
may improve responsiveness, but the simple 100-page fixture does not justify it by itself.

| Operation family | T88 evidence                                                                                                                                | Gate before accepting edits                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Text runs        | One real DOCX run and one real PPTX title run replaced and reopened; target-run formatting retained                                         | Reject spans across multiple runs, fields, tracked changes, and ambiguous IDs until separately tested. |
| Tables           | One DOCX and one PPTX table cell changed; each file reopened and only the containing document/slide member changed                          | Merged cells, repeated rows, rich cell content and target identity remain unqualified.                 |
| Sections         | One DOCX bottom margin changed by 36,195 EMU; only `word/document.xml` changed and the value reopened                                       | Headers, footers, breaks, relationships and actual pagination remain unqualified.                      |
| Images           | One same-dimension 1×1 DOCX PNG replaced; only its media member changed and the relationship reopened                                       | Rich media, metadata, SVG, relationships and visual placement remain unqualified.                      |
| Styles           | One DOCX named style color changed; only `word/styles.xml` changed and the value reopened. Whole-paragraph assignment lost three run styles | PPTX style edits and full formatting behavior remain unqualified.                                      |
| Slides           | One PPTX slide order swap changed only `ppt/presentation.xml`; both slide titles reopened in swapped order                                  | New/deleted slides, IDs, layouts, masters and notes associations need wider tests.                     |
| Objects/shapes   | One PPTX text-box horizontal offset changed by 90,000 EMU; only its slide member changed and the value reopened                             | Other shape types, groups, charts and unsupported objects remain unqualified.                          |
| Notes            | One PPTX note string changed only `ppt/notesSlides/notesSlide1.xml`; the package reopened. High-level save rewrote notes without request    | Note text-frame model, master/relationship behavior and visual output remain unqualified.              |

For direct Office edits, begin with validated structured operations that identify exact OOXML
targets and reject ambiguous or unsupported content. Operate on a copy, compare every unrequested
package part, reopen the result, then publish a revision atomically. These surgical examples prove
only that unrequested ZIP members remain byte-identical for the named fixture operations; they do
not prove visual identity or unchanged semantics within a touched XML part. Text spanning runs,
merged tables, complex sections, rich images, slide groups/charts, note masters, style inheritance,
repeatable fields, signatures and relationships each require separate validation and failure tests
before being advertised. The edit probes themselves are **not** safe to use on untrusted
documents: it omits archive size/path/XML limits and signed-document handling.

## Sources and supply chain

The isolated [pnpm lockfile](pnpm-lock.yaml) fixes the downloaded tarball integrity for
`docx-preview` 0.4.1, `@aiden0z/pptx-renderer` 1.3.0, `pdfjs-dist` 6.3.289, and direct JSZip
3.10.2; its installation uses `--ignore-scripts`. The isolated classic-bundle probe also pins
`esbuild` 0.28.2 as a spike-only development dependency. npm package metadata reports Apache-2.0
for the three renderers, `(MIT OR GPL-3.0-or-later)` for JSZip, and MIT for esbuild. This is license identification,
not a vulnerability audit or approval for production adoption.

- [`docx-preview` npm metadata](https://www.npmjs.com/package/docx-preview), [upstream page-break and API documentation](https://github.com/VolodymyrBaydalka/docxjs#readme)
- [`@aiden0z/pptx-renderer` npm metadata](https://www.npmjs.com/package/%40aiden0z/pptx-renderer), [upstream lazy/windowed, limits, and unsupported-feature documentation](https://github.com/aiden0z/pptx-renderer#readme), [upstream security guidance](https://github.com/aiden0z/pptx-renderer/blob/main/docs/SECURITY.md)
- [`pdfjs-dist` npm metadata](https://www.npmjs.com/package/pdfjs-dist), [PDF.js upstream viewer memory guidance](https://github.com/mozilla/pdf.js/wiki/Frequently-Asked-Questions), [PDF.js license](https://github.com/mozilla/pdf.js/blob/master/LICENSE)
- [JSZip npm metadata](https://www.npmjs.com/package/jszip)
- [esbuild npm metadata](https://www.npmjs.com/package/esbuild)

No renderer, Office editing library, or PDF.js worker has been added to the application bundle or
its production dependency graph by T88.
