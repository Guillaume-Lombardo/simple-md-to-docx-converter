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
* 2026-09-04: Implemented the runtime-neutral broker core on verified `main` after the ClamAV
  rootless-network correction merged. The slice adds a strict 4 KiB canonical control protocol,
  immutable image/entrypoint/security-policy evidence, per-principal create replay fencing, an
  authenticated bounded SQLite WAL inventory, monotonic crash-consistent lifecycle transitions,
  atomic removed proof tombstones, startup/reconnect reconciliation, readiness gating, and a
  deterministic fault-injecting runtime. Reserved records are discarded without runtime mutation;
  ambiguous create intent, missing exit evidence, unknown label-only units, specification or
  incarnation mismatch, and incomplete termination all fail closed. The tombstone is deleted only
  after its exact principal/attempt/unit/proof acknowledgement while the create high-water mark is
  retained. This remains an internal core slice: Unix/mTLS transports, Podman/Kubernetes backends,
  bounded workspace data flow, and production assembly remain to be delivered before T70 can be
  completed.
* 2026-09-04: Finalized the broker core after three independent reviews of the exact revision. The
  authenticated inventory now detects membership deletion and substitution, rejects over-limit
  reconciliation without truncation, preserves policy evidence across configuration rollover, and
  revokes readiness on every internal storage fault. Termination returns the exact durable proof.
  Proof acknowledgement is indefinitely idempotent without an append-only receipt ledger: a
  missing unit is a state-free success, an active or mismatched unit is retained and rejected, and
  only an exact principal/attempt/unit/proof binding deletes a removed tombstone atomically while
  retaining the replay high-water mark. The exact light selection passed 2,723 tests at 94.15%
  total and 90.14% branch-only application coverage; changed application lines reached 95.62%, and
  the 298 broker tests reached 90.19% branch coverage. Ruff formatting/linting, `ty`, and diff
  validation also passed. Inventory, lifecycle, and protocol reviewers approved the exact revision
  without remaining findings.
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
* 2026-09-04: Two later exact-head runs showed that the fake scanner protocol fix was sound but
  rootless Podman network-name resolution remained transient on hosted runners: the application
  passed an exact `INSTREAM` probe through the scanner alias, then intermittently reported
  `UPLOAD_SCANNER_UNAVAILABLE` during the following workflow while every scanner process remained
  healthy. The distributed smoke and final-image E2E harnesses now derive the scanner address from
  Podman's trusted network inventory and install a deterministic container-local host mapping. They
  still execute the exact alias-based network probe before user workflows, so network wiring and
  scanner protocol coverage are preserved without adding application retries or weakening
  fail-closed behavior. The current product configuration and Compose topology are unchanged. The
  affected 83 harness tests, Ruff, `ty`, CI validation, the complete local distributed image smoke,
  and the immutable 0.5.2 distributed rollback rehearsal pass.
* 2026-09-04: Implemented the owner-only Linux Unix-socket boundary for the external isolation
  broker from verified `main` at `2119adb`. The transport derives its stable configured principal
  only after kernel `SO_PEERCRED` authentication, accepts exactly one canonical 4 KiB-bounded frame
  after client write EOF, applies one absolute operation deadline, and bounds its listen backlog,
  active handlers, and shutdown drain. Startup reserves and verifies an exact owner-mode socket
  inode before completing broker reconciliation and begins listening only afterward; active,
  insecure, replaced, or unprovably stale nodes fail closed. The client reciprocally verifies the
  filesystem node, server peer, response operation, request identity, attempt/unit/proof identities,
  and proof principal. A lifecycle `flock`, serialized deadline-aware dispatch gate, watchdog, and
  fatal drain state prevent stale-socket replacement, post-deadline admission, or restart over a
  blocked prior generation. No workspace data or raw runtime authority crosses this boundary. The
  76 focused dispatcher/transport tests, including 14 real AF_UNIX integrations, pass at 91.55%
  total branch coverage for the two new modules; the complete 374-test broker selection, Ruff,
  `ty`, and diff validation also pass. The exact light gate passes 2,801 tests at 94.07% total,
  90.21% application branch coverage, and 91.41% changed application-line coverage.
