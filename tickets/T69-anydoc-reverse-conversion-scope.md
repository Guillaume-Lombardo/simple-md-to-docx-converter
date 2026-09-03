---
ticket: T69
linear_id: G1L-537
linear_url: https://linear.app/g1lom/issue/G1L-537/t69-validate-and-specify-anydoc-reverse-conversion
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T69 - Validate and specify anydoc reverse conversion

## Objective

Validate Firecrawl anydoc as Markweave's local, non-OCR reverse-conversion engine and define the
normative product, security, packaging, and compatibility contract before implementation.

## Acceptance criteria

* Pin and evaluate an exact `firecrawl-anydoc` release under Python 3.14 in the UBI 9 backend image,
  including x86-64 wheel compatibility, license/provenance, startup/import behavior, memory,
  duration, and cancellation constraints.
* For the synchronous in-process native call, prove and choose enforceable cancellation, wall-time
  timeout, memory containment, lease-heartbeat, lost-lease fencing, and no-publication-after-loss
  semantics. A Python timeout or cancellation flag that leaves native work running is insufficient,
  and lease expiry must not permit overlapping native execution. If the fixed in-process, no-engine-
  subprocess contract cannot satisfy these properties, stop T70 and escalate the conflicting
  product or isolation decision to the product manager instead of weakening it.
* Measure cold and warm wall time, CPU time, peak resident memory, retained asset bytes, thread
  count, and concurrency scaling across small, representative, and configured-limit fixtures. Use
  the evidence to propose a reviewed configurable low-compute operating envelope; do not invent an
  unmeasured fixed threshold.
* Prove conversion is CPU-only and uses no GPU or accelerator, ML model, browser, Pandoc,
  LibreOffice, or other document-engine subprocess.
* Test the supported input families with a redistributable corpus: Word, PowerPoint, Excel,
  OpenDocument, RTF, EPUB, CSV, and text-based PDF; record an explicit extension/content-detection
  matrix and reject unsupported, encrypted, malformed, resource-exhausting, scanned, and image-only
  inputs with stable categories.
* Prove the default execution path is completely local and cannot opt into hosted Firecrawl OCR,
  even when `FIRECRAWL_*` environment variables are present; document OCR as excluded future scope.
* Determine an implementable asset-aware serialization strategy that preserves each embedded image
  at its source position as a safe relative Markdown image link and emits deterministic filenames
  in a ZIP. Prefer an upstream API when available; do not silently fork or duplicate the full
  anydoc serializer.
* Explicitly resolve the current PDF limitation: anydoc's PDF path does not expose the shared
  document model or embedded assets. Define the supported PDF contract without claiming image
  preservation that cannot be proved.
* Define deterministic output layout, filename/media-type normalization, duplicate handling,
  ordering, the content-free manifest/traceability schema, limits, retention, ownership, remote-link
  behavior, and safe error contracts. T70, not T69, owns the canonical manifest generator.
* Define reverse authorization explicitly: every `/api/v1/reversions` source, status, cancellation,
  and result route is owner-only. Administrators receive no document bytes, filenames, Markdown,
  assets, content-derived digests, impersonation, or download capability; separately audited
  administrator observability may expose only the minimum content-free operational metadata needed
  for capacity and execution diagnosis.
* Update `docs/product-specification.md` and the follow-up ticket contracts with the approved
  decision. This ticket introduces no production behavior.

## Dependencies

* T04
* T20
* T45
* T64

## Implementation boundary

* Own the reproducible spike, corpus additions needed for the decision, dependency/supply-chain
  assessment, architecture decision, and normative specification.
* Do not add a production route, persistence schema, worker behavior, or browser workflow.
* Do not enable or call Firecrawl's hosted OCR service.

## Quality requirements

* Keep every document-controlled operation local and network-independent.
* Prefer the in-process Python binding, bounded threads, and the smallest concurrency that meets the
  measured service objective; any different integration surface requires explicit T69 evidence.
* Use bounded, redistributable fixtures and record exact upstream versions and known limitations.
* Keep repository artifacts in English.

## Progress

* 2026-09-03: Created after the initial feasibility review confirmed that `firecrawl-anydoc 0.2.4`
  imports under Python 3.14 from a manylinux CPython 3.10 ABI3 wheel. The review also found the
  blocking asset contract gap: Python exposes embedded bytes and source-position asset identifiers
  through `Document`, but the public Markdown renderer emits only alt text for embedded images and
  PDF conversion exposes no `Document` asset model.
* 2026-09-03: Product scope was tightened to an exclusively CPU-only, low-compute workflow. T69 now
  owns measured CPU, wall-time, peak-memory, thread, asset, and concurrency evidence before any
  production budget is selected.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
