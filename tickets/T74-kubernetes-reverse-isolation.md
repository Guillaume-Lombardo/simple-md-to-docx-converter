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
* Prove the stable isolation unit is exited, empty, and removed using CRI/cgroup evidence; Pod
  deletion, absence, force deletion, or an API acknowledgement alone is insufficient.
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
