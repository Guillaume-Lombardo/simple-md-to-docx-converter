---
ticket: T70
linear_id: G1L-538
linear_url: https://linear.app/g1lom/issue/G1L-538/t70-implement-secure-asset-aware-anydoc-conversion
status: In Progress
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
  reviewed T71-configured wall-time deadline is applied autonomously by the runtime at creation, so
  it remains effective across worker or broker process failure. The broker maintains a mandatory
  bounded crash-consistent content-free inventory/tombstone; it is authenticated and integrity-
  protected and contains no document data or secret. It records the broker-authored stable identity
  before runtime create. Immutable broker-authored runtime labels supplement discovery but never
  replace inventory, and neither labels nor managed identities are controllable by user/document
  input. At startup and reconnect it idempotently sweeps every inventoried orphan, hard-terminates
  it, and proves it empty and removed. The broker refuses readiness and every create request until
  reconciliation completes successfully. It durably records runtime-confirmed exit and empty before
  removal, records removed before proof return, and retains the tombstone until durable worker/T71
  proof acknowledgement. A crash between kill/removal and proof resumes from inventory and runtime
  state; absence, delete acknowledgement, or Kubernetes force-delete alone is not proof. The
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
  overlapping attempt, bounded IPC, autonomous deadline enforcement across broker failure,
  startup/reconnect orphan sweeping, creation/readiness refusal during incomplete reconciliation,
  write-ahead identity, crash-consistent transition ordering, tombstone acknowledgement, idempotent
  proof reconstruction after a crash between kill and proof, and fail-closed behavior when
  termination proof is unavailable. Explicitly reject absence, delete acknowledgement, and
  Kubernetes force-delete as standalone proof.

## Dependencies

* T08
* T18
* T20
* T69

## Implementation boundary

* Own the external broker service and authenticated bounded protocol, Podman and Kubernetes
  isolation backends, immutable image/argv and child-security policy, supervised disposable-attempt
  runner, mandatory crash-consistent managed-unit inventory/tombstones, supplementary runtime-label
  discovery, reconciliation, and terminate-and-prove protocol, anydoc adapter, bounded internal
  renderer compatibility
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
  and prove that runtime authority is absent from the application and child. Cover restart with a
  live orphan, runtime expiry while the broker is down, incomplete sweep readiness/create refusal,
  forged or user-controlled inventory/labels, write-ahead failure, transition persistence failure,
  tombstone retention/acknowledgement, Kubernetes force-delete, and crashes both before and after
  removal but before proof.
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
* 2026-09-04: Independent review added the broker crash/restart contract. T70 must apply the
  T71-configured deadline through the runtime at creation, discover managed units through bounded
  persistent content-free inventory with immutable broker-authored labels as supplementary
  evidence, sweep orphans before readiness or creation, and resume proof after a crash between
  kill/removal and acknowledgement. Worker recovery remains blocked until the proof is durably
  recorded.
* 2026-09-04: Follow-up review made the bounded crash-consistent content-free inventory/tombstone
  mandatory because labels disappear with a removed unit. Identity must be durable before create;
  exit/empty must be durable before removal; removed must be durable before proof return; and the
  tombstone remains until durable worker/T71 acknowledgement. Runtime labels only supplement the
  idempotent sweep. Absence, delete acknowledgement, and Kubernetes force-delete are insufficient
  proof.
* 2026-09-04: Started from verified main
  `e7c872ee70980eea11a112678856a62886a336a2` after T69 and its repository mirror passed exact-main
  gates. The first cohesive delivery slice is the inert reverse-attempt core: exact optional anydoc
  dependency and supply-chain ownership, fail-closed compatibility adapter, bounded asset
  normalization, canonical manifest and deterministic Markdown/ZIP packaging, fixed child
  protocol, minimal attempt image, and unit/golden/real-library/container coverage. It adds no
  HTTP, persistence, job orchestration, broker authority, or production assembly.
* 2026-09-04: Implemented and independently approved the inert reverse-attempt core slice. The
  child now owns format detection, a fail-closed pinned anydoc adapter, safe HTTP(S)-only hyperlink
  admission, bounded asset normalization with strict container/polyglot checks, structured
  source-position asset references, a canonical content-free manifest, and pre-sized deterministic
  Markdown/ZIP output. The fixed workspace protocol binds every response to its expected attempt
  identity and exposes no runtime authority. The dedicated minimal image passed real DOCX/PNG and
  SVG/CairoSVG smoke tests under an arbitrary UID with a read-only root and no network. Its closed
  evidence bundle includes the exact OCI archive, image SBOMs, the embedded 113-component anydoc
  Cargo SBOM, license validation, and separate vulnerability reports. Independent security and
  integration reviews reported no remaining findings. Ruff formatting/linting, `ty`, 250 focused
  reverse/anydoc tests, 392 CI/container/release tests, and 2,654 of 2,655 broad local tests passed;
  the sole process-reaping timing failure passed immediately in isolation. PostgreSQL, S3, and the
  three excluded document engines were unavailable or intentionally excluded from that broad local
  command. T70 remains In Progress because the external broker, runtime backends, termination proof,
  and production integration belong to later slices.
* 2026-09-04: Closed the two first-run CI gaps before merge. The rootless container smoke now maps
  only its temporary bind workspace to the fixed arbitrary child UID, and the light coverage gate
  explicitly selects the real-anydoc integration suite through the `light_coverage` marker. The
  exact light selection passes 2,408 tests at 94.09% total and 90.02% branch-only application
  coverage; the corrected container job also passes on GitHub Actions.
* 2026-09-04: Addressed CodeRabbit's three valid functional findings without accepting its unsafe
  blanket signature-scan suggestion. Unresolved anchors now consume nested image occurrences once;
  speculative/duplicate note bodies preserve pinned rendering semantics while a retained-occurrence
  mask removes orphan references and normalized assets before packaging; unavailable empty-alt
  images are not retained when they emit no Markdown. Asset admission now rejects structurally
  parseable ZIP polyglots in every image format without rejecting benign magic bytes in valid PNG,
  JPEG, GIF, SVG, or WebP payloads. The exact light gate passes 2,427 tests at 94.13% total and
  90.13% branch-only coverage.
* 2026-09-04: Hardened the container test scanner after three exact-head CI runs exposed a
  readiness race in the test double. The bounded fake ClamAV endpoint now answers the same exact
  `zPING` health protocol used by runtime diagnostics before accepting `INSTREAM`, retains
  fail-closed behavior for unknown and malformed commands, and uses a bounded 64-connection listen
  backlog for concurrent application and worker startup. Real TCP integration coverage proves
  `PING`, unknown-command rejection, and a complete scan on separate connections. Independent
  review approved the fix without findings. The canonical local non-engine selection passed 2,674
  tests at 95.21% total coverage; its 30 PostgreSQL setup errors and three S3 failures reflect the
  intentionally absent service endpoints, while the known process-reaping timing test was the only
  unrelated local failure. The 64 directly affected container/protocol tests, Ruff, `ty`, CI
  validation, and diff validation pass. CodeRabbit's exact-head review identified one valid
  test-lifecycle finding; the integration server now shuts down and joins in a `finally` block so a
  failed protocol assertion cannot leave a non-daemon test thread running.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