* 2026-09-04: Implemented the lifecycle-only rootless Podman backend behind the shared isolation
  runtime contract. The broker now creates or recovers one deterministic labelled container from
  the configured repository and immutable digest with pulls disabled, a fixed reverse-attempt
  entrypoint and empty argument vector, no network, no inherited environment or health command,
  no runtime logging, a read-only root without implicit writable root tmpfs mounts, no
  capabilities, no-new-privileges, a fixed non-root UID, private PID/UTS and no IPC namespace,
  no restart, and exact T71-supplied CPU, memory/swap, PID, workspace-tmpfs, and whole-second
  runtime deadline limits. Every shell-free Podman command has an absolute deadline, bounded
  output, process-group cleanup on every post-spawn exceptional exit, and content-free errors.
  Podman runs locally with an explicit hermetic environment and an empty owner-only hooks
  directory; the broker validates the realized mounts, environment, identity, namespaces,
  capabilities, security options, logging, cgroup placement, and resource limits before start.
  Exact create-command and immutable-label
  evidence makes a lost create response idempotently recoverable while rejecting image, policy,
  identity, or incarnation substitution. Rootless cgroup-v2 and seccomp are mandatory.
  Termination sends whole-container SIGKILL and separately requires canonical runtime-confirmed
  exit, an exact retained cgroup-v2 `populated=0` result, and no active exec sessions. The
  authenticated inventory persists that empty evidence before deletion; removal proof binds it to
  bounded exact-name and label-scoped post-delete absence, so crash recovery does not depend on
  finite runtime event retention. Absence or a kill/remove acknowledgement without the prior
  durable empty transition is never proof. Bounded label discovery
  rejects malformed, duplicate, unknown, and substituted units, and retained incarnation evidence
  reconstructs proof after removal-response loss. The 57 focused unit/shared-conformance tests and
  four real rootless Podman integrations cover a signal-resistant descendant, autonomous expiry
  while the broker is down, startup sweep/readiness refusal, and crash recovery after removal.
  The container CI domain builds a controlled derivative of the reviewed attempt image and runs
  this real lifecycle suite. The real fixture also proves that fixed UID/GID `1001:0` can traverse
  and write the bounded `/work` tmpfs without making it world-accessible. Workspace data
  transfer/result extraction, T71 supervisor and reverse
  channel ceilings, mTLS, Kubernetes, production composition, and application/job integration
  remain explicitly outside this slice. The complete 435-test broker selection passes, the new
  backend reaches 93% branch coverage, and the exact light gate passes 2,860 tests at 94.04% total
  and 90.11% application branch coverage; Ruff, `ty`, CI-selector validation, and diff validation
  also pass.
* 2026-09-05: Closed the independent lifecycle review findings. Whole-unit emptiness is now a
  positive exact cgroup-v2 `populated=0` proof retained across container exit, removal recovery is
  bound to the authenticated pre-delete empty transition instead of finite event logs, and the
  group-writable but not world-accessible workspace is usable by fixed UID/GID `1001:0`. Podman
  commands use a hermetic environment, force the local engine, disable hooks through a verified
  empty owner-only directory, and validate the complete realized isolation and resource spec before
  start. Unexpected post-spawn command-runner failures now kill and reap the process group. The 423
  broker unit tests and four real rootless Podman integrations pass. The canonical non-engine run
  completed 3,117 passing tests at 94.91% total and 90.95% application branch coverage; its 30
  PostgreSQL setup errors and three S3 failures are the documented unavailable local services, and
  the unrelated release process-reaping timing test remains the only other failure. Ruff and `ty`
  pass. No Kubernetes acceptance is claimed.
* 2026-09-05: Corrected the rootless systemd cgroup binding after exact-head review found that
  dash-expanded slice hierarchy could leave the broker reading an empty sibling cgroup. The
  backend now requires Podman's local rootless systemd cgroup manager and the exact delegated
  cgroup-v2 root, creates and verifies a dedicated deterministic parent and unit slice, and binds
  the realized Podman `CgroupPath` to both the live init process's `/proc` membership and
  `populated=1` in the exact unit slice before accepting a running container. Empty evidence still
  requires that same slice to reach `populated=0`, and cleanup stops the exact bounded systemd unit
  and positively verifies its disappearance. A real rootless test begins without the parent,
  independently follows Podman's inspected process cgroup, observes the populated transition
  while a signal-resistant descendant is alive and after whole-container termination, and leaves
  no dedicated slice behind. Wrong managers, roots, runtime paths, process memberships, and
  unconfirmed systemd cleanup fail closed. The 66 backend unit tests, complete 427-test broker
  unit selection, and four real Podman integrations pass; Ruff and `ty` pass. The canonical
  non-engine run completed 3,121 passing tests at 94.84% total and 90.85% application branch
  coverage; its 30 PostgreSQL setup errors, three unavailable-S3 failures, and the unrelated known
  release reaping timing failure remain documented local-environment limitations. No Kubernetes
  acceptance is claimed.
