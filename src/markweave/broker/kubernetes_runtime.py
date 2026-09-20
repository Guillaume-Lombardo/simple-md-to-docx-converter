"""Fail-closed Kubernetes backend for reverse-attempt isolation.

Kubernetes workload authority and node-level CRI/cgroup attestation are kept in
separate components. Application workers continue to use the T70 broker
protocol and receive neither authority."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from importlib import import_module
from typing import TYPE_CHECKING
from uuid import UUID

from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeIncarnation,
    RuntimeRecoveryBinding,
    policy_specification_evidence,
)

if TYPE_CHECKING:
    from markweave.broker.ports import RuntimeUnit
    from markweave.reversions.models import (
        ReverseAttemptRequest,
        ReverseAttemptResponse,
    )
import markweave.broker.kubernetes_contracts as _kubernetes_contracts
import markweave.broker.kubernetes_pod_contract as _kubernetes_pod_contract
import markweave.broker.kubernetes_recovery as _kubernetes_recovery

_DNS_LABEL = _kubernetes_contracts._DNS_LABEL
_SANDBOX_ID = _kubernetes_contracts._SANDBOX_ID
_CGROUP_PATH = _kubernetes_contracts._CGROUP_PATH
_FIXED_ENTRYPOINT = _kubernetes_contracts._FIXED_ENTRYPOINT
_MANAGED_LABEL = _kubernetes_contracts._MANAGED_LABEL
_UNIT_LABEL = _kubernetes_contracts._UNIT_LABEL
_ATTEMPT_LABEL = _kubernetes_contracts._ATTEMPT_LABEL
_PRINCIPAL_LABEL = _kubernetes_contracts._PRINCIPAL_LABEL
_POLICY_LABEL = _kubernetes_contracts._POLICY_LABEL
_SPECIFICATION_ANNOTATION = _kubernetes_contracts._SPECIFICATION_ANNOTATION
_POOL_LABEL = _kubernetes_contracts._POOL_LABEL
_FENCE_LABEL = _kubernetes_contracts._FENCE_LABEL
_TAINT_KEY = _kubernetes_contracts._TAINT_KEY
_MAX_DNS_NAME_BYTES = _kubernetes_contracts._MAX_DNS_NAME_BYTES
_MAX_IMAGE_REPOSITORY_BYTES = _kubernetes_contracts._MAX_IMAGE_REPOSITORY_BYTES
_RESOURCE_PATH_DEPTH = _kubernetes_contracts._RESOURCE_PATH_DEPTH
KubernetesRuntimeError = _kubernetes_contracts.KubernetesRuntimeError
KubernetesAttestationNotReady = _kubernetes_contracts.KubernetesAttestationNotReady
KubernetesPodIdentity = _kubernetes_contracts.KubernetesPodIdentity
KubernetesSandboxIdentity = _kubernetes_contracts.KubernetesSandboxIdentity
KubernetesRuntimeUnit = _kubernetes_contracts.KubernetesRuntimeUnit
KubernetesRuntimeConfig = _kubernetes_contracts.KubernetesRuntimeConfig
KubernetesAttestationContract = _kubernetes_contracts.KubernetesAttestationContract
KubernetesControlPlane = _kubernetes_contracts.KubernetesControlPlane
KubernetesNodeAttester = _kubernetes_contracts.KubernetesNodeAttester
manifest_digest = _kubernetes_pod_contract.manifest_digest
pod_contract_projection = _kubernetes_pod_contract.pod_contract_projection
project_observed_pod = _kubernetes_pod_contract.project_observed_pod
pod_contract_digest = _kubernetes_pod_contract.pod_contract_digest
_cpu_millicores = _kubernetes_pod_contract._cpu_millicores
_pod_contract_template = _kubernetes_pod_contract._pod_contract_template
_project_like = _kubernetes_pod_contract._project_like
_cpu_quantity = _kubernetes_pod_contract._cpu_quantity
_byte_quantity = _kubernetes_pod_contract._byte_quantity
_extra_workload_containers = _kubernetes_pod_contract._extra_workload_containers
_filter_default_tolerations = _kubernetes_pod_contract._filter_default_tolerations
_restore_omitted_api_defaults = _kubernetes_pod_contract._restore_omitted_api_defaults
_allowed_api_default = _kubernetes_pod_contract._allowed_api_default
_canonical_recovery = _kubernetes_recovery._canonical_recovery
_runtime_name = _kubernetes_recovery._runtime_name
_pod_recovery_mapping = _kubernetes_recovery._pod_recovery_mapping
_sandbox_recovery_mapping = _kubernetes_recovery._sandbox_recovery_mapping
_policy_recovery_mapping = _kubernetes_recovery._policy_recovery_mapping
_prepared_unit_mapping = _kubernetes_recovery._prepared_unit_mapping
_prepared_recovery_binding = _kubernetes_recovery._prepared_recovery_binding
_closed_recovery = _kubernetes_recovery._closed_recovery
_recovery_text = _kubernetes_recovery._recovery_text
_recovery_int = _kubernetes_recovery._recovery_int
_recovery_ints = _kubernetes_recovery._recovery_ints
_decode_recovery_policy = _kubernetes_recovery._decode_recovery_policy
_decode_prepared_recovery_binding = (
    _kubernetes_recovery._decode_prepared_recovery_binding
)
_decode_recovery_binding = _kubernetes_recovery._decode_recovery_binding
build_pod_manifest = _kubernetes_pod_contract.build_pod_manifest


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
        request_model = import_module(
            "markweave.reversions.models"
        ).ReverseAttemptRequest
        if type(request) is not request_model:
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

    _manifest_for = staticmethod(build_pod_manifest)

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
