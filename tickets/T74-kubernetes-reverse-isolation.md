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
