---
ticket: T74
linear_id: G1L-571
linear_url: https://linear.app/g1lom/issue/G1L-571/t74-design-and-implement-the-kubernetes-reverse-isolation-backend
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T74 - Design and implement the Kubernetes reverse-isolation backend

## Objective

Design and implement an optional Kubernetes reverse-conversion isolation backend without weakening
the termination-proof and resource-containment contract already proven by the T70 rootless Podman
backend.

## Acceptance criteria

* Keep the application worker and attempt workload free of raw OCI, CRI, node, or workload-mutating
  credentials.
* Provide a separately reviewed trusted node-attestation component on a dedicated worker pool if
  Kubernetes remains the selected deployment target.
* Enforce fixed broker-authored image, argv, identity, network, credential, mount, CPU, memory,
  PID/descendant, workspace/ephemeral, and autonomous deadline policy at the runtime/kernel boundary.
* Use a memory-backed bounded workspace, fixed node-level PID policy, default-deny egress including
  node-local destinations, and fail-closed node fencing.
* Prove the stable isolation unit is exited, empty, and removed using CRI/cgroup evidence. After
  complete CRI evidence proves every container exited on the unchanged fenced node, a complete
  negative lookup of the exact previously bound cgroup is accepted as kernel evidence that the
  cgroup became empty before its runtime-managed removal. Pod deletion, Pod absence, force
  deletion, an incomplete lookup, or an API acknowledgement alone is insufficient.
* Preserve T70 inventory, reconciliation, tombstone, proof acknowledgement, and both authenticated
  broker transport contracts.
* Add unit, integration, real-cluster, restart/recovery, failure, security, and exact-image E2E
  coverage for every boundary and failure mode.
* Do not make T71-T73 or the reverse-conversion delivery depend on this optional backend.

## Dependencies

* T70
* T71

## Implementation boundary

* Own only the optional Kubernetes backend, node attester, dedicated-pool deployment artifacts, and
  Kubernetes-specific proof/security tests.
* Do not weaken or replace the T70 Podman contract and do not expose node or cluster authority to
  the application or attempt child.

## Quality requirements

* Preserve every T70 fail-closed isolation, reconciliation, and termination-proof invariant.
* Require real-cluster integration and E2E evidence; mocked Pod API behavior is insufficient.
* Keep repository artifacts and user-facing errors in English.

## Progress

* 2026-09-10: Implemented and validated the selected RuntimeClass-scoped runc wrapper on the local
  k3s 1.35/containerd 2.2 boundary. A real workload reached `Running`; kernel mountinfo and the
  concrete inspector independently reported `/work` as a 32 MiB tmpfs with exact
  `nodev,noexec,nosuid,rw` flags, while the accepted isolated CNI state and `pids.max=64` remained
  intact. A workload missing `/work` failed closed with `StartError` before runc, and replacing the
  installed wrapper bytes made the node fence fail. The fence and evidence now bind the exact
  runtime-handler and wrapper digests, and the attester receives only read-only mounts of those
  files. The previously blocked attester image now builds and its minimal import/closed-failure
  smoke probes pass after separating the bounded command runner and making broker exports lazy;
  no reverse-conversion parsing dependencies are installed in that image. Probe namespaces,
  RuntimeClass, labels, taint, and five host assets were removed, and k3s is inactive/disabled.
  The mandatory dedicated-pool, published-digest, full broker/attester/attempt exact-image matrix
  remains unexecuted because this machine is a shared single-node development cluster.
* 2026-09-10: The product manager selected the RuntimeClass-scoped OCI runtime-wrapper solution for
  the Kubernetes 1.35 `emptyDir` mount-option gap. The trusted node wrapper must be fixed by the
  dedicated
  containerd runtime handler, digest-attested as part of the node fence, add only
  `nodev,nosuid,noexec` to a present `/work` tmpfs before the attempt process starts, fail closed on
  any malformed or unexpected mount, and leave the CRI pause sandbox unchanged. The node attester
  must continue to verify the actual resulting mount independently. Implementation and real-k3s
  validation resume under this decision.
