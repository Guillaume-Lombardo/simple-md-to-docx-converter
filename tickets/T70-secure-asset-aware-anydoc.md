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
* 2026-09-05: Follow-up review made every response-file copy revalidate the exact incarnation
  before any subsequent copy or decode, including protocol-error paths. Raw archive validation now
  requires the exact fixed-path USTAR header, rejects GNU/PAX forms, and verifies both file-block
  padding and all trailing blocks are zero. The child accepts a response write only while the
  existing state is canonical `pending` for the same attempt; malformed, already-complete, and
  substituted-attempt states remain untouched. The shared fake runtime now rejects wrong or
  ill-typed expected attempt identities even while no response is available. Regression coverage
  includes post-copy identity substitution with an exact operation-local inspect count. The 706
  broker/reversion tests and all six real rootless Podman 5.4 integrations pass; hosted Podman 4.9
  and a fresh full-image build remain CI validation boundaries.
* 2026-09-05: Hosted fresh-image CI exposed that the smoke harness polled a child-owned atomic
  `0600` state file directly from the host. It now performs that content-free readiness check
  through `podman unshare`, preserving the child-only workspace mode and arbitrary UID; the full
  smoke passes against the controlled current-code overlay. CodeRabbit review also identified two
  valid conformance issues: the fake runtime now persists and enforces the exact policy channel
  ceilings, and the bounded command runner makes its stdin pipe nonblocking, retries temporary
  backpressure, and retains the absolute deadline when a child never reads. Regression tests cover
  all three paths. The 715 broker/reversion/container tests and all six real rootless Podman
  integrations pass; the fresh-image build and hosted Podman 4.9 boundary remain assigned to CI.
* 2026-09-05: The real-Podman workspace overlay now rejects incompatible base-image overrides
  before copying into a fixed interpreter layout. Its build preflight requires the exact executable
  venv, Python 3.14, matching prefix and site-packages membership, and an existing attempt-runner
  import resolving from that destination. Review then made the preflight independent of Python
  assertion optimization and inherited `PYTHON*` variables: it executes with an empty environment,
  ignores Python environment settings, and exits explicitly from boolean checks. A real adversarial
  base with `PYTHONOPTIMIZE=1`, a Python 3.9 interpreter at the expected executable path, and a decoy
  module is rejected without producing an image. The controlled overlay build, complete smoke, and
  all seven real rootless integrations pass.
* 2026-09-05: Follow-up review corrected the base-image preflight to execute the installed attempt
  runner rather than only resolve its module specification. The import is isolated from inherited
  Python environment settings, and every import exception including a zero-status `SystemExit` is
  converted into an explicit failed contract check. A real Python 3.14 base retaining the expected
  venv and module layout but with `anydoc` removed is rejected without producing an overlay image.
  The positive overlay build, complete smoke, and all eight real rootless Podman integrations pass.
* 2026-09-05: Added the host-native lifecycle-only broker process assembly. A bounded canonical
  owner-only configuration supplies every lifecycle, transport, Podman, inventory, and channel
  limit; the authentication key is read from a pre-existing single-link owner file with
  `O_NOFOLLOW` and `O_CLOEXEC`, while the SQLite database and sidecars are created under umask 077
  and revalidated as owner-only regular files. The factory derives the rootless UID and cgroup root,
  uses fixed absolute Podman and systemd commands with allowlisted environments, gives inventory and
  discovery the same capacity, and completes startup reconciliation before exposing the Unix
  listener. The transport now exposes a content-free stopping/fatal signal, and the process maps
  signals and internal failures to bounded exit behavior with an independent hard shutdown
  watchdog. The installed `markweave-broker` entry point retains a dependency-light failure boundary
  for minimal wheel profiles. Real subprocess coverage exercises the complete lifecycle, lock
  exclusion, SIGINT/SIGTERM, SIGKILL orphan reconciliation before readiness, malformed modes/config,
  and exact container/cgroup cleanup. The full broker boundary passes 29 tests against rootless
  Podman 5.4, the light selection passes 3,024 tests at 93.99% total and 90.13% branch coverage, and
  the clean-wheel installation probe passes. The public protocol remains v1; STAGE/COLLECT wire
  operations, T71 jobs/leases/publication, production budgets, mTLS, deployment manifests,
  Kubernetes, OCR, and UI remain excluded.
* 2026-09-05: Follow-up review corrected the hard shutdown proof boundary. An unsuccessful Unix
  handler drain no longer marks process shutdown complete or disarms the independent watchdog; the
  watchdog remains live through interpreter teardown and forces a bounded nonzero exit even when a
  non-daemon handler would otherwise keep the process alive. A real subprocess regression proves
  the hard deadline and content-free output. The 572-test broker selection, focused subprocess
  regression, Ruff, and `ty` pass.
* 2026-09-05: The final process review closed four additional authority and boundedness gaps. The
  container-domain selector now includes the dependency-light top-level broker entry point. A stop
  requested during startup reconciliation is preserved across socket setup, so the real Unix
  listener never begins admission and no CREATE or ACK reaches dispatch, including when the stop
  arrives immediately before `start()`. A single owner-only authority lock under the canonical
  EUID-derived runtime root is acquired before SQLite is opened and retained until successful
  handler drainage; two processes cannot share the per-UID Podman/label/cgroup authority even with
  entirely distinct state and socket paths.
  Configuration and key reads are nonblocking as well as no-follow, rejecting owner FIFOs without
  hanging, and extreme canonical JSON numbers map to the same bounded configuration failure. The
  574-test broker selection, targeted authority subprocess tests, Ruff, and `ty` pass.
