"""Kubernetes runtime identities, policy configuration and control/attestation ports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnitState,
    RuntimeIncarnation,
    RuntimeRecoveryBinding,
)

if TYPE_CHECKING:
    from markweave.reversions.models import (
        ReverseAttemptRequest,
        ReverseAttemptResponse,
    )


_DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")

_SANDBOX_ID = re.compile(r"[0-9a-f]{32,128}\Z")

_CGROUP_PATH = re.compile(r"/[A-Za-z0-9_.:/@-]{1,1023}\Z")

_FIXED_ENTRYPOINT = ("python", "-m", "markweave.reversions.attempt_main")

_MANAGED_LABEL = "reverse.markweave.dev/managed"

_UNIT_LABEL = "reverse.markweave.dev/unit-id"

_ATTEMPT_LABEL = "reverse.markweave.dev/attempt-id"

_PRINCIPAL_LABEL = "reverse.markweave.dev/principal-id"

_POLICY_LABEL = "reverse.markweave.dev/policy-revision"

_SPECIFICATION_ANNOTATION = "reverse.markweave.dev/policy-specification"

_POOL_LABEL = "reverse.markweave.dev/isolation-pool"

_FENCE_LABEL = "reverse.markweave.dev/node-fence"

_TAINT_KEY = "reverse.markweave.dev/dedicated"

_MAX_DNS_NAME_BYTES = 253

_MAX_IMAGE_REPOSITORY_BYTES = 255

_RESOURCE_PATH_DEPTH = 2


class KubernetesRuntimeError(RuntimeError):
    """Content-free Kubernetes isolation failure."""


class KubernetesAttestationNotReady(KubernetesRuntimeError):
    """Trusted inspector reports that a sandbox is not observable yet."""


@dataclass(frozen=True, slots=True)
class KubernetesPodIdentity:
    """Exact Kubernetes Pod incarnation returned by the control plane."""

    namespace: str
    name: str
    pod_uid: UUID
    node_name: str
    unit_id: UUID
    attempt_id: UUID
    principal_id: UUID
    policy_revision: str
    policy_specification: EvidenceDigest

    def __post_init__(self) -> None:
        if (
            type(self.namespace) is not str
            or _DNS_LABEL.fullmatch(self.namespace) is None
            or type(self.name) is not str
            or _DNS_LABEL.fullmatch(self.name) is None
            or type(self.pod_uid) is not UUID
            or type(self.node_name) is not str
            or not self.node_name
            or len(self.node_name) > _MAX_DNS_NAME_BYTES
            or type(self.unit_id) is not UUID
            or type(self.attempt_id) is not UUID
            or type(self.principal_id) is not UUID
            or type(self.policy_revision) is not str
            or _DNS_LABEL.fullmatch(self.policy_revision) is None
            or type(self.policy_specification) is not EvidenceDigest
        ):
            raise ValueError("Kubernetes Pod identity is invalid")


@dataclass(frozen=True, slots=True)
class KubernetesSandboxIdentity:
    """Node-attested CRI sandbox and stable cgroup identity."""

    sandbox_id: str
    cgroup_path: str
    node_uid: UUID
    node_fence: EvidenceDigest
    container_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.sandbox_id) is not str
            or _SANDBOX_ID.fullmatch(self.sandbox_id) is None
            or type(self.cgroup_path) is not str
            or _CGROUP_PATH.fullmatch(self.cgroup_path) is None
            or type(self.node_uid) is not UUID
            or type(self.node_fence) is not EvidenceDigest
            or type(self.container_ids) is not tuple
            or not self.container_ids
            or len(set(self.container_ids)) != len(self.container_ids)
            or any(
                type(value) is not str or _SANDBOX_ID.fullmatch(value) is None
                for value in self.container_ids
            )
        ):
            raise ValueError("Kubernetes sandbox identity is invalid")


@dataclass(frozen=True, slots=True)
class KubernetesRuntimeUnit:
    """Opaque identity jointly bound by Kubernetes and the node attester."""

    unit_id: UUID
    incarnation: RuntimeIncarnation
    pod: KubernetesPodIdentity
    sandbox: KubernetesSandboxIdentity
    recovery_binding: RuntimeRecoveryBinding | None = None

    def __post_init__(self) -> None:
        if (
            type(self.unit_id) is not UUID
            or type(self.incarnation) is not RuntimeIncarnation
            or type(self.pod) is not KubernetesPodIdentity
            or type(self.sandbox) is not KubernetesSandboxIdentity
            or (
                self.recovery_binding is not None
                and (
                    type(self.recovery_binding) is not RuntimeRecoveryBinding
                    or self.recovery_binding.backend != "kubernetes"
                )
            )
            or self.incarnation.incarnation_id != self.pod.pod_uid
        ):
            raise ValueError("Kubernetes runtime unit identity is invalid")

    @property
    def attempt_id(self) -> UUID:
        """Return the attempt identity bound into the discovered Pod."""

        return self.pod.attempt_id

    @property
    def principal_id(self) -> UUID:
        """Return the principal identity bound into the discovered Pod."""

        return self.pod.principal_id


@dataclass(frozen=True, slots=True)
class KubernetesRuntimeConfig:
    """Broker-owned fixed Kubernetes placement and identity policy."""

    namespace: str
    service_account: str
    runtime_class: str
    pool_name: str
    node_fence_revision: str
    run_as_uid: int
    run_as_gid: int
    interpreter_memory_margin_bytes: int

    def __post_init__(self) -> None:
        strings = (
            self.namespace,
            self.service_account,
            self.runtime_class,
            self.pool_name,
            self.node_fence_revision,
        )
        if (
            any(
                type(value) is not str or _DNS_LABEL.fullmatch(value) is None
                for value in strings
            )
            or type(self.run_as_uid) is not int
            or self.run_as_uid <= 0
            or type(self.run_as_gid) is not int
            or self.run_as_gid <= 0
            or type(self.interpreter_memory_margin_bytes) is not int
            or self.interpreter_memory_margin_bytes <= 0
        ):
            raise ValueError("Kubernetes runtime configuration is invalid")


@dataclass(frozen=True, slots=True)
class KubernetesAttestationContract:
    """Broker-authored values the node attester must prove exactly."""

    pod_contract: Mapping[str, object]
    manifest_digest: EvidenceDigest
    policy: BrokerPolicy
    pool: str
    fence_revision: str


class KubernetesControlPlane(Protocol):
    """Narrow Pod-only authority held exclusively by the trusted broker."""

    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity: ...

    def find(self, name: str) -> KubernetesPodIdentity | None: ...

    def stage_request(
        self, pod: KubernetesPodIdentity, request: ReverseAttemptRequest
    ) -> None: ...

    def try_collect_response(
        self, pod: KubernetesPodIdentity, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None: ...

    def terminate(self, pod: KubernetesPodIdentity) -> None: ...

    def delete(self, pod: KubernetesPodIdentity) -> None: ...

    def absent(self, pod: KubernetesPodIdentity) -> bool: ...

    def discover(
        self, *, namespace: str, labels: Mapping[str, str], limit: int
    ) -> tuple[KubernetesPodIdentity, ...]: ...


class KubernetesNodeAttester(Protocol):
    """Separate node-local CRI/cgroup authority on the dedicated pool."""

    def bind(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity: ...

    def adopt_create_intent(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity: ...

    def recover(
        self,
        unit: KubernetesRuntimeUnit,
        contract: KubernetesAttestationContract,
        lifecycle_state: ManagedUnitState,
    ) -> KubernetesSandboxIdentity: ...

    def recover_create_intent(
        self,
        pod: KubernetesPodIdentity,
        proposed_contract: KubernetesAttestationContract,
    ) -> tuple[KubernetesSandboxIdentity, KubernetesAttestationContract]: ...

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest: ...

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest: ...

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest: ...

    def acknowledge(
        self, unit: KubernetesRuntimeUnit, removal_evidence: EvidenceDigest
    ) -> None: ...
