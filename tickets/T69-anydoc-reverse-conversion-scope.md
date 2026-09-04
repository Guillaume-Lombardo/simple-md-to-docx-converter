---
ticket: T69
linear_id: G1L-537
linear_url: https://linear.app/g1lom/issue/G1L-537/t69-validate-and-specify-anydoc-reverse-conversion
status: In Progress
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
  and lease expiry must not permit overlapping native execution. Because the shared-process
  contract cannot satisfy these properties, record the approved external-isolation-broker design:
  only the broker holds Podman/Kubernetes workload authority; the application uses a narrow
  authenticated Unix/mTLS protocol and receives no raw runtime socket or workload-mutating service
  account; each immutable-image, fixed-argument disposable attempt runs without network, secrets,
  persistent volumes, or publication capability; and broker-proven whole-unit termination precedes
  recovery.
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
* Keep the Python binding in-process only inside the approved broker-created disposable per-attempt
  kernel isolation unit, with bounded threads and the smallest concurrency that meets the measured
  service objective. Keep Podman/Kubernetes authority exclusively in the broker, never the
  application or child, and leave CPU, memory, PID/descendant, and workspace/ephemeral budget values
  configurable for T71.
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
* 2026-09-03: Added a hash-locked redistributable corpus and reproducible host/exact-UBI probe for
  `firecrawl-anydoc 0.2.4`. The CPython ABI3 wheel loads on Python 3.14, all eight requested format
  families convert locally, embedded assets and their source-position ids are exposed for tested
  non-PDF models, PDF remains text-only, and scanned PDF, malformed, encrypted, unsupported, and
  resource-limit failures have stable upstream classes. The no-network UBI run used one CPU,
  512 MiB, 64 PIDs, a read-only root, no capabilities, and `RAYON_NUM_THREADS=1`; it observed no
  document-engine child, GPU/ML runtime, or hosted fallback. Exact measurements, provenance, format
  matrix, candidate package schema, and authorization contract are retained under
  `spikes/anydoc/`.
* 2026-09-03: Identified two independent feasibility blockers. The synchronous Python API supplies
  no cancellation token, deadline, or memory budget; cancelling a running Future leaves native work
  active, and a shared-process ceiling or forced exit cannot isolate one call. Lease publication
  fencing is possible, but lease expiry cannot both prevent overlapping native work and recover a
  crashed attempt. Separately, the public renderer strips embedded image references and exposes no
  `Document` renderer or asset resolver, so preserving image positions requires a maintained
  compatibility boundary. All-unavailable images are defined as a deterministic ZIP with Markdown plus the
  closed content-free manifest, zero emitted assets/bytes, and an unavailable-inline occurrence
  count; plain Markdown is reserved for documents with no embedded or unavailable image position.
  Refreshed host and exact-UBI reports measure whole-process CPU for every complete concurrency
  batch and sample peak live process threads during the batch, including the bounded Rayon thread.
  The bounded corpus now contains a probed fixture for every one of the 21 admitted extensions;
  generated alias fixtures record Apache-2.0 provenance and copied upstream fixtures remain MIT.
* 2026-09-04: The product manager resolved both feasibility blockers and selected the external
  isolation-broker refinement after deployment preflight proved that the current arbitrary-UID,
  capability-free worker cannot create delegated cgroups or disposable workloads itself. The broker
  alone holds Podman/Kubernetes workload authority and exposes only a narrow authenticated Unix-
  socket or mTLS protocol. It pins the reviewed attempt image by digest and fixed argv, creates one
  disposable kernel-isolated unit with no network, secrets, service-account token, ConfigMap, PVC,
  persistence credential, raw OCI socket, or publication capability, and hard-kills and proves the
  stable unit empty and removed before recovery. The worker-side supervisor retains heartbeat,
  attempt-token validation, bounded-output acceptance, and sole publication authority. T71 supplies
  reviewed configurable budgets and durably binds the stable unit identity and termination proof to
  recovery. T70 owns the broker protocol/service/backends and attempt runner. The manager also
  authorized one narrowly bounded internal adapter around the pinned anydoc model and renderer
  behavior, with no second parser or broad fork; security, serializer parity, asset position,
  version compatibility, SBOM/license inventory, named T70 ownership, and removal when upstream
  exposes an official asset-aware hook remain mandatory.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