* 2026-09-05: Finished the lifecycle review by removing the shared systemd slice hierarchy. Each
  unit now receives one non-hierarchical deterministic slice directly under the verified rootless
  user-service cgroup, so removing one unit cannot race with sibling cleanup or retain a
  broker-created parent. Cleanup uses bounded `systemctl --user` commands and requires exact
  `loaded/inactive/dead` manager state, an empty manager `ControlGroup`, and filesystem absence;
  integration setup and teardown no longer remove managed cgroups directly. All four real Podman
  scenarios verify this manager and path state. The three successful broker lifecycle scenarios
  do so read-only and also require absence from active and all-unit listings; only the deliberate
  identity-substitution scenario uses explicit fixture recovery after the broker correctly refuses
  cleanup. The container CI selector now covers every broker module and the complete real-Podman
  fixture directory, preventing lifecycle dependency or fixture changes from bypassing the
  boundary suite. The 427 broker tests, 390
  selector/CI/container tests, four real Podman integrations, Ruff, and `ty` pass. The canonical
  non-engine selection completed 3,129 passing tests at 94.80% total and 90.80% application branch
  coverage, with the same 30 unavailable-PostgreSQL errors, three unavailable-S3 failures, and
  unrelated known release reaping timing failure. No Kubernetes acceptance is claimed.
* 2026-09-05: Restored compatibility with the hosted Ubuntu runner's Podman 4.9.3 after its first
  container-domain run exposed the older exact inspect projection. Podman 4.9 joins the configured
  entrypoint vector into one string, while Podman 5.4 returns the vector; the broker now accepts
  only those two exact representations while still requiring the exact broker-authored create
  command, labels, image digest, policy, and complete realized isolation spec. A successful create
  that fails validation is rolled back by exact container ID and cgroup identity; both cleanup
  boundaries are attempted, an inactive empty precreated cgroup leaf is removed safely, and any
  unconfirmed cleanup remains a content-free failure. Unit tests cover both Podman projections,
  near substitutions, recovered-container non-removal, malformed identity output, systemd and
  filesystem failures, and bounded cgroup evidence. Follow-up review then restricted failed-create
  rollback to the canonical ID returned by the successful create response, required exact-ID
  absence plus empty label-scoped discovery before cgroup cleanup, and extended the same cleanup
  guarantee across `BaseException` interruptions. The next hosted run exposed crun's documented
  cgroup-v2 systemd `container` subgroup: live process binding now accepts only the exact inspected
  scope or that scope plus `/container`, while retaining exact `State.CgroupPath` and populated
  parent-leaf evidence and rejecting every other descendant or format. A subsequent hosted run
  passed that runtime check and exposed only the integration assertion's former single-form
  assumption; the boundary test now uses the same exact two-form predicate before continuing
  through product termination and cleanup. Failed creates that prove
  both exact-name and label-scoped absence now remove their empty precreated cgroup without
  weakening lost-response recovery. The controlled fixture installs signal handlers before fork,
  and only the parent publishes readiness after its resistant child exists. The local Podman 5.4.2
  lifecycle suite remains four-for-four. The 119 backend tests, two fixture-ordering tests, and
  2,933-test light selection pass at 90.09% application
  branch coverage, correcting the hosted run's 89.77% result. Hosted Podman 4.9.3 remains to be
  reconfirmed by CI after independent review. No Kubernetes acceptance is claimed.
* 2026-09-05: Added the next internal Podman workspace slice without exposing it through the public
  Unix protocol or adding T71 job/lease/publication behavior. T71-supplied input and output channel
  ceilings are now mandatory policy inputs and are bound into schema-v2 policy evidence,
  immutable runtime labels, the create command, and the allowlisted realized environment. The
  broker stages exactly one deterministic minimal tar stream containing fixed request files, an
  attempt-bound pending state, and a final commit marker into the bounded `/work` tmpfs. It
  collects only fixed response files from a complete attempt-bound state, parses returned tar
  bytes without extraction, rejects non-regular, linked, escaping, extra, truncated, malformed, or
  oversized archives, and revalidates the exact unit/incarnation before and after every copy. The
  child waits for the request commit, publishes result and metadata before atomically committing
  the response, and remains alive until the existing whole-container terminate-and-prove path
  removes its tmpfs. Six real rootless Podman integrations now include a successful PDF workspace
  round trip and autonomous crash cleanup after staging, alongside the four lifecycle scenarios;
  they retain the exact Podman 4.9/5.4 cgroup compatibility contract. Focused unit/security tests
  cover pending and partial responses, channel bounds and policy evidence, strict tar paths/types/
  links/padding, streaming command bounds, and pre/post-copy identity substitution. No document
  data enters labels, logs, or inventory. Production budget values, the public transport,
  persistent reverse jobs, publication, production assembly, mTLS, Kubernetes, and OCR remain
  outside this slice; no Kubernetes acceptance is claimed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
