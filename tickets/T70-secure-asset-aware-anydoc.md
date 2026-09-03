---
ticket: T70
linear_id: G1L-538
linear_url: https://linear.app/g1lom/issue/G1L-538/t70-implement-secure-asset-aware-anydoc-conversion
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T70 - Implement secure asset-aware anydoc conversion

## Objective

Implement the approved local anydoc adapter and deterministic asset-aware Markdown package builder
for reverse conversion.

## Acceptance criteria

* Add the exact approved `firecrawl-anydoc` dependency through `uv`, preserve Python 3.14 and UBI 9
  compatibility, and include it in dependency, SBOM, license, vulnerability, and release
  verification.
* Parse only the format matrix approved by T69 from bounded in-memory or isolated-workspace inputs
  after the existing malware-scanning boundary.
* Implement one disposable process/container per conversion attempt, placed in a dedicated stable
  kernel isolation unit or cgroup. The anydoc binding runs in-process only inside that unit, with
  bounded threads. T71 configures CPU, memory, PID/descendant, and bounded
  workspace/ephemeral-storage budgets enforced at the kernel boundary. The external supervisor owns
  heartbeat and the attempt token, passes only bounded local input/output, gives the child no
  network or persistence credentials, and is the only publisher. Cancellation, deadline, lease
  loss, or resource failure hard-terminates the whole stable unit rather than a PID; the supervisor
  proves the unit empty and terminated before recovery or another attempt can start. PID exit alone
  is insufficient. Normal completion still revalidates the active lease/token before publication.
* Keep the production path CPU-only. It must not load GPU/accelerator or ML runtimes or invoke a
  browser, Pandoc, LibreOffice, or another document engine. The approved per-attempt anydoc child is
  an isolation boundary, not a second document engine.
* Implement one narrowly bounded maintained internal compatibility adapter around the pinned
  anydoc `Document` model and renderer behavior. Consume the single parsed document; never reparse
  source bytes or add a second parser. Inventory every private symbol and minimally mirrored
  upstream renderer behavior in one fail-closed module boundary, including applicable license
  notices, and reject unknown anydoc versions or document-model variants.
* Require security review, asset-free serializer parity against the pinned upstream renderer,
  source-position asset-link goldens, and compatibility tests for every anydoc update. Include the
  adapter and exact upstream surface in dependency, SBOM, license, and vulnerability evidence. T70
  owns maintenance and removes the adapter once upstream supplies a supported asset-aware hook; a
  broad fork is not authorized.
* Convert structured content into deterministic UTF-8 GitHub-Flavored Markdown while preserving
  supported headings, lists, tables, links, notes, code, equations, and document order.
* Emit a deterministic ZIP containing one root Markdown file plus referenced files under `assets/`;
  every exported image has a safe relative `![]()` link at the corresponding source position, a
  normalized allowed media type and extension, bounded bytes/dimensions/count, collision-free
  stable name, and no orphan or escaping archive path.
* Treat every exported image as untrusted using the T08-equivalent security boundary: identify
  decoded signatures independently of source names and declared media types; reject non-image,
  mismatched, and polyglot payloads; bound dimensions, decoded bytes, and decompression work; reject
  animated or multi-frame content; and sanitize then rasterize SVG locally with external entities,
  scripts, external references, and network access disabled before deterministic normalization.
* Generate the approved content-free traceability manifest through one T70-owned canonical
  serializer with stable field and ZIP-entry ordering. Do not include source/result text, original
  filenames, asset source names, local paths, secrets, or nondeterministic timestamps, and do not
  leave a second manifest generator for T71.
* Reject or safely degrade unavailable images and document-controlled remote images according to
  the T69 contract; never download remote resources.
* Keep OCR and every hosted Firecrawl path disabled. Scanned or image-only PDF input fails with a
  stable `needs_ocr`-style error and no network request.
* Map anydoc and packaging failures to stable content-free Markweave errors without filenames,
  document content, secrets, or local paths.
* Add unit, corpus/golden, fuzz-or-mutation, security, and real-library integration coverage for
  success and relevant malformed, encrypted, decompression, nesting, signature/type mismatch,
  non-image, polyglot, animated/multi-frame, hostile-SVG, timeout, cancellation, and
  resource-limit failures. Prove stable whole-unit hard termination and descendant reaping for every terminal signal,
  no child publication capability, no late result acceptance, termination-before-recovery, no
  overlapping attempt, bounded IPC, and fail-closed behavior when termination proof is unavailable.

## Dependencies

* T08
* T18
* T20
* T69

## Implementation boundary

* Own the supervised disposable-attempt runner and termination protocol, anydoc adapter, bounded
  internal renderer compatibility boundary, asset-aware serializer/package builder, the single
  deterministic content-free manifest generator, reverse-conversion domain errors, format corpus,
  dependency lock, and directly affected backend-image contents. Expose the runner and verified
  termination result as ports for T71; do not add persistent lease or job orchestration here.
* Do not add HTTP routes, persistent jobs, database migrations, or browser UI.
* Do not implement OCR or any network-backed fallback.

## Quality requirements

* Preserve arbitrary-UID, read-only-root, bounded-workspace, no-document-egress, and malware-
  scanning invariants.
* Meet the measured low-compute envelope approved by T69 and expose no unbounded internal
  parallelism.
* Maintain the repository coverage thresholds and add a dedicated real-anydoc integration marker
  or domain if required by T69.
* Keep repository artifacts and user-facing errors in English.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; depends on T69.
* 2026-09-03: Scope now requires an exclusively CPU-only native path and the measured low-compute
  envelope from T69.
* 2026-09-03: T69's product decisions authorize a disposable supervised process/container in a
  dedicated per-attempt kernel isolation unit/cgroup and a bounded maintained internal renderer adapter. T70 owns the isolation runner,
  terminate-and-verify protocol, compatibility boundary, required security/parity/version and
  SBOM/license evidence, and removal when an official upstream asset-aware hook becomes available.
  The native call remains in-process only within its disposable child; shared-worker native
  execution, PID-only termination proof, and a broad serializer fork are prohibited. T71 owns the
  configurable CPU, memory, PID/descendant, and workspace/ephemeral budget values.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
