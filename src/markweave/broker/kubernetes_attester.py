"""Node-local CRI/cgroup attestation policy for Kubernetes reverse attempts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import UUID

from markweave.broker.kubernetes_runtime import (
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
    manifest_digest,
)
from markweave.broker.models import BrokerPolicy, EvidenceDigest

_DEDICATED_TAINT = "reverse.markweave.dev/dedicated"
_POOL_LABEL = "reverse.markweave.dev/isolation-pool"
_FENCE_LABEL = "reverse.markweave.dev/node-fence"
_CGROUP_V2 = 2


@dataclass(frozen=True, slots=True)
class NodeFenceSnapshot:
    """Content-free node facts collected from kubelet, CNI and cgroup v2."""

    node_name: str
    node_uid: UUID
    ready: bool
    pool_name: str
    fence_revision: str
    dedicated_taint_value: str
    cgroup_version: int
    pod_pids_limit: int
    cpu_quota_period_micros: int
    network_interfaces: tuple[str, ...]
    forwarding_enabled: bool


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    """Bounded positive CRI and cgroup facts for one Pod UID."""

    pod_uid: UUID
    node_uid: UUID
    sandbox_id: str
    cgroup_path: str
    observed_manifest: dict[str, object]
    container_ids: tuple[str, ...]
    container_states: tuple[str, ...]
    sandbox_ready: bool
    cgroup_populated: bool
    descendant_pids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RemovalSnapshot:
    """Positive complete node lookup after deletion of an attested sandbox."""

    pod_uid: UUID
    node_uid: UUID
    sandbox_id: str
    cgroup_path: str
    cri_lookup_complete: bool
    sandbox_present: bool
    cgroup_lookup_complete: bool
    cgroup_present: bool


class CriCgroupInspector(Protocol):
    """Read-only adapter implemented by the separately trusted node process."""

    def node_fence(self, node_name: str) -> NodeFenceSnapshot: ...

    def sandbox(self, pod_uid: UUID) -> SandboxSnapshot: ...

    def removal(self, pod_uid: UUID) -> RemovalSnapshot: ...


def _evidence(kind: str, payload: object) -> EvidenceDigest:
    encoded = json.dumps(
        {"kind": kind, "payload": payload, "schema_version": 1},
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return EvidenceDigest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


class NodeAttestationEngine:
    """Fail-closed policy engine around read-only CRI and cgroup observations."""

    def __init__(self, inspector: CriCgroupInspector) -> None:
        if not all(
            callable(getattr(inspector, name, None))
            for name in ("node_fence", "sandbox", "removal")
        ):
            raise ValueError("Kubernetes node inspector is invalid")
        self._inspector = inspector

    def bind(
        self,
        pod: KubernetesPodIdentity,
        *,
        expected_manifest_digest: EvidenceDigest,
        expected_policy: BrokerPolicy,
        expected_pool: str,
        expected_fence_revision: str,
    ) -> KubernetesSandboxIdentity:
        """Bind the Pod UID to one fenced node, CRI sandbox and stable cgroup."""

        try:
            node = self._inspector.node_fence(pod.node_name)
            sandbox = self._inspector.sandbox(pod.pod_uid)
        except Exception as error:
            raise KubernetesRuntimeError(
                "Kubernetes node attestation failed"
            ) from error
        if (
            type(node) is not NodeFenceSnapshot
            or node.node_name != pod.node_name
            or node.node_uid != pod.node_uid
            or node.ready is not True
            or node.pool_name != expected_pool
            or node.fence_revision != expected_fence_revision
            or node.dedicated_taint_value != expected_pool
            or node.cgroup_version != _CGROUP_V2
            or node.pod_pids_limit != expected_policy.limits.pid_limit
            or node.cpu_quota_period_micros != expected_policy.limits.cpu_period_micros
            or node.network_interfaces != ("lo",)
            or node.forwarding_enabled is not False
        ):
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")
        if (
            type(sandbox) is not SandboxSnapshot
            or sandbox.pod_uid != pod.pod_uid
            or sandbox.node_uid != pod.node_uid
            or manifest_digest(sandbox.observed_manifest) != expected_manifest_digest
            or sandbox.sandbox_ready is not True
            or not sandbox.container_ids
            or len(sandbox.container_ids) != len(sandbox.container_states)
            or any(
                state not in {"CREATED", "RUNNING", "EXITED"}
                for state in sandbox.container_states
            )
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox attestation is invalid")
        fence = _evidence(
            "node-fence",
            {
                "cpu_quota_period_micros": node.cpu_quota_period_micros,
                "dedicated_taint": {_DEDICATED_TAINT: node.dedicated_taint_value},
                "fence_label": {_FENCE_LABEL: node.fence_revision},
                "network_interfaces": node.network_interfaces,
                "node_uid": str(node.node_uid),
                "pod_pids_limit": node.pod_pids_limit,
                "pool_label": {_POOL_LABEL: node.pool_name},
            },
        )
        try:
            return KubernetesSandboxIdentity(
                sandbox.sandbox_id, sandbox.cgroup_path, fence
            )
        except ValueError as error:
            raise KubernetesRuntimeError(
                "Kubernetes sandbox attestation is invalid"
            ) from error

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        """Prove all CRI containers exited; a Pod phase or API result is ignored."""

        snapshot = self._sandbox(unit)
        if (
            snapshot.sandbox_ready is not False
            or not snapshot.container_states
            or any(state != "EXITED" for state in snapshot.container_states)
        ):
            raise KubernetesRuntimeError("Kubernetes exit is unconfirmed")
        return _evidence(
            "exit",
            {
                "container_ids": snapshot.container_ids,
                "container_states": snapshot.container_states,
                "node_uid": str(snapshot.node_uid),
                "pod_uid": str(snapshot.pod_uid),
                "sandbox_id": snapshot.sandbox_id,
            },
        )

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        """Prove the attested stable cgroup has neither population nor descendants."""

        if type(exit_evidence) is not EvidenceDigest:
            raise KubernetesRuntimeError("Kubernetes exit evidence is invalid")
        snapshot = self._sandbox(unit)
        if snapshot.cgroup_populated is not False or snapshot.descendant_pids != ():
            raise KubernetesRuntimeError("Kubernetes stable unit is not empty")
        return _evidence(
            "empty",
            {
                "cgroup_path": snapshot.cgroup_path,
                "descendant_count": 0,
                "exit_evidence": exit_evidence.value,
                "node_uid": str(snapshot.node_uid),
                "pod_uid": str(snapshot.pod_uid),
                "populated": False,
                "sandbox_id": snapshot.sandbox_id,
            },
        )

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        """Bind prior emptiness to complete negative CRI and cgroup lookups."""

        if type(empty_evidence) is not EvidenceDigest:
            raise KubernetesRuntimeError("Kubernetes empty evidence is invalid")
        try:
            snapshot = self._inspector.removal(unit.pod.pod_uid)
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes removal is unconfirmed") from error
        if (
            type(snapshot) is not RemovalSnapshot
            or snapshot.pod_uid != unit.pod.pod_uid
            or snapshot.node_uid != unit.pod.node_uid
            or snapshot.sandbox_id != unit.sandbox.sandbox_id
            or snapshot.cgroup_path != unit.sandbox.cgroup_path
            or snapshot.cri_lookup_complete is not True
            or snapshot.sandbox_present is not False
            or snapshot.cgroup_lookup_complete is not True
            or snapshot.cgroup_present is not False
        ):
            raise KubernetesRuntimeError("Kubernetes removal is unconfirmed")
        return _evidence(
            "removed",
            {
                **asdict(snapshot),
                "empty_evidence": empty_evidence.value,
                "node_uid": str(snapshot.node_uid),
                "pod_uid": str(snapshot.pod_uid),
            },
        )

    def _sandbox(self, unit: KubernetesRuntimeUnit) -> SandboxSnapshot:
        if type(unit) is not KubernetesRuntimeUnit:
            raise KubernetesRuntimeError("Kubernetes runtime unit is invalid")
        try:
            snapshot = self._inspector.sandbox(unit.pod.pod_uid)
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes sandbox lookup failed") from error
        if (
            type(snapshot) is not SandboxSnapshot
            or snapshot.pod_uid != unit.pod.pod_uid
            or snapshot.node_uid != unit.pod.node_uid
            or snapshot.sandbox_id != unit.sandbox.sandbox_id
            or snapshot.cgroup_path != unit.sandbox.cgroup_path
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox identity changed")
        return snapshot