* 2026-09-10: The live concrete-inspector probe reached a second architecture decision point after
  passing the selected CNI, CRI, cgroup, and node-fence checks. On k3s/Kubernetes 1.35, the required
  memory-backed `emptyDir` workspace is actually mounted `rw,relatime`; `nodev`, `noexec`, and
  `nosuid` are absent. The inspector faithfully returned only `rw`, so the T74 policy correctly
  rejects the sandbox. Kubernetes 1.35 exposes no mount-options field for `emptyDir`; StorageClass
  mount options do not apply. Preserving the approved workspace contract therefore requires a new
  trusted CSI/node mount component or OCI runtime hook/wrapper with mount authority. The alternative
  is an explicit security-contract relaxation. Either choice materially changes architecture or
  acceptance criteria and requires product/security approval. All probe resources were cleaned up
  and k3s is inactive/disabled.
* 2026-09-10: Implemented the selected hardened standard-containerd design. The node attester now
  reaches containerd only through a bounded Envoy Unix-socket proxy that permits the five exact CRI
  v1 inspection methods and denies mutation; a live proxy probe successfully listed sandboxes and
  rejected `StopPodSandbox`. Added the digest-attested `00-markweave-isolated` CNI, which creates an
  unpeered dummy `eth0` with `192.0.2.1/32`, disables forwarding and IPv6 on that interface, and
  removes routes and neighbors. A live k3s/containerd Pod reached `Running` with that address.
  Kernel `/proc/<sandbox-pid>/net` evidence confirmed only `lo`/`eth0`, only loopback and the dummy
  address, no IPv4 or non-loopback IPv6 routes, and no ARP neighbor. The attester now enforces those
  live kernel facts and the exact CNI asset digests. The probe also proved that
  `/proc/<pid>/root/proc/sys/net` reflects the reader namespace, so the design does not falsely use
  it as sandbox forwarding evidence; the digest-attested CNI checks forwarding during ADD, while
  the attested unpeered and unrouted state prevents egress. All temporary cluster and host changes
  were removed and k3s returned to inactive/disabled. The dedicated exact-image acceptance matrix
  remains required. ShellCheck, Ruff format/lint, `ty`, `git diff --check`, and 136 focused tests
  pass; the three changed Python security modules have 93.21% combined branch coverage.
* 2026-09-10: The product manager selected the hardened standard-containerd design: place a
  separately trusted allowlist proxy between the node attester and the CRI socket, expose only the
  exact inspection RPCs required by T74, and use an isolated non-loopback dummy interface with no
  peer or routes. Network evidence must come from the sandbox's kernel network namespace and prove
  the exact interface, address, route, and peer-isolation contract; the digest-attested CNI must
  disable and check forwarding during namespace creation, and CRI CNI metadata is not sufficient
  on its own. Implementation and real-k3s validation resume under this decision.
* 2026-09-10: Independent review of PR #225 retained five findings. Local follow-up now bounds
  official Kubernetes Node/Pod reads with the inspector operation timeout, rejects `.` and `..`
  cgroup components for both sandbox and removal inspection, requires the rendered Kubernetes
  termination grace period to exceed the attester watchdog, and declares the digest-pinned attester
  image amd64-only. Ruff, `ty`, `git diff --check`, and 49 focused tests pass. The reported
  multi-exception `SyntaxError` is not valid for the mandated Python 3.14 target (PEP 758), as also
  demonstrated by the passing import and process tests. The remaining valid security finding is
  architectural: a read-only filesystem mount of the containerd socket still permits mutation RPCs
  over that socket. T74 therefore additionally requires a decision between a narrow separately
  trusted CRI allowlist proxy, explicit acceptance of direct root-attester CRI authority, or stopping
  the Kubernetes backend; the previously recorded CNI decision remains unresolved as well.
