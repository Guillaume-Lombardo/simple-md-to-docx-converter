"""Node-local CRI/cgroup attestation policy for Kubernetes reverse attempts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Protocol
from uuid import UUID

from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesAttestationNotReady,
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
    manifest_digest,
    project_observed_pod,
)
from markweave.broker.models import EvidenceDigest

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


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    """Bounded positive CRI and cgroup facts for one Pod UID."""

    pod_uid: UUID
    node_uid: UUID
    sandbox_id: str
    cgroup_path: str
    observed_pod: dict[str, object]
    container_ids: tuple[str, ...]
    container_states: tuple[str, ...]
    sandbox_ready: bool
    network_interfaces: tuple[str, ...]
    forwarding_enabled: bool
    cpu_quota_micros: int
    cpu_period_micros: int
    memory_max_bytes: int
    pids_max: int
    workspace_filesystem: str
    workspace_mount_path: str
    workspace_size_bytes: int
    workspace_mount_flags: tuple[str, ...]
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
        self._contracts: dict[UUID, tuple[KubernetesAttestationContract, UUID]] = {}

    def bind(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity:
        """Bind the Pod UID to one fenced node, CRI sandbox and stable cgroup."""

        node = _inspector_call(
            lambda: self._inspector.node_fence(pod.node_name),
            "Kubernetes node attestation failed",
        )
        sandbox = _inspector_call(
            lambda: self._inspector.sandbox(pod.pod_uid),
            "Kubernetes node attestation failed",
        )
        if (
            type(node) is not NodeFenceSnapshot
            or node.node_name != pod.node_name
            or node.ready is not True
            or node.pool_name != contract.pool
            or node.fence_revision != contract.fence_revision
            or node.dedicated_taint_value != contract.pool
            or node.cgroup_version != _CGROUP_V2
            or node.pod_pids_limit != contract.policy.limits.pid_limit
            or node.cpu_quota_period_micros != contract.policy.limits.cpu_period_micros
        ):
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")
        if (
            type(sandbox) is not SandboxSnapshot
            or sandbox.pod_uid != pod.pod_uid
            or sandbox.node_uid != node.node_uid
            or not _observed_identity_matches(sandbox.observed_pod, pod)
            or manifest_digest(contract.pod_contract) != contract.manifest_digest
            or project_observed_pod(sandbox.observed_pod, contract.pod_contract)
            != contract.pod_contract
            or sandbox.sandbox_ready is not True
            or sandbox.network_interfaces != ("lo",)
            or sandbox.forwarding_enabled is not False
            or not sandbox.container_ids
            or len(sandbox.container_ids) != len(sandbox.container_states)
            or any(state != "RUNNING" for state in sandbox.container_states)
            or sandbox.cpu_quota_micros != contract.policy.limits.cpu_quota_micros
            or sandbox.cpu_period_micros != contract.policy.limits.cpu_period_micros
            or sandbox.memory_max_bytes != contract.policy.limits.memory_bytes
            or sandbox.pids_max != contract.policy.limits.pid_limit
            or sandbox.workspace_filesystem != "tmpfs"
            or sandbox.workspace_mount_path != "/work"
            or sandbox.workspace_size_bytes != contract.policy.limits.workspace_bytes
            or sandbox.workspace_mount_flags != ("nodev", "noexec", "nosuid", "rw")
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox attestation is invalid")
        fence = _evidence(
            "node-fence",
            {
                "cpu_quota_period_micros": node.cpu_quota_period_micros,
                "dedicated_taint": {_DEDICATED_TAINT: node.dedicated_taint_value},
                "fence_label": {_FENCE_LABEL: node.fence_revision},
                "node_uid": str(node.node_uid),
                "pod_pids_limit": node.pod_pids_limit,
                "pool_label": {_POOL_LABEL: node.pool_name},
            },
        )
        binding = _evidence(
            "sandbox-binding",
            {
                "cgroup_path": sandbox.cgroup_path,
                "container_ids": sandbox.container_ids,
                "cpu_max": (
                    sandbox.cpu_quota_micros,
                    sandbox.cpu_period_micros,
                ),
                "manifest": contract.manifest_digest.value,
                "memory_max": sandbox.memory_max_bytes,
                "network_interfaces": sandbox.network_interfaces,
                "node_fence": fence.value,
                "node_uid": str(sandbox.node_uid),
                "pids_max": sandbox.pids_max,
                "pod_uid": str(sandbox.pod_uid),
                "sandbox_forwarding_enabled": sandbox.forwarding_enabled,
                "sandbox_id": sandbox.sandbox_id,
                "workspace_filesystem": sandbox.workspace_filesystem,
                "workspace_mount_flags": sandbox.workspace_mount_flags,
                "workspace_mount_path": sandbox.workspace_mount_path,
                "workspace_size_bytes": sandbox.workspace_size_bytes,
            },
        )
        try:
            identity = KubernetesSandboxIdentity(
                sandbox.sandbox_id, sandbox.cgroup_path, sandbox.node_uid, binding
            )
        except ValueError:
            failure = KubernetesRuntimeError(
                "Kubernetes sandbox attestation is invalid"
            )
            raise failure from None
        previous = self._contracts.setdefault(pod.pod_uid, (contract, node.node_uid))
        if previous != (contract, node.node_uid):
            raise KubernetesRuntimeError("Kubernetes sandbox attestation is invalid")
        return identity

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        """Prove all CRI containers exited; a Pod phase or API result is ignored."""

        self._revalidate_fence(unit)
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
        self._revalidate_fence(unit)
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
        self._revalidate_fence(unit)
        snapshot = _inspector_call(
            lambda: self._inspector.removal(unit.pod.pod_uid),
            "Kubernetes removal is unconfirmed",
        )
        if (
            type(snapshot) is not RemovalSnapshot
            or snapshot.pod_uid != unit.pod.pod_uid
            or snapshot.node_uid != unit.sandbox.node_uid
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

    def _revalidate_fence(self, unit: KubernetesRuntimeUnit) -> None:
        if type(unit) is not KubernetesRuntimeUnit:
            raise KubernetesRuntimeError("Kubernetes runtime unit is invalid")
        retained = self._contracts.get(unit.pod.pod_uid)
        if retained is None:
            raise KubernetesRuntimeError("Kubernetes runtime unit is invalid")
        contract, expected_node_uid = retained
        node = _inspector_call(
            lambda: self._inspector.node_fence(unit.pod.node_name),
            "Kubernetes node attestation failed",
        )
        if (
            type(node) is not NodeFenceSnapshot
            or node.node_uid != expected_node_uid
            or node.node_name != unit.pod.node_name
            or node.ready is not True
            or node.pool_name != contract.pool
            or node.fence_revision != contract.fence_revision
            or node.dedicated_taint_value != contract.pool
            or node.cgroup_version != _CGROUP_V2
            or node.pod_pids_limit != contract.policy.limits.pid_limit
            or node.cpu_quota_period_micros != contract.policy.limits.cpu_period_micros
        ):
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")

    def _sandbox(self, unit: KubernetesRuntimeUnit) -> SandboxSnapshot:
        if type(unit) is not KubernetesRuntimeUnit:
            raise KubernetesRuntimeError("Kubernetes runtime unit is invalid")
        snapshot = _inspector_call(
            lambda: self._inspector.sandbox(unit.pod.pod_uid),
            "Kubernetes sandbox lookup failed",
        )
        if (
            type(snapshot) is not SandboxSnapshot
            or snapshot.pod_uid != unit.pod.pod_uid
            or snapshot.node_uid != unit.sandbox.node_uid
            or snapshot.sandbox_id != unit.sandbox.sandbox_id
            or snapshot.cgroup_path != unit.sandbox.cgroup_path
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox identity changed")
        return snapshot


def _observed_identity_matches(observed: object, pod: KubernetesPodIdentity) -> bool:
    if not isinstance(observed, Mapping):
        return False
    metadata = observed.get("metadata")
    specification = observed.get("spec")
    return (
        isinstance(metadata, Mapping)
        and isinstance(specification, Mapping)
        and metadata.get("name") == pod.name
        and metadata.get("namespace") == pod.namespace
        and metadata.get("uid") == str(pod.pod_uid)
        and specification.get("nodeName") == pod.node_name
    )


def _inspector_call[T](operation: Callable[[], T], message: str) -> T:
    try:
        return operation()
    except KubernetesAttestationNotReady:
        raise
    except Exception:
        failure = KubernetesRuntimeError(message)
    raise failure
