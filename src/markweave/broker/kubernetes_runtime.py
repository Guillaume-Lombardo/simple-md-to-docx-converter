"""Fail-closed Kubernetes backend for reverse-attempt isolation.

Kubernetes workload authority and node-level CRI/cgroup attestation are kept in
separate components. Application workers continue to use the T70 broker
protocol and receive neither authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeIncarnation,
    policy_specification_evidence,
)
from markweave.broker.ports import RuntimeUnit
from markweave.reversions.models import ReverseAttemptRequest, ReverseAttemptResponse

_DNS_LABEL = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")
_SANDBOX_ID = re.compile(r"[0-9a-f]{32,128}\Z")
_CGROUP_PATH = re.compile(r"/[A-Za-z0-9_.:/@-]{1,1023}\Z")
_FIXED_ENTRYPOINT = ("python", "-m", "markweave.reversions.attempt_main")
_MANAGED_LABEL = "reverse.markweave.dev/managed"
_UNIT_LABEL = "reverse.markweave.dev/unit-id"
_ATTEMPT_LABEL = "reverse.markweave.dev/attempt-id"
_PRINCIPAL_LABEL = "reverse.markweave.dev/principal-id"
_POLICY_LABEL = "reverse.markweave.dev/policy-revision"
_SPECIFICATION_LABEL = "reverse.markweave.dev/policy-specification"
_POOL_LABEL = "reverse.markweave.dev/isolation-pool"
_FENCE_LABEL = "reverse.markweave.dev/node-fence"
_TAINT_KEY = "reverse.markweave.dev/dedicated"
_MAX_DNS_NAME_BYTES = 253
_MAX_IMAGE_REPOSITORY_BYTES = 255


class KubernetesRuntimeError(RuntimeError):
    """Content-free Kubernetes isolation failure."""


@dataclass(frozen=True, slots=True)
class KubernetesPodIdentity:
    """Exact Kubernetes Pod incarnation returned by the control plane."""

    namespace: str
    name: str
    pod_uid: UUID
    node_name: str
    node_uid: UUID
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
            or type(self.node_uid) is not UUID
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
    node_fence: EvidenceDigest

    def __post_init__(self) -> None:
        if (
            type(self.sandbox_id) is not str
            or _SANDBOX_ID.fullmatch(self.sandbox_id) is None
            or type(self.cgroup_path) is not str
            or _CGROUP_PATH.fullmatch(self.cgroup_path) is None
            or type(self.node_fence) is not EvidenceDigest
        ):
            raise ValueError("Kubernetes sandbox identity is invalid")


@dataclass(frozen=True, slots=True)
class KubernetesRuntimeUnit:
    """Opaque identity jointly bound by Kubernetes and the node attester."""

    unit_id: UUID
    incarnation: RuntimeIncarnation
    pod: KubernetesPodIdentity
    sandbox: KubernetesSandboxIdentity

    def __post_init__(self) -> None:
        if (
            type(self.unit_id) is not UUID
            or type(self.incarnation) is not RuntimeIncarnation
            or type(self.pod) is not KubernetesPodIdentity
            or type(self.sandbox) is not KubernetesSandboxIdentity
            or self.incarnation.incarnation_id != self.pod.pod_uid
        ):
            raise ValueError("Kubernetes runtime unit identity is invalid")


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
        ):
            raise ValueError("Kubernetes runtime configuration is invalid")


class KubernetesControlPlane(Protocol):
    """Narrow Pod-only authority held exclusively by the trusted broker."""

    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity: ...

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
        *,
        expected_manifest_digest: EvidenceDigest,
        expected_policy: BrokerPolicy,
        expected_pool: str,
        expected_fence_revision: str,
    ) -> KubernetesSandboxIdentity: ...

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest: ...

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest: ...

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest: ...


def manifest_digest(manifest: Mapping[str, object]) -> EvidenceDigest:
    """Return canonical content-free evidence for one broker-authored manifest."""

    try:
        encoded = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise KubernetesRuntimeError("Kubernetes manifest is invalid") from error
    return EvidenceDigest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


class KubernetesIsolationRuntime:
    """Kubernetes backend preserving the shared positive-proof lifecycle."""

    def __init__(
        self,
        *,
        image_repository: str,
        policy: BrokerPolicy,
        config: KubernetesRuntimeConfig,
        control_plane: KubernetesControlPlane,
        node_attester: KubernetesNodeAttester,
    ) -> None:
        if (
            type(image_repository) is not str
            or not image_repository
            or len(image_repository) > _MAX_IMAGE_REPOSITORY_BYTES
            or "@" in image_repository
            or any(part in {"", ".", ".."} for part in image_repository.split("/"))
            or type(policy) is not BrokerPolicy
            or type(config) is not KubernetesRuntimeConfig
            or not _implements_control_plane(control_plane)
            or not _implements_attester(node_attester)
        ):
            raise ValueError("Kubernetes runtime configuration is invalid")
        self._image_repository = image_repository
        self._policy = policy
        self._config = config
        self._control = control_plane
        self._attester = node_attester
        self._known: dict[UUID, KubernetesRuntimeUnit] = {}

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> KubernetesRuntimeUnit:
        """Create and positively bind one exact immutable-policy Pod sandbox."""

        if (
            type(unit) is not ManagedUnit
            or unit.state is not ManagedUnitState.CREATE_INTENT
            or type(policy) is not BrokerPolicy
            or policy != self._policy
            or unit.policy_revision != policy.revision
            or unit.policy_specification != policy_specification_evidence(policy)
        ):
            raise KubernetesRuntimeError("Kubernetes create contract is invalid")
        manifest = self._manifest(unit, policy)
        try:
            pod = self._control.create(manifest)
            self._verify_pod_identity(unit, pod)
            sandbox = self._attester.bind(
                pod,
                expected_manifest_digest=manifest_digest(manifest),
                expected_policy=policy,
                expected_pool=self._config.pool_name,
                expected_fence_revision=self._config.node_fence_revision,
            )
            result = KubernetesRuntimeUnit(
                unit.unit_id,
                RuntimeIncarnation(pod.pod_uid, unit.policy_specification),
                pod,
                sandbox,
            )
            previous = self._known.setdefault(unit.unit_id, result)
            if previous != result:
                raise KubernetesRuntimeError("Kubernetes incarnation conflicts")
            return result
        except KubernetesRuntimeError:
            raise
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes create failed") from error

    def stage_request(
        self, runtime_unit: RuntimeUnit, request: ReverseAttemptRequest
    ) -> None:
        verified = self._coerce(runtime_unit)
        if type(request) is not ReverseAttemptRequest:
            raise KubernetesRuntimeError("Kubernetes workspace request is invalid")
        try:
            self._control.stage_request(verified.pod, request)
        except Exception as error:
            raise KubernetesRuntimeError(
                "Kubernetes workspace staging failed"
            ) from error

    def try_collect_response(
        self, runtime_unit: RuntimeUnit, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        verified = self._coerce(runtime_unit)
        if type(expected_attempt_id) is not UUID:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        try:
            response = self._control.try_collect_response(
                verified.pod, expected_attempt_id
            )
        except Exception as error:
            raise KubernetesRuntimeError(
                "Kubernetes workspace collection failed"
            ) from error
        if response is not None and response.attempt_id != expected_attempt_id:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        return response

    def hard_terminate(self, runtime_unit: RuntimeUnit) -> None:
        verified = self._coerce(runtime_unit)
        try:
            self._control.terminate(verified.pod)
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes termination failed") from error

    def confirm_exit(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        try:
            evidence = self._attester.confirm_exit(verified)
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes exit is unconfirmed") from error
        return _require_evidence(evidence, "Kubernetes exit is unconfirmed")

    def confirm_empty(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        exit_evidence = self.confirm_exit(verified)
        try:
            evidence = self._attester.confirm_empty(verified, exit_evidence)
        except Exception as error:
            raise KubernetesRuntimeError(
                "Kubernetes stable unit is not empty"
            ) from error
        return _require_evidence(evidence, "Kubernetes stable unit is not empty")

    def remove(self, runtime_unit: RuntimeUnit) -> None:
        verified = self._coerce(runtime_unit)
        try:
            self._control.delete(verified.pod)
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes removal failed") from error

    def confirm_removed(
        self, runtime_unit: RuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        if type(empty_evidence) is not EvidenceDigest:
            raise KubernetesRuntimeError("Kubernetes empty evidence is invalid")
        try:
            cri_evidence = self._attester.confirm_removed(verified, empty_evidence)
            if self._control.absent(verified.pod) is not True:
                raise KubernetesRuntimeError("Kubernetes removal is unconfirmed")
        except KubernetesRuntimeError:
            raise
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes removal is unconfirmed") from error
        self._known.pop(verified.unit_id, None)
        return _require_evidence(cri_evidence, "Kubernetes removal is unconfirmed")

    def discover(self, *, limit: int) -> tuple[KubernetesRuntimeUnit, ...]:
        if type(limit) is not int or limit <= 0:
            raise KubernetesRuntimeError("Kubernetes discovery limit is invalid")
        try:
            pods = self._control.discover(
                namespace=self._config.namespace,
                labels={_MANAGED_LABEL: "1"},
                limit=limit,
            )
        except Exception as error:
            raise KubernetesRuntimeError("Kubernetes discovery failed") from error
        if type(pods) is not tuple or len(pods) > limit:
            raise KubernetesRuntimeError("Kubernetes discovery exceeds its limit")
        discovered: list[KubernetesRuntimeUnit] = []
        seen: set[UUID] = set()
        for pod in pods:
            if type(pod) is not KubernetesPodIdentity:
                raise KubernetesRuntimeError("Kubernetes discovery identity is invalid")
            managed = ManagedUnit(
                pod.attempt_id,
                pod.unit_id,
                AuthenticatedPrincipal(pod.principal_id),
                1,
                pod.policy_revision,
                pod.policy_specification,
                ManagedUnitState.CREATE_INTENT,
                1,
            )
            self._verify_pod_identity(managed, pod)
            if (
                pod.policy_revision != self._policy.revision
                or pod.policy_specification
                != policy_specification_evidence(self._policy)
            ):
                raise KubernetesRuntimeError("Kubernetes discovery policy is invalid")
            sandbox = self._attester.bind(
                pod,
                expected_manifest_digest=manifest_digest(
                    self._manifest(managed, self._policy)
                ),
                expected_policy=self._policy,
                expected_pool=self._config.pool_name,
                expected_fence_revision=self._config.node_fence_revision,
            )
            runtime_unit = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
            previous = self._known.setdefault(pod.unit_id, runtime_unit)
            if previous != runtime_unit or runtime_unit.unit_id in seen:
                raise KubernetesRuntimeError("Kubernetes discovery identity is invalid")
            seen.add(runtime_unit.unit_id)
            discovered.append(runtime_unit)
        return tuple(sorted(discovered, key=lambda item: str(item.unit_id)))

    def _coerce(self, value: RuntimeUnit) -> KubernetesRuntimeUnit:
        if type(value) is not KubernetesRuntimeUnit:
            raise KubernetesRuntimeError("Kubernetes runtime unit is invalid")
        if self._known.get(value.unit_id) != value:
            raise KubernetesRuntimeError("Kubernetes runtime unit is unknown")
        return value

    def _verify_pod_identity(
        self, unit: ManagedUnit, pod: KubernetesPodIdentity
    ) -> None:
        if (
            type(pod) is not KubernetesPodIdentity
            or pod.namespace != self._config.namespace
            or pod.name != f"markweave-reverse-{unit.unit_id.hex}"
            or pod.unit_id != unit.unit_id
            or pod.attempt_id != unit.attempt_id
            or pod.principal_id != unit.principal.principal_id
            or pod.policy_revision != unit.policy_revision
            or pod.policy_specification != unit.policy_specification
        ):
            raise KubernetesRuntimeError("Kubernetes Pod identity is invalid")

    def _manifest(self, unit: ManagedUnit, policy: BrokerPolicy) -> dict[str, object]:
        config = self._config
        cpu_millicores = _cpu_millicores(policy)
        labels = {
            _ATTEMPT_LABEL: str(unit.attempt_id),
            _MANAGED_LABEL: "1",
            _POLICY_LABEL: policy.revision,
            _PRINCIPAL_LABEL: str(unit.principal.principal_id),
            _SPECIFICATION_LABEL: unit.policy_specification.value.removeprefix(
                "sha256:"
            ),
            _UNIT_LABEL: str(unit.unit_id),
        }
        name = f"markweave-reverse-{unit.unit_id.hex}"
        seconds = max(1, math.ceil(policy.limits.wall_time_millis / 1000))
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": config.namespace,
                "labels": labels,
            },
            "spec": {
                "activeDeadlineSeconds": seconds,
                "automountServiceAccountToken": False,
                "containers": [
                    {
                        "name": "attempt",
                        "image": f"{self._image_repository}@{policy.image_digest}",
                        "imagePullPolicy": "IfNotPresent",
                        "command": list(_FIXED_ENTRYPOINT),
                        "args": [],
                        "env": [
                            {
                                "name": "MARKWEAVE_REVERSE_MAX_INPUT_BYTES",
                                "value": str(policy.channel_limits.max_input_bytes),
                            },
                            {
                                "name": "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES",
                                "value": str(policy.channel_limits.max_output_bytes),
                            },
                        ],
                        "resources": {
                            "limits": {
                                "cpu": f"{cpu_millicores}m",
                                "memory": str(policy.limits.memory_bytes),
                                "ephemeral-storage": str(policy.limits.workspace_bytes),
                            },
                            "requests": {
                                "cpu": f"{cpu_millicores}m",
                                "memory": str(policy.limits.memory_bytes),
                                "ephemeral-storage": str(policy.limits.workspace_bytes),
                            },
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                            "privileged": False,
                            "readOnlyRootFilesystem": True,
                            "runAsGroup": config.run_as_gid,
                            "runAsNonRoot": True,
                            "runAsUser": config.run_as_uid,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                        "workingDir": "/work",
                    }
                ],
                "dnsPolicy": "None",
                "dnsConfig": {"nameservers": ["127.0.0.1"]},
                "enableServiceLinks": False,
                "hostIPC": False,
                "hostNetwork": False,
                "hostPID": False,
                "nodeSelector": {
                    _POOL_LABEL: config.pool_name,
                    _FENCE_LABEL: config.node_fence_revision,
                },
                "restartPolicy": "Never",
                "runtimeClassName": config.runtime_class,
                "serviceAccountName": config.service_account,
                "setHostnameAsFQDN": False,
                "shareProcessNamespace": False,
                "terminationGracePeriodSeconds": 0,
                "tolerations": [
                    {
                        "effect": "NoSchedule",
                        "key": _TAINT_KEY,
                        "operator": "Equal",
                        "value": config.pool_name,
                    }
                ],
                "volumes": [
                    {
                        "name": "work",
                        "emptyDir": {
                            "medium": "Memory",
                            "sizeLimit": str(policy.limits.workspace_bytes),
                        },
                    }
                ],
            },
        }


def _require_evidence(value: object, message: str) -> EvidenceDigest:
    if type(value) is not EvidenceDigest:
        raise KubernetesRuntimeError(message)
    return value


def _implements_control_plane(value: object) -> bool:
    return all(
        callable(getattr(value, name, None))
        for name in (
            "create",
            "stage_request",
            "try_collect_response",
            "terminate",
            "delete",
            "absent",
            "discover",
        )
    )


def _implements_attester(value: object) -> bool:
    return all(
        callable(getattr(value, name, None))
        for name in ("bind", "confirm_exit", "confirm_empty", "confirm_removed")
    )


def _cpu_millicores(policy: BrokerPolicy) -> int:
    scaled = policy.limits.cpu_quota_micros * 1000
    period = policy.limits.cpu_period_micros
    if scaled % period:
        raise KubernetesRuntimeError(
            "Kubernetes CPU policy is not exactly representable"
        )
    result = scaled // period
    if result <= 0:
        raise KubernetesRuntimeError("Kubernetes CPU policy is invalid")
    return result