* 2026-09-10: Implemented the reviewable portion preceding the CNI decision: a bounded read-only
  Kubernetes/CRI/cgroup-v2 inspector, root-only production attester process, strict immutable
  configuration, root-owned authenticated ledger wiring, least-privilege RBAC, hardened node-local
  DaemonSet, digest-pinned minimal attester Containerfile, containerd runtime handler, and effective
  kubelet configuration drop-in. The solution-3 absent-cgroup proof is covered by fail-closed tests.
  Ruff, `ty`, `git diff --check`, 125 focused tests, and 119 focused coverage tests pass; the three
  changed security modules reach 94.23% branch coverage. The canonical engine-excluded run passed
  4,169 tests and reached 94.63% total coverage, with 44 PostgreSQL setup errors, three RustFS
  failures, and two pre-existing release-integration failures caused by unavailable external
  configuration or environment behavior. Its only change-related packaging failure was fixed and
  reverified. The attester image build could not complete because repeated checksum-pinned `uv`
  downloads from GitHub reset or stalled; no image/E2E success is claimed.
* 2026-09-10: A second authorized real-k3s probe validated the dedicated runtime handler, effective
  kubelet config drop-in (`podPidsLimit=64`, `cpuCFSQuotaPeriod=100ms`), exact node labels/taint,
  and the concrete inspector's positive fence. The inspector correctly rejected a real Flannel
  sandbox as non-isolated. A positive loopback-only probe then exposed a CRI constraint:
  containerd refuses `RunPodSandbox` when CNI reports no non-loopback Pod IP; adding `127.0.0.1/8`
  to the result is still rejected. Standard Kubernetes/containerd therefore cannot create the
  currently required `lo`-only attempt sandbox. The namespace, RuntimeClass, labels, taint, CNI
  probe files, kubelet/runtime drop-ins, and service changes were removed, and k3s was restored to
  inactive/disabled. T74 is blocked on a product/security decision: either approve an isolated
  non-loopback dummy interface with stronger kernel/netns attestation, approve maintaining a
  patched CRI/runtime that accepts loopback-only Pod IPs, or retain the current contract and stop
  Kubernetes backend work.
* 2026-09-10: The product manager selected the bounded absent-cgroup proof design. T74 may treat a
  complete negative lookup of the exact previously attested cgroup as empty evidence only after
  complete CRI enumeration proves every bound container exited and the node fence and all retained
  identities remain unchanged. This intentionally adapts the temporal proof contract to observed
  containerd/kubelet cgroup lifecycle; Pod state, Pod absence, deletion acknowledgement, incomplete
  enumeration, or an unbound path remain insufficient. Implementation resumes under this explicit
  decision.
* 2026-09-10: Authorized real-k3s inspection on `codex-dev` exposed a lifecycle
  contract blocker before implementation of the concrete inspector. On k3s `v1.35.5+k3s1`,
  containerd `2.2.3-k3s1`, and cgroup v2, an exact disposable probe showed that the bounded
  workload container's cgroup is removed automatically after its process exits, while the stable
  Pod cgroup remains populated by the CRI pause sandbox. Kubelet subsequently stops the sandbox
  and removes the Pod cgroup automatically. The current contract requires positive durable
  `EXITED`, `EMPTY`, and `REMOVED` transitions, with `EMPTY` recorded before the broker deletes the
  exact Pod UID; neither available cgroup therefore supplies the required stable lifecycle. Treating
  an already absent workload cgroup as proof that it was empty would change the approved proof
  semantics. Preserving a separately removable stable cgroup instead requires a new trusted
  node-level runtime wrapper or supervisor with cgroup-mutation authority, beyond the currently
  approved read-only inspector. The probe namespace was deleted and k3s was returned to its initial
  inactive/disabled state. T74 remains blocked pending the product/security choice between those
  two designs; fake or Pod-API-only evidence is still not accepted.