* 2026-09-05: PR review follow-up made non-finite exponent-form JSON numbers fail during parsing,
  before canonical re-encoding, and made the missing-command unit case independent of the runner's
  actual UID. Cold-runner subprocess allowances no longer compete with the deliberately short hard
  watchdog assertion. The lifecycle-lock E2E now starts a second low-level Unix server against the
  same socket, so it exercises the socket lock independently of the separate per-EUID process
  authority lock while proving the first broker remains live. All 12 real process E2Es, 51 focused
  process/configuration tests, 73 Unix transport tests, Ruff, and `ty` pass.

* 2026-09-05: Added the separately versioned `markweave-reverse-broker-workspace` v1 Unix
  subprotocol without changing lifecycle protocol v1. STAGE authenticates the peer before reading
  a canonical bounded header or allocating its exact digest-bound source body, derives the durable
  unit incarnation, and returns a content-free receipt. A volatile unit-scoped replay ledger makes
  an exact lost-response retry idempotent without a second runtime copy and rejects substitutions.
  COLLECT is read-only and receipt-bound, returning canonical pending/failure headers or a bounded
  digest-bound result body; service and runtime/inventory failures fence readiness. Low-level Unix
  servers keep workspace operations disabled unless explicit policy channel ceilings are supplied,
  while production assembly passes those existing T71-owned policy inputs without adding defaults.
  Security coverage includes malformed, noncanonical, oversized, partial, slow/disconnected,
  replayed, and identity-substituted traffic, with no dispatch before exact payload, digest, and EOF
  validation. Real Unix and rootless Podman process E2E covers READY through CREATE, lost STAGE ACK
  replay, pending/success/failure COLLECT, TERMINATE, PROOF, ACK, restart reconciliation, and exact
  cleanup. The complete broker boundary passes 649 tests; the 3,084-test light selection reaches
  93.88% total and 90.04% branch coverage. Persistent T71 jobs/leases/publication, mTLS, deployment,
  Kubernetes, OCR, and public APIs remain excluded; no Kubernetes acceptance is claimed.

* 2026-09-05: Workspace protocol review follow-up now rediscovers and validates the exact live
  runtime incarnation before returning an idempotent STAGE receipt; absent, substituted, or failed
  discovery fences readiness without performing a second copy. COLLECT accepts only exact runtime
  response model types, the child channel's closed failure-category allowlist, and non-empty result
  bytes within both the staged request and runtime policy output ceilings. The final workspace wire
  encoder independently enforces its channel ceiling and rejects non-child categories. The Unix
  client validates the source and every declared content-limit invariant against its configured
  channel before encoding or creating a socket. Real pending-response coverage now uses a fixed
  content-free `/work/test.release` barrier instead of an elapsed-time assumption. The complete
  reviewed broker selection was corrected to 663 tests (624 unit, 18 Unix, 13 process, and 8
  Podman); the added absolute-deadline regression raises the current boundary to 664 tests (625
  unit plus the same integrations). All 13 real broker-process and 8 real Podman integrations pass,
  and the 3,098-test light selection reaches 93.88% total and 90.06% branch coverage.

* 2026-09-05: Post-rebase validation on main `64f8a14` required no product correction. The exact
  rebase head `53e3cb8` passes 141 focused workspace/Unix/service tests, the complete 664-test
  broker boundary, all 13 real broker-process and 8 real Podman integrations, Ruff, and `ty`. Main's
  additional light test raises that selection to 3,099 passing tests at 93.88% total and 90.06%
  branch coverage (4,195/4,658); changed application coverage is 94.23% (539/572 lines). The
  lifecycle v1 wire remains unchanged and no temporary Podman resource remains.

* 2026-09-05: Added an inert paired-channel mTLS transport without changing lifecycle v1 or
  workspace v1. Both peers require TLS 1.3 and certificates from the dedicated private CA, then
  bind the exact single SPIFFE URI SAN, role EKU, and one of at most two configured SHA-256 hashes
  of the exact leaf-certificate DER to one stable principal. The server issues a 256-bit CSPRNG
  exchange identity on the response channel; the separately authenticated request channel must
  present the same leaf and exchange, deliver one exact bounded v1 frame, and complete authenticated
  TLS `close_notify` before dispatch. Pairing is one-shot and replay-resistant, with one absolute
  operation deadline, explicit handshake/pending/handler capacities, bounded framing, and
  content-free failures. Real AF_INET/OpenSSL tests cover lifecycle and workspace success plus
  malformed, oversized, truncated, extra, replayed, substituted, mis-pinned, wrong-SAN, wrong-EKU,
  untrusted, expired, slow, disconnected, and shutdown traffic. All 98 focused mTLS tests and the
  complete 763-test broker unit/integration boundary pass; the new module reaches 94.08% statement
  and 90.63% branch coverage. Ruff and `ty` pass. Production process assembly, deployment,
  Kubernetes, T71 orchestration/publication, HTTP, and UI remain excluded from this slice.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
