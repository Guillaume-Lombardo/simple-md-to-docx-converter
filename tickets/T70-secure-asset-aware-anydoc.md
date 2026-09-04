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
* Implement a trusted external isolation broker as the only holder of Podman/Kubernetes workload
  authority. The application worker reaches it only through a narrow authenticated owner-restricted
  Unix socket or mutually authenticated TLS protocol and receives no raw OCI socket or workload-
  mutating service account. The broker accepts only bounded requests, content-free stable attempt
  identities, the reviewed image pinned by immutable digest, and a fixed reverse-attempt argv;
  user/document data cannot select runtime policy. It creates one disposable process/container
  workload per attempt in a dedicated stable kernel isolation unit. The anydoc binding runs in-
  process only inside that unit with bounded threads. The child has no network, service-account
  token, Secret, ConfigMap, PVC, persistence or broker credential, runtime socket, or publication
  capability. T71 supplies reviewed configurable CPU, memory, PID/descendant, and
  workspace/ephemeral budgets, which the broker enforces at the runtime/kernel boundary. The
  worker-side supervisor owns heartbeat, attempt token, bounded output validation, and publication.
  Cancellation, deadline, lease loss, broker disconnect, or resource failure stops output
  acceptance and makes the broker hard-terminate the whole stable unit, prove it empty, remove it,
  and return a content-free proof before recovery or another attempt. PID exit or delete
  acknowledgement alone is insufficient. Normal completion also requires termination proof and
  active lease/token revalidation before publication.
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

* Own the external broker service and authenticated bounded protocol, Podman and Kubernetes
  isolation backends, immutable image/argv and child-security policy, supervised disposable-attempt
  runner and terminate-and-prove protocol, anydoc adapter, bounded internal renderer compatibility
  boundary, asset-aware serializer/package builder, the single deterministic content-free manifest
  generator, reverse-conversion domain errors, format corpus, dependency lock, and directly affected
  backend/broker image and deployment contents. Expose the runner, content-free stable unit identity,
  and verified termination proof as ports for T71; do not add persistent lease or job orchestration
  here. Never expose a raw OCI socket or workload-mutating service account to the application or
  child.
* Do not add HTTP routes, persistent jobs, database migrations, or browser UI.
* Do not implement OCR or any network-backed fallback.

## Quality requirements

* Preserve arbitrary-UID, read-only-root, bounded-workspace, no-document-egress, and malware-
  scanning invariants.
* Meet the measured low-compute envelope approved by T69 and expose no unbounded internal
  parallelism.
* Test both broker transports and both runtime backends. Authenticate peers, reject replay,
  oversized/truncated/extra protocol frames and every caller-selected image/argv/mount/network/
  credential/resource override, fail closed on broker disconnect or unavailable termination proof,
  and prove that runtime authority is absent from the application and child.
* Maintain the repository coverage thresholds and add a dedicated real-anydoc integration marker
  or domain if required by T69.
* Keep repository artifacts and user-facing errors in English.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; depends on T69.
* 2026-09-03: Scope now requires an exclusively CPU-only native path and the measured low-compute
  envelope from T69.
* 2026-09-04: Deployment preflight proved that the current arbitrary-UID, capability-free worker
  cannot safely create delegated per-attempt cgroups or workloads. The product manager selected a
  trusted external isolation broker as the sole holder of Podman/Kubernetes authority, reached
  through a narrow authenticated Unix/mTLS protocol. T70 now owns that broker, its two runtime
  backends, immutable image/argv policy, attempt runner, and terminate-and-prove protocol in addition
  to the approved bounded renderer adapter and package/security work. The child remains networkless
  and credentialless; the application receives no raw OCI socket or workload-mutating service
  account; and T71 owns reviewed configurable budget values plus durable lease/recovery binding.
  Shared-worker native execution, PID-only proof, publication before unit termination, and a broad
  serializer fork remain prohibited.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
