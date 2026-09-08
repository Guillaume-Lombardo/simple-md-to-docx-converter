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
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from uuid import UUID

from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    RuntimeRecoveryBinding,
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


def pod_contract_projection(pod: Mapping[str, object]) -> dict[str, object]:
    """Return the canonical broker-owned subset before API-server defaulting."""

    if not isinstance(pod, Mapping):
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid")
    try:
        projected = _project_like(pod, _pod_contract_template(pod), ())
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid") from error
    if type(projected) is not dict or any(type(key) is not str for key in projected):
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid")
    return cast(dict[str, object], projected)


def project_observed_pod(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    """Project an API-defaulted Pod through an exact expected contract shape."""

    if not isinstance(observed, Mapping) or not isinstance(expected, Mapping):
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid")
    try:
        if _extra_workload_containers(observed):
            raise ValueError
        prepared = _filter_default_tolerations(observed, expected)
        projected = _project_like(prepared, expected, ())
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid") from error
    if type(projected) is not dict or any(type(key) is not str for key in projected):
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid")
    return cast(dict[str, object], projected)


def pod_contract_digest(pod: Mapping[str, object]) -> EvidenceDigest:
    """Digest one canonical Pod security contract."""

    return manifest_digest(pod_contract_projection(pod))


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
        self._recovered: set[UUID] = set()

    def prepare(
        self, unit: ManagedUnit, policy: BrokerPolicy
    ) -> RuntimeRecoveryBinding | None:
        """Bind creation policy durably before the first Kubernetes API mutation."""

        if (
            type(unit) is not ManagedUnit
            or unit.state is not ManagedUnitState.RESERVED
            or type(policy) is not BrokerPolicy
            or policy != self._policy
            or unit.policy_revision != policy.revision
            or unit.policy_specification != policy_specification_evidence(policy)
        ):
            raise KubernetesRuntimeError("Kubernetes create contract is invalid")
        intent = ManagedUnit(
            unit.attempt_id,
            unit.unit_id,
            unit.principal,
            unit.create_sequence,
            unit.policy_revision,
            unit.policy_specification,
            ManagedUnitState.CREATE_INTENT,
            unit.revision + 1,
        )
        manifest = self._manifest(intent, policy)
        return _prepared_recovery_binding(
            intent,
            policy,
            self._config,
            self._image_repository,
            manifest_digest(pod_contract_projection(manifest)),
        )

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> KubernetesRuntimeUnit:
        """Create and positively bind one exact immutable-policy Pod sandbox."""

        if (
            type(unit) is not ManagedUnit
            or unit.state is not ManagedUnitState.CREATE_INTENT
            or type(policy) is not BrokerPolicy
            or policy != self._policy
        ):
            raise KubernetesRuntimeError("Kubernetes create contract is invalid")
        creation_policy = policy
        creation_config = self._config
        creation_repository = self._image_repository
        if unit.runtime_recovery is not None:
            creation_policy, creation_config, creation_repository, expected_digest = (
                _decode_prepared_recovery_binding(unit, unit.runtime_recovery)
            )
        elif (
            unit.policy_revision != policy.revision
            or unit.policy_specification != policy_specification_evidence(policy)
        ):
            raise KubernetesRuntimeError("Kubernetes create contract is invalid")
        manifest = self._manifest_for(
            unit, creation_policy, creation_config, creation_repository
        )
        pod_contract = pod_contract_projection(manifest)
        if (
            unit.runtime_recovery is not None
            and manifest_digest(pod_contract) != expected_digest
        ):
            raise KubernetesRuntimeError("Kubernetes create contract is invalid")
        try:
            pod = self._control.create(manifest)
            self._verify_pod_identity(unit, pod)
            sandbox = self._attester.bind(
                pod,
                KubernetesAttestationContract(
                    pod_contract,
                    manifest_digest(pod_contract),
                    creation_policy,
                    creation_config.pool_name,
                    creation_config.node_fence_revision,
                ),
            )
            provisional = KubernetesRuntimeUnit(
                unit.unit_id,
                RuntimeIncarnation(pod.pod_uid, unit.policy_specification),
                pod,
                sandbox,
            )
            result = KubernetesRuntimeUnit(
                provisional.unit_id,
                provisional.incarnation,
                provisional.pod,
                provisional.sandbox,
                self._recovery_binding(
                    provisional,
                    creation_policy,
                    creation_config,
                    image_repository=creation_repository,
                ),
            )
            previous = self._known.setdefault(unit.unit_id, result)
            if previous != result:
                raise KubernetesRuntimeError("Kubernetes incarnation conflicts")
            return result
        except KubernetesRuntimeError:
            raise
        except Exception:
            failure = KubernetesRuntimeError("Kubernetes create failed")
        raise failure

    def recover_create_intent(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> KubernetesRuntimeUnit | None:
        """Recover or adopt an exact Pod after a lost create/bind reply."""

        if (
            type(unit) is not ManagedUnit
            or unit.state is not ManagedUnitState.CREATE_INTENT
            or unit.runtime_recovery != binding
            or unit.runtime_incarnation is not None
        ):
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        policy, config, repository, expected_digest = _decode_prepared_recovery_binding(
            unit, binding
        )
        manifest = self._manifest_for(unit, policy, config, repository)
        pod_contract = pod_contract_projection(manifest)
        if manifest_digest(pod_contract) != expected_digest:
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        pod = _external_call(
            lambda: self._control.find(_runtime_name(unit.unit_id)),
            "Kubernetes recovery discovery failed",
            preserve_runtime_error=True,
        )
        if pod is None:
            return None
        self._verify_pod_identity(unit, pod)
        contract = KubernetesAttestationContract(
            pod_contract,
            expected_digest,
            policy,
            config.pool_name,
            config.node_fence_revision,
        )
        sandbox = _external_call(
            lambda: self._attester.adopt_create_intent(pod, contract),
            "Kubernetes recovery attestation failed",
            preserve_runtime_error=True,
        )
        provisional = KubernetesRuntimeUnit(
            unit.unit_id,
            RuntimeIncarnation(pod.pod_uid, unit.policy_specification),
            pod,
            sandbox,
        )
        result = KubernetesRuntimeUnit(
            provisional.unit_id,
            provisional.incarnation,
            provisional.pod,
            provisional.sandbox,
            self._recovery_binding(
                provisional,
                policy,
                config,
                expected_contract=contract,
                image_repository=repository,
            ),
        )
        previous = self._known.setdefault(unit.unit_id, result)
        if previous != result:
            raise KubernetesRuntimeError("Kubernetes recovery identity conflicts")
        self._recovered.add(unit.unit_id)
        return result

    def recover(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> KubernetesRuntimeUnit:
        """Recover an exact creation-time binding, including an exited Pod."""

        pod, sandbox, policy, config, repository, contract_digest = (
            _decode_recovery_binding(binding)
        )
        if (
            type(unit) is not ManagedUnit
            or unit.runtime_incarnation is None
            or unit.runtime_recovery != binding
            or unit.state
            not in {
                ManagedUnitState.CREATED,
                ManagedUnitState.EXIT_CONFIRMED,
                ManagedUnitState.EMPTY_CONFIRMED,
            }
            or pod.unit_id != unit.unit_id
            or pod.attempt_id != unit.attempt_id
            or pod.principal_id != unit.principal.principal_id
            or pod.policy_revision != unit.policy_revision
            or pod.policy_specification != unit.policy_specification
            or unit.runtime_incarnation
            != RuntimeIncarnation(pod.pod_uid, pod.policy_specification)
            or policy_specification_evidence(policy) != unit.policy_specification
        ):
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        manifest = self._manifest_for(unit, policy, config, repository)
        pod_contract = pod_contract_projection(manifest)
        contract = KubernetesAttestationContract(
            pod_contract,
            manifest_digest(pod_contract),
            policy,
            config.pool_name,
            config.node_fence_revision,
        )
        if contract.manifest_digest != contract_digest:
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        absent = _external_call(
            lambda: self._control.absent(pod),
            "Kubernetes recovery discovery failed",
            preserve_runtime_error=True,
        )
        if absent is not False and unit.state is not ManagedUnitState.EMPTY_CONFIRMED:
            raise KubernetesRuntimeError("Kubernetes recovery identity is unavailable")
        provisional = KubernetesRuntimeUnit(
            unit.unit_id, unit.runtime_incarnation, pod, sandbox, binding
        )
        recovered = _external_call(
            lambda: self._attester.recover(provisional, contract, unit.state),
            "Kubernetes recovery attestation failed",
            preserve_runtime_error=True,
        )
        if recovered != sandbox:
            raise KubernetesRuntimeError("Kubernetes recovery identity changed")
        previous = self._known.setdefault(unit.unit_id, provisional)
        if previous != provisional:
            raise KubernetesRuntimeError("Kubernetes recovery identity conflicts")
        self._recovered.add(unit.unit_id)
        return provisional

    def acknowledge_recovery(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> None:
        """Release attester state only after the broker persisted proof ACK."""

        pod, sandbox, _, _, _, _ = _decode_recovery_binding(binding)
        if (
            type(unit) is not ManagedUnit
            or unit.state is not ManagedUnitState.REMOVED
            or not unit.proof_acknowledged
            or unit.runtime_recovery != binding
            or unit.runtime_incarnation is None
            or unit.runtime_incarnation
            != RuntimeIncarnation(pod.pod_uid, pod.policy_specification)
            or pod.unit_id != unit.unit_id
            or pod.attempt_id != unit.attempt_id
            or pod.principal_id != unit.principal.principal_id
            or pod.policy_revision != unit.policy_revision
            or pod.policy_specification != unit.policy_specification
        ):
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        expected = KubernetesRuntimeUnit(
            unit.unit_id, unit.runtime_incarnation, pod, sandbox, binding
        )
        runtime_unit = self._known.get(unit.unit_id)
        if runtime_unit is None:
            runtime_unit = expected
        elif runtime_unit != expected:
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        removal_evidence = unit.removal_evidence
        if removal_evidence is None:
            raise KubernetesRuntimeError("Kubernetes recovery acknowledgement failed")
        _external_call(
            lambda: self._attester.acknowledge(runtime_unit, removal_evidence),
            "Kubernetes recovery acknowledgement failed",
            preserve_runtime_error=True,
        )
        self._known.pop(unit.unit_id, None)
        self._recovered.discard(unit.unit_id)

    def stage_request(
        self, runtime_unit: RuntimeUnit, request: ReverseAttemptRequest
    ) -> None:
        verified = self._coerce(runtime_unit)
        if type(request) is not ReverseAttemptRequest:
            raise KubernetesRuntimeError("Kubernetes workspace request is invalid")
        managed = ManagedUnit(
            verified.pod.attempt_id,
            verified.unit_id,
            AuthenticatedPrincipal(verified.pod.principal_id),
            1,
            verified.pod.policy_revision,
            verified.pod.policy_specification,
            ManagedUnitState.CREATE_INTENT,
            1,
        )
        manifest = self._manifest(managed, self._policy)
        rebound = _external_call(
            lambda: self._attester.bind(
                verified.pod,
                self._attestation(pod_contract_projection(manifest), self._policy),
            ),
            "Kubernetes pre-staging attestation failed",
        )
        if rebound != verified.sandbox:
            raise KubernetesRuntimeError("Kubernetes pre-staging attestation failed")
        _external_call(
            lambda: self._control.stage_request(verified.pod, request),
            "Kubernetes workspace staging failed",
        )

    def try_collect_response(
        self, runtime_unit: RuntimeUnit, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        verified = self._coerce(runtime_unit)
        if type(expected_attempt_id) is not UUID:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        response = _external_call(
            lambda: self._control.try_collect_response(
                verified.pod, expected_attempt_id
            ),
            "Kubernetes workspace collection failed",
        )
        if response is not None and response.attempt_id != expected_attempt_id:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        return response

    def hard_terminate(self, runtime_unit: RuntimeUnit) -> None:
        verified = self._coerce(runtime_unit)
        try:
            _external_call(
                lambda: self._control.terminate(verified.pod),
                "Kubernetes termination failed",
            )
        except KubernetesRuntimeError as termination_error:
            try:
                evidence = self._attester.confirm_exit(verified)
                _require_evidence(evidence, "Kubernetes exit is unconfirmed")
            except Exception:
                raise termination_error from None

    def confirm_exit(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        evidence = _external_call(
            lambda: self._attester.confirm_exit(verified),
            "Kubernetes exit is unconfirmed",
        )
        return _require_evidence(evidence, "Kubernetes exit is unconfirmed")

    def confirm_empty(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        exit_evidence = self.confirm_exit(verified)
        evidence = _external_call(
            lambda: self._attester.confirm_empty(verified, exit_evidence),
            "Kubernetes stable unit is not empty",
        )
        return _require_evidence(evidence, "Kubernetes stable unit is not empty")

    def remove(self, runtime_unit: RuntimeUnit) -> None:
        verified = self._coerce(runtime_unit)
        _external_call(
            lambda: self._control.delete(verified.pod),
            "Kubernetes removal failed",
        )

    def confirm_removed(
        self, runtime_unit: RuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        verified = self._coerce(runtime_unit)
        if type(empty_evidence) is not EvidenceDigest:
            raise KubernetesRuntimeError("Kubernetes empty evidence is invalid")
        cri_evidence = _external_call(
            lambda: self._attester.confirm_removed(verified, empty_evidence),
            "Kubernetes removal is unconfirmed",
        )
        absent = _external_call(
            lambda: self._control.absent(verified.pod),
            "Kubernetes removal is unconfirmed",
        )
        if absent is not True:
            raise KubernetesRuntimeError("Kubernetes removal is unconfirmed")
        self._known.pop(verified.unit_id, None)
        self._recovered.discard(verified.unit_id)
        return _require_evidence(cri_evidence, "Kubernetes removal is unconfirmed")

    def discover(self, *, limit: int) -> tuple[KubernetesRuntimeUnit, ...]:
        if type(limit) is not int or limit <= 0:
            raise KubernetesRuntimeError("Kubernetes discovery limit is invalid")
        pods = _external_call(
            lambda: self._control.discover(
                namespace=self._config.namespace,
                labels={_MANAGED_LABEL: "1"},
                limit=limit,
            ),
            "Kubernetes discovery failed",
        )
        if type(pods) is not tuple or len(pods) > limit:
            raise KubernetesRuntimeError("Kubernetes discovery exceeds its limit")
        discovered: list[KubernetesRuntimeUnit] = []
        seen: set[UUID] = set()
        for pod in pods:
            if type(pod) is not KubernetesPodIdentity:
                raise KubernetesRuntimeError("Kubernetes discovery identity is invalid")
            known = self._known.get(pod.unit_id)
            if known is not None:
                if known.pod != pod or known.unit_id in seen:
                    raise KubernetesRuntimeError(
                        "Kubernetes discovery identity is invalid"
                    )
                if known.unit_id not in self._recovered:
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
                    contract = self._attestation(
                        pod_contract_projection(self._manifest(managed, self._policy)),
                        self._policy,
                    )
                    if self._attester.bind(pod, contract) != known.sandbox:
                        raise KubernetesRuntimeError(
                            "Kubernetes discovery identity is invalid"
                        )
                seen.add(known.unit_id)
                discovered.append(known)
                continue
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
            manifest = self._manifest(managed, self._policy)
            pod_contract = pod_contract_projection(manifest)
            attestation = self._attestation(pod_contract, self._policy)
            sandbox, creation_attestation = _external_call(
                lambda pod=pod, contract=attestation: (
                    self._attester.recover_create_intent(pod, contract)
                ),
                "Kubernetes discovery failed",
                preserve_runtime_error=True,
            )
            if (
                type(creation_attestation) is not KubernetesAttestationContract
                or policy_specification_evidence(creation_attestation.policy)
                != pod.policy_specification
                or creation_attestation.manifest_digest
                != manifest_digest(creation_attestation.pod_contract)
            ):
                raise KubernetesRuntimeError("Kubernetes discovery policy is invalid")
            runtime_unit = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
            runtime_unit = KubernetesRuntimeUnit(
                runtime_unit.unit_id,
                runtime_unit.incarnation,
                runtime_unit.pod,
                runtime_unit.sandbox,
                self._recovery_binding(
                    runtime_unit,
                    creation_attestation.policy,
                    self._config,
                    expected_contract=creation_attestation,
                ),
            )
            previous = self._known.setdefault(pod.unit_id, runtime_unit)
            if previous != runtime_unit or runtime_unit.unit_id in seen:
                raise KubernetesRuntimeError("Kubernetes discovery identity is invalid")
            seen.add(runtime_unit.unit_id)
            discovered.append(runtime_unit)
        return tuple(sorted(discovered, key=lambda item: str(item.unit_id)))

    def _coerce(self, value: RuntimeUnit) -> KubernetesRuntimeUnit:
        if type(value) is KubernetesRuntimeUnit:
            candidate = value
        else:
            try:
                candidate = self._known[value.unit_id]
                if (
                    value.attempt_id != candidate.attempt_id
                    or value.principal_id != candidate.principal_id
                    or value.incarnation != candidate.incarnation
                ):
                    raise ValueError
            except AttributeError, KeyError, ValueError:
                raise KubernetesRuntimeError(
                    "Kubernetes runtime unit is invalid"
                ) from None
        if self._known.get(candidate.unit_id) != candidate:
            raise KubernetesRuntimeError("Kubernetes runtime unit is unknown")
        return candidate

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
        return self._manifest_for(unit, policy, self._config, self._image_repository)

    @staticmethod
    def _manifest_for(
        unit: ManagedUnit,
        policy: BrokerPolicy,
        config: KubernetesRuntimeConfig,
        image_repository: str,
    ) -> dict[str, object]:
        if (
            policy.limits.memory_bytes - policy.limits.workspace_bytes
            < config.interpreter_memory_margin_bytes
        ):
            raise KubernetesRuntimeError("Kubernetes memory policy is invalid")
        cpu_millicores = _cpu_millicores(policy)
        labels = {
            _ATTEMPT_LABEL: str(unit.attempt_id),
            _MANAGED_LABEL: "1",
            _POLICY_LABEL: policy.revision,
            _PRINCIPAL_LABEL: str(unit.principal.principal_id),
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
                "annotations": {
                    _SPECIFICATION_ANNOTATION: unit.policy_specification.value,
                },
            },
            "spec": {
                "activeDeadlineSeconds": seconds,
                "automountServiceAccountToken": False,
                "containers": [
                    {
                        "name": "attempt",
                        "image": f"{image_repository}@{policy.image_digest}",
                        "imagePullPolicy": "IfNotPresent",
                        "command": list(_FIXED_ENTRYPOINT),
                        "args": [],
                        "env": [
                            {
                                "name": "MARKWEAVE_POD_UID",
                                "valueFrom": {
                                    "fieldRef": {"fieldPath": "metadata.uid"}
                                },
                            },
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
                "securityContext": {
                    "fsGroup": config.run_as_gid,
                    "fsGroupChangePolicy": "OnRootMismatch",
                    "runAsGroup": config.run_as_gid,
                    "runAsNonRoot": True,
                    "runAsUser": config.run_as_uid,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
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

    def _recovery_binding(
        self,
        unit: KubernetesRuntimeUnit,
        policy: BrokerPolicy,
        config: KubernetesRuntimeConfig,
        *,
        expected_contract: KubernetesAttestationContract | None = None,
        image_repository: str | None = None,
    ) -> RuntimeRecoveryBinding:
        repository = (
            self._image_repository if image_repository is None else image_repository
        )
        contract = pod_contract_projection(
            self._manifest_for(
                ManagedUnit(
                    unit.attempt_id,
                    unit.unit_id,
                    AuthenticatedPrincipal(unit.principal_id),
                    1,
                    policy.revision,
                    unit.pod.policy_specification,
                    ManagedUnitState.CREATE_INTENT,
                    1,
                ),
                policy,
                config,
                repository,
            )
        )
        if expected_contract is not None and (
            expected_contract.policy != policy
            or expected_contract.pool != config.pool_name
            or expected_contract.fence_revision != config.node_fence_revision
            or expected_contract.pod_contract != contract
            or expected_contract.manifest_digest != manifest_digest(contract)
        ):
            raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
        payload = {
            "phase": "bound",
            "config": asdict(config),
            "contract_digest": manifest_digest(contract).value,
            "image_repository": repository,
            "pod": _pod_recovery_mapping(unit.pod),
            "policy": _policy_recovery_mapping(policy),
            "sandbox": _sandbox_recovery_mapping(unit.sandbox),
        }
        return RuntimeRecoveryBinding("kubernetes", 1, _canonical_recovery(payload))

    def _attestation(
        self, pod_contract: Mapping[str, object], policy: BrokerPolicy
    ) -> KubernetesAttestationContract:
        return KubernetesAttestationContract(
            pod_contract,
            manifest_digest(pod_contract),
            policy,
            self._config.pool_name,
            self._config.node_fence_revision,
        )


def _require_evidence(value: object, message: str) -> EvidenceDigest:
    if type(value) is not EvidenceDigest:
        raise KubernetesRuntimeError(message)
    return value


def _external_call[T](
    operation: Callable[[], T],
    message: str,
    *,
    preserve_runtime_error: bool = False,
) -> T:
    try:
        return operation()
    except KubernetesRuntimeError:
        if preserve_runtime_error:
            raise
        failure = KubernetesRuntimeError(message)
    except Exception:
        failure = KubernetesRuntimeError(message)
    raise failure


def _implements_control_plane(value: object) -> bool:
    return all(
        callable(getattr(value, name, None))
        for name in (
            "create",
            "find",
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
        for name in (
            "acknowledge",
            "adopt_create_intent",
            "bind",
            "confirm_empty",
            "confirm_exit",
            "confirm_removed",
            "recover",
            "recover_create_intent",
        )
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


def _pod_contract_template(pod: Mapping[str, object]) -> dict[str, object]:
    return dict(pod)


def _project_like(value: object, template: object, path: tuple[str, ...]) -> object:
    if isinstance(template, Mapping):
        if not isinstance(value, Mapping):
            raise TypeError
        for key in value.keys() - template.keys():
            if type(key) is not str or not _allowed_api_default(
                path, key, value[key], value
            ):
                raise ValueError
        return {
            key: _project_like(value[key], child, (*path, key))
            for key, child in template.items()
        }

    if type(template) is list:
        if type(value) is not list or len(value) != len(template):
            raise TypeError
        return [
            _project_like(item, template[index], (*path, str(index)))
            for index, item in enumerate(value)
        ]
    if len(path) >= _RESOURCE_PATH_DEPTH and path[-2] in {"limits", "requests"}:
        if path[-1] == "cpu":
            return _cpu_quantity(value)
        if path[-1] in {"memory", "ephemeral-storage"}:
            return _byte_quantity(value)
    if path[-1:] == ("sizeLimit",):
        return _byte_quantity(value)
    if type(value) is not type(template):
        raise TypeError
    return value


def _cpu_quantity(value: object) -> int:
    if type(value) is not str or not value:
        raise ValueError
    amount = Decimal(value[:-1]) if value.endswith("m") else Decimal(value) * 1000
    if amount != amount.to_integral_value() or amount <= 0:
        raise ValueError
    return int(amount)


def _byte_quantity(value: object) -> int:
    if type(value) is not str or not value:
        raise ValueError
    suffixes = {
        "Ki": 1 << 10,
        "Mi": 1 << 20,
        "Gi": 1 << 30,
        "Ti": 1 << 40,
        "Pi": 1 << 50,
        "Ei": 1 << 60,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }
    suffix = next((item for item in suffixes if value.endswith(item)), "")
    number = value[: -len(suffix)] if suffix else value
    amount = Decimal(number) * suffixes.get(suffix, 1)
    if amount != amount.to_integral_value() or amount <= 0:
        raise ValueError
    return int(amount)


def _extra_workload_containers(pod: Mapping[str, object]) -> bool:
    specification = pod.get("spec")
    if not isinstance(specification, Mapping):
        raise TypeError
    values = (
        specification.get("initContainers"),
        specification.get("ephemeralContainers"),
    )
    return any(value is not None and value not in ((), []) for value in values)


def _filter_default_tolerations(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    observed_copy = dict(observed)
    observed_specification = observed.get("spec")
    expected_specification = expected.get("spec")
    if not isinstance(observed_specification, Mapping) or not isinstance(
        expected_specification, Mapping
    ):
        raise TypeError
    expected_tolerations = expected_specification.get("tolerations")
    observed_tolerations = observed_specification.get("tolerations")
    if type(expected_tolerations) is not list or type(observed_tolerations) is not list:
        raise TypeError
    expected_keys = {
        item.get("key") for item in expected_tolerations if isinstance(item, Mapping)
    }
    defaults = (
        {
            "effect": "NoExecute",
            "key": "node.kubernetes.io/not-ready",
            "operator": "Exists",
            "tolerationSeconds": 300,
        },
        {
            "effect": "NoExecute",
            "key": "node.kubernetes.io/unreachable",
            "operator": "Exists",
            "tolerationSeconds": 300,
        },
    )
    for item in observed_tolerations:
        if not isinstance(item, Mapping):
            raise TypeError
        if item.get("key") not in expected_keys and dict(item) not in defaults:
            raise ValueError
    specification_copy = dict(observed_specification)
    specification_copy["tolerations"] = [
        item
        for item in observed_tolerations
        if isinstance(item, Mapping) and item.get("key") in expected_keys
    ]
    observed_copy["spec"] = specification_copy
    return observed_copy


def _allowed_api_default(
    path: tuple[str, ...], key: str, value: object, parent: Mapping[object, object]
) -> bool:
    allowed = False
    if path == ():
        allowed = key == "status" and isinstance(value, Mapping)
    elif path == ("metadata",):
        if key in {
            "creationTimestamp",
            "deletionTimestamp",
            "resourceVersion",
            "uid",
        }:
            allowed = type(value) is str and bool(value)
        elif key == "deletionGracePeriodSeconds":
            deletion_timestamp = parent.get("deletionTimestamp")
            allowed = (
                type(value) is int
                and value >= 0
                and type(deletion_timestamp) is str
                and bool(deletion_timestamp)
            )
        elif key == "generation":
            allowed = type(value) is int and value >= 1
        else:
            allowed = key == "managedFields" and type(value) is list
    elif path == ("spec",):
        if key == "nodeName":
            allowed = type(value) is str and bool(value)
        elif key == "serviceAccount":
            allowed = value == parent.get("serviceAccountName")
        else:
            allowed = (key, value) in {
                ("preemptionPolicy", "PreemptLowerPriority"),
                ("priority", 0),
                ("schedulerName", "default-scheduler"),
            }
    elif path == ("spec", "containers", "0"):
        allowed = (key, value) in {
            ("terminationMessagePath", "/dev/termination-log"),
            ("terminationMessagePolicy", "File"),
        }
    return allowed


def _canonical_recovery(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None


def _runtime_name(unit_id: UUID) -> str:
    return f"markweave-reverse-{unit_id.hex}"


def _pod_recovery_mapping(pod: KubernetesPodIdentity) -> dict[str, object]:
    return {
        "attempt_id": str(pod.attempt_id),
        "name": pod.name,
        "namespace": pod.namespace,
        "node_name": pod.node_name,
        "pod_uid": str(pod.pod_uid),
        "policy_revision": pod.policy_revision,
        "policy_specification": pod.policy_specification.value,
        "principal_id": str(pod.principal_id),
        "unit_id": str(pod.unit_id),
    }


def _sandbox_recovery_mapping(
    sandbox: KubernetesSandboxIdentity,
) -> dict[str, object]:
    return {
        "cgroup_path": sandbox.cgroup_path,
        "node_fence": sandbox.node_fence.value,
        "node_uid": str(sandbox.node_uid),
        "sandbox_id": sandbox.sandbox_id,
        "container_ids": list(sandbox.container_ids),
    }


def _policy_recovery_mapping(policy: BrokerPolicy) -> dict[str, object]:
    return asdict(policy)


def _prepared_unit_mapping(unit: ManagedUnit) -> dict[str, object]:
    return {
        "attempt_id": str(unit.attempt_id),
        "create_sequence": unit.create_sequence,
        "policy_revision": unit.policy_revision,
        "policy_specification": unit.policy_specification.value,
        "principal_id": str(unit.principal.principal_id),
        "runtime_name": _runtime_name(unit.unit_id),
        "unit_id": str(unit.unit_id),
    }


def _prepared_recovery_binding(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    config: KubernetesRuntimeConfig,
    repository: str,
    contract_digest: EvidenceDigest,
) -> RuntimeRecoveryBinding:
    return RuntimeRecoveryBinding(
        "kubernetes",
        1,
        _canonical_recovery(
            {
                "config": asdict(config),
                "contract_digest": contract_digest.value,
                "image_repository": repository,
                "phase": "prepared",
                "policy": _policy_recovery_mapping(policy),
                "unit": _prepared_unit_mapping(unit),
            }
        ),
    )


def _closed_recovery(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError
    return cast(Mapping[str, object], value)


def _recovery_text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if type(result) is not str or not result:
        raise ValueError
    return result


def _recovery_int(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int:
        raise ValueError
    return result


def _recovery_ints(value: Mapping[str, object]) -> dict[str, int]:
    if any(type(item) is not int for item in value.values()):
        raise ValueError
    return cast(dict[str, int], dict(value))


def _decode_recovery_policy(
    root: Mapping[str, object],
) -> tuple[BrokerPolicy, KubernetesRuntimeConfig, str, EvidenceDigest]:
    policy_value = _closed_recovery(
        root["policy"], {"channel_limits", "image_digest", "limits", "revision"}
    )
    limits = _closed_recovery(
        policy_value["limits"], set(RuntimeLimits.__dataclass_fields__)
    )
    channel = _closed_recovery(
        policy_value["channel_limits"],
        set(RuntimeChannelLimits.__dataclass_fields__),
    )
    config_value = _closed_recovery(
        root["config"], set(KubernetesRuntimeConfig.__dataclass_fields__)
    )
    policy = BrokerPolicy(
        _recovery_text(policy_value, "revision"),
        _recovery_text(policy_value, "image_digest"),
        RuntimeLimits(**_recovery_ints(limits)),
        RuntimeChannelLimits(**_recovery_ints(channel)),
    )
    config = KubernetesRuntimeConfig(
        namespace=_recovery_text(config_value, "namespace"),
        service_account=_recovery_text(config_value, "service_account"),
        runtime_class=_recovery_text(config_value, "runtime_class"),
        pool_name=_recovery_text(config_value, "pool_name"),
        node_fence_revision=_recovery_text(config_value, "node_fence_revision"),
        run_as_uid=_recovery_int(config_value, "run_as_uid"),
        run_as_gid=_recovery_int(config_value, "run_as_gid"),
        interpreter_memory_margin_bytes=_recovery_int(
            config_value, "interpreter_memory_margin_bytes"
        ),
    )
    repository = _recovery_text(root, "image_repository")
    if (
        len(repository) > _MAX_IMAGE_REPOSITORY_BYTES
        or "@" in repository
        or any(part in {"", ".", ".."} for part in repository.split("/"))
    ):
        raise ValueError
    return (
        policy,
        config,
        repository,
        EvidenceDigest(_recovery_text(root, "contract_digest")),
    )


def _decode_prepared_recovery_binding(
    unit: ManagedUnit, binding: RuntimeRecoveryBinding
) -> tuple[BrokerPolicy, KubernetesRuntimeConfig, str, EvidenceDigest]:
    if binding.backend != "kubernetes" or binding.schema_version != 1:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
    try:
        root = _closed_recovery(
            json.loads(binding.payload.decode("ascii")),
            {
                "config",
                "contract_digest",
                "image_repository",
                "phase",
                "policy",
                "unit",
            },
        )
        unit_value = _closed_recovery(
            root["unit"],
            {
                "attempt_id",
                "create_sequence",
                "policy_revision",
                "policy_specification",
                "principal_id",
                "runtime_name",
                "unit_id",
            },
        )
        policy, config, repository, digest = _decode_recovery_policy(root)
        if (
            _canonical_recovery(root) != binding.payload
            or root["phase"] != "prepared"
            or UUID(_recovery_text(unit_value, "unit_id")) != unit.unit_id
            or UUID(_recovery_text(unit_value, "attempt_id")) != unit.attempt_id
            or _recovery_int(unit_value, "create_sequence") != unit.create_sequence
            or UUID(_recovery_text(unit_value, "principal_id"))
            != unit.principal.principal_id
            or _recovery_text(unit_value, "runtime_name") != _runtime_name(unit.unit_id)
            or _recovery_text(unit_value, "policy_revision") != unit.policy_revision
            or EvidenceDigest(_recovery_text(unit_value, "policy_specification"))
            != unit.policy_specification
            or policy.revision != unit.policy_revision
            or policy_specification_evidence(policy) != unit.policy_specification
        ):
            raise ValueError
    except KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None
    return policy, config, repository, digest


def _decode_recovery_binding(
    binding: RuntimeRecoveryBinding,
) -> tuple[
    KubernetesPodIdentity,
    KubernetesSandboxIdentity,
    BrokerPolicy,
    KubernetesRuntimeConfig,
    str,
    EvidenceDigest,
]:
    if (
        type(binding) is not RuntimeRecoveryBinding
        or binding.backend != "kubernetes"
        or binding.schema_version != 1
    ):
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
    try:
        value = json.loads(binding.payload.decode("ascii"))
        root = _closed_recovery(
            value,
            {
                "config",
                "contract_digest",
                "image_repository",
                "phase",
                "pod",
                "policy",
                "sandbox",
            },
        )
        if _canonical_recovery(root) != binding.payload:
            raise ValueError
        if root["phase"] != "bound":
            raise ValueError
        pod_value = _closed_recovery(
            root["pod"],
            {
                "attempt_id",
                "name",
                "namespace",
                "node_name",
                "pod_uid",
                "policy_revision",
                "policy_specification",
                "principal_id",
                "unit_id",
            },
        )
        sandbox_value = _closed_recovery(
            root["sandbox"],
            {
                "cgroup_path",
                "container_ids",
                "node_fence",
                "node_uid",
                "sandbox_id",
            },
        )
        pod = KubernetesPodIdentity(
            _recovery_text(pod_value, "namespace"),
            _recovery_text(pod_value, "name"),
            UUID(_recovery_text(pod_value, "pod_uid")),
            _recovery_text(pod_value, "node_name"),
            UUID(_recovery_text(pod_value, "unit_id")),
            UUID(_recovery_text(pod_value, "attempt_id")),
            UUID(_recovery_text(pod_value, "principal_id")),
            _recovery_text(pod_value, "policy_revision"),
            EvidenceDigest(_recovery_text(pod_value, "policy_specification")),
        )
        sandbox = KubernetesSandboxIdentity(
            _recovery_text(sandbox_value, "sandbox_id"),
            _recovery_text(sandbox_value, "cgroup_path"),
            UUID(_recovery_text(sandbox_value, "node_uid")),
            EvidenceDigest(_recovery_text(sandbox_value, "node_fence")),
            tuple(cast(list[str], sandbox_value["container_ids"])),
        )
        policy, config, repository, contract_digest = _decode_recovery_policy(root)
    except KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None
    return pod, sandbox, policy, config, repository, contract_digest