* 2026-09-08: Partial durable-recovery PR
  [#224](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/224) was
  squash-merged as `a8ac43abee44609860594fbbf40bbe781ddf2a5f` after independent functional
  and security reviews, a completed incremental CodeRabbit review, and resolution of every review
  thread. Exact-head CI
  [run 34271382507](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/34271382507)
  and exact-main CI
  [run 34274314282](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/34274314282)
  are green, including branch and changed-line coverage gates, both rootless exact-image E2E
  variants, document engines, PostgreSQL, and S3/RustFS suites. T74 remains In Progress: the
  concrete bounded CRI/cgroup inspector, attester process and deployment, dedicated-pool fencing,
  loopback-only CNI and node-local egress proof, and real-k3s acceptance matrix remain mandatory.
* 2026-09-08: Added crash-consistent Kubernetes creation and lifecycle recovery. The broker now
  persists an authenticated, bounded, content-free creation binding before any Kubernetes API
  mutation, migrates authenticated inventory schema v2 to v3 atomically, and retains recovery
  state until durable worker proof acknowledgement. The node attester now uses a bounded
  HMAC-authenticated SQLite ledger with exact lifecycle replay, creation-time policy preservation,
  pre-bind Pod adoption through full re-attestation, exact container identity continuity, and
  idempotent acknowledgement cleanup. Kubernetes API and mTLS operations reject non-finite or
  unbounded timeouts, and malformed responses remain content-free. Independent functional and
  security reviews approved exact commit `43bcaf2`; 1,014 broker/Kubernetes tests, Ruff format and
  lint, `ty`, and diff checks pass. The canonical suite still requires unavailable PostgreSQL and
  S3 configuration. The concrete bounded CRI/cgroup inspector, attester process and durable-secret
  deployment, loopback-only CNI, node routing and firewall proof, and exact-image real-k3s E2E
  matrix remain mandatory before T74 can be completed.
* 2026-09-08: Added the concrete namespace-scoped Kubernetes Pod/exec control adapter and the
  node-routed TLS 1.3 mTLS attester client/service transport. The adapters enforce fixed bounded
  commands and paths, identity revalidation, UID-preconditioned deletion, retry-safe staging,
  exact peer certificate pins, server-owned binding state, closed messages, sanitized failures,
  and strict exit/empty/removal evidence chaining. The reference deployment, operator guide,
  optional pinned `kubernetes==35.0.0` dependency, package matrix, and unit/real-loopback-mTLS tests
  are updated. Ruff format/check, `ty`, and 81 focused tests pass with 92.49% focused branch
  coverage. The canonical engine-excluded run reached 4,049 passes and 94.57% total coverage; its
  remaining 44 PostgreSQL setup errors, three RustFS/S3 failures, and two release-integration
  failures require unavailable external configuration or predate this slice. Real k3s validation
  and the concrete CRI/cgroup inspector still require privileged cluster startup and inspection.
* 2026-09-08: The product manager authorized the Kubernetes cluster on `codex-dev` for T74.
  The host has k3s `v1.35.5+k3s1` installed with Traefik and ServiceLB disabled, but the service is
  currently disabled and inactive and its kubeconfig is root-readable only. Implementation resumes
  from verified `main`; starting and accessing the real cluster will require the separately guarded
  privileged host operation before real-boundary validation can run.
* 2026-09-08: Fixed the final incremental CodeRabbit finding by preserving the seeded fake
  runtime's attempt identity for restart-oriented workspace collection tests. The 268-test focused
  selection, Ruff, `ty`, and `git diff --check` pass.
* 2026-09-08: Closed the independent follow-up review by making attempt and principal identities a
  mandatory fail-closed `RuntimeUnit` contract across Kubernetes, Podman, persisted recovery, and
  test runtimes; deletion grace metadata is now rejected without a deletion timestamp. The proposed
  host-port/mTLS topology is explicitly documented as non-operational pending its real-cluster
  gates. All 852 broker unit tests pass, as does the 268-test focused selection spanning Kubernetes
  integration, reconciliation, Podman, and workspace behavior; Ruff, `ty`, and `git diff --check`
  pass.
* 2026-09-08: Addressed the locally testable findings and recorded a proposed, non-operational
  host-port mTLS contract from CodeRabbit review `5139760727`: node-specific topology and credential
  placeholders, malformed observation and
  discovery error normalization, persisted attempt/principal reconciliation, terminating-Pod
  metadata, a configurable positive interpreter-memory margin, exception-chain leakage checks,
  explicit identity-invariant naming, and exact RBAC assertions. The focused Kubernetes suite
  passes 58 tests with 97.44% line and 92.36% branch coverage across the runtime and attester.
  The attester server, broker client, node-name routing, peer authorization, CNI/firewall behavior,
  and exact-node binding remain unimplemented real-cluster gates. T74 remains In Progress and the
  mandatory real-cluster/exact-image blocker is unchanged.
* 2026-09-08: Non-draft partial foundation PR [#222](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/222) was published at reviewed commit `1bcce038ed8e3a69934511610553390ebc66ef3e`. T74 remains In Progress; exact-head CI and CodeRabbit are pending, and the real-cluster/exact-image completion blocker is unchanged.
* 2026-09-08: Hardened the reviewable foundation after independent security review. The policy
  digest now uses an annotation instead of an invalid 64-character label; the attester runs in a
  namespace outside the broker's `pods/exec` scope and the broker has no Node-reading ClusterRole;
  Pod `fsGroup` ownership makes the memory-backed workspace usable by the non-root attempt; and
  attestation now binds sandbox-netns isolation plus observed cgroup `cpu.max`, `memory.max`,
  `pids.max`, and tmpfs path/size/mount flags. Canonical projection accepts Kubernetes API defaults
  and equivalent resource quantities while rejecting injected workloads or authored-field drift.
  A follow-up hardening pass now rejects every unrecognized observed Pod field or toleration,
  permits only explicitly validated Kubernetes defaults, and binds the observed Pod UID/node name.
  The focused correction suite passes 51 unit/integration tests and the two new modules reach
  97.01% line and 91.79% branch coverage. The mandatory real-cluster and exact-image gates remain
  blocked by the absence of an authorized Kubernetes context and are not claimed complete.
* 2026-09-08: Added the runtime-neutral Kubernetes isolation proof core, a separately reviewable
  node-attestation policy engine, the dedicated fenced-pool reference topology, deployment and
  proof documentation, unit coverage, and SQLite broker restart/tombstone integration coverage.
  The focused suite passes 22 unit tests and one integration test; the two new Python modules reach
  91% combined branch coverage. Ruff and `ty` pass. The canonical engine-excluded suite reaches
  3,989 passes and 95% repository coverage; PostgreSQL/RustFS tests cannot start without their
  required environment, and one process timeout test that failed under full-suite load passed on
  immediate isolated rerun.
* 2026-09-08: Mandatory production completion is blocked because no authorized Kubernetes context
  or dedicated isolation pool is available. The remaining work needs the actual CRI/kubelet/CNI
  schemas to implement and validate the read-only inspector, authenticated attester service,
  namespace-scoped Pod/exec adapter, exact-image E2E, restart/recovery, deadline, OOM, PID,
  workspace, egress, node-fencing, substitution, and proof-failure cases. Fake Pod API behavior is
  explicitly not treated as acceptance evidence.
* 2026-09-08: Resumed by product-manager decision. Implementation starts from the verified current
  `main` in an isolated worktree and retains the optional, non-blocking boundary.
* 2026-09-05: Split from T70 by product-manager decision after feasibility proved that the standard
  Pod API and RuntimeClass cannot satisfy per-attempt PID containment, hard ephemeral-storage
  limits, node-local egress isolation, or proof that a sandbox is empty and removed. T70 proceeds
  with Podman as its sole required backend; this ticket is intentionally non-blocking for T71-T73.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
