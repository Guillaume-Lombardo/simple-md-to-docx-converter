"""Node attestation lifecycle and durable ledger reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import cast
from uuid import UUID

from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_attester_inventory import (
    AttesterLifecycleRecord,
    AttesterLifecycleState,
    SQLiteNodeAttesterLedger,
)
from markweave.broker.kubernetes_attester_protocol import (
    _PROTOCOL,
    _VERSION,
    _contract,
    _contract_mapping,
    _decode,
    _encode,
    _pod,
    _required_text,
    _same_runtime_identity,
    _sandbox,
    _sandbox_mapping,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesAttestationNotReady,
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
)
from markweave.broker.models import (
    EvidenceDigest,
    ManagedUnitState,
    RuntimeIncarnation,
)

_MAX_NODE_NAME_BYTES = 253


class NodeAttesterService:
    """Authorize a closed operation set around one node-local policy engine."""

    def __init__(
        self,
        engine: NodeAttestationEngine,
        ledger: SQLiteNodeAttesterLedger,
        *,
        node_name: str,
    ) -> None:
        if (
            type(engine) is not NodeAttestationEngine
            or type(ledger) is not SQLiteNodeAttesterLedger
            or type(node_name) is not str
            or not node_name
            or len(node_name.encode("utf-8")) > _MAX_NODE_NAME_BYTES
        ):
            raise ValueError("Kubernetes node attester service is invalid")
        self._engine = engine
        self._ledger = ledger
        self._node_name = node_name
        self._bound: dict[UUID, KubernetesRuntimeUnit] = {}
        self._exit_evidence: dict[UUID, EvidenceDigest] = {}
        self._empty_evidence: dict[UUID, EvidenceDigest] = {}
        self._removed_evidence: dict[UUID, tuple[EvidenceDigest, EvidenceDigest]] = {}
        self._state_lock = RLock()
        self._restore_ledger()

    def handle(self, payload: bytes) -> bytes:
        """Validate one content-free request and return a canonical response."""

        with self._state_lock:
            return self._handle_locked(payload)

    def _handle_locked(self, payload: bytes) -> bytes:
        try:
            request = _decode(payload)
            if (
                request.get("protocol") != _PROTOCOL
                or request.get("version") != _VERSION
            ):
                raise ValueError
            operation = request.get("operation")
            if operation == "bind":
                response = self._bind(request)
            elif operation in {"adopt_create_intent", "recover_create_intent"}:
                response = self._recover_create_intent(request)
            elif operation == "recover":
                response = self._recover(request)
            elif operation == "acknowledge":
                response = self._acknowledge(request)
            elif operation in {"confirm_exit", "confirm_empty", "confirm_removed"}:
                response = self._proof(cast(str, operation), request)
            else:
                raise ValueError
            return _encode({"outcome": "ok", **response})
        except KubernetesAttestationNotReady:
            return _encode({"outcome": "not_ready"})
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes attester request is invalid"
            ) from None

    def _bind(self, request: Mapping[str, object]) -> dict[str, object]:
        if set(request) != {"contract", "operation", "pod", "protocol", "version"}:
            raise ValueError
        pod = _pod(request["pod"])
        if pod.node_name != self._node_name:
            raise KubernetesRuntimeError("Kubernetes attester node binding is invalid")
        contract = _contract(request["contract"])
        binding_payload = _encode(request)
        existing = self._ledger.get(pod.pod_uid)
        if existing is not None:
            if existing.binding_payload != binding_payload:
                raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
            stored_sandbox = _sandbox(_decode(existing.sandbox_payload))
            self._restore_record(existing, pod, contract, stored_sandbox)
            return {"sandbox": _sandbox_mapping(stored_sandbox)}
        sandbox = self._engine.bind(pod, contract)
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        previous = self._bound.setdefault(pod.pod_uid, unit)
        if not _same_runtime_identity(previous, unit):
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        record = AttesterLifecycleRecord(
            pod.pod_uid,
            AttesterLifecycleState.BOUND,
            binding_payload,
            _encode(_sandbox_mapping(sandbox)),
        )
        try:
            self._ledger.reserve(record)
        except Exception:
            self._reconcile_reservation_failure(record, pod, contract, sandbox, unit)
            raise
        return {"sandbox": _sandbox_mapping(sandbox)}

    def _recover_create_intent(
        self, request: Mapping[str, object]
    ) -> dict[str, object]:
        if set(request) != {
            "operation",
            "pod",
            "proposed_contract",
            "protocol",
            "version",
        }:
            raise ValueError
        pod = _pod(request["pod"])
        if pod.node_name != self._node_name:
            raise KubernetesRuntimeError("Kubernetes attester node binding is invalid")
        proposed_contract = _contract(request["proposed_contract"])
        record = self._ledger.get(pod.pod_uid)
        if record is None and request["operation"] == "adopt_create_intent":
            sandbox = self._engine.adopt_create_intent(pod, proposed_contract)
            bind_request = {
                "contract": request["proposed_contract"],
                "operation": "bind",
                "pod": request["pod"],
                "protocol": _PROTOCOL,
                "version": _VERSION,
            }
            unit = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
            record = AttesterLifecycleRecord(
                pod.pod_uid,
                AttesterLifecycleState.BOUND,
                _encode(bind_request),
                _encode(_sandbox_mapping(sandbox)),
            )
            try:
                self._ledger.reserve(record)
            except Exception:
                self._reconcile_reservation_failure(
                    record, pod, proposed_contract, sandbox, unit
                )
                raise
            self._bound[pod.pod_uid] = unit
            return {
                "contract": _contract_mapping(proposed_contract),
                "sandbox": _sandbox_mapping(sandbox),
            }
        if record is None or record.state is not AttesterLifecycleState.BOUND:
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        stored = _decode(record.binding_payload)
        if (
            set(stored) != {"contract", "operation", "pod", "protocol", "version"}
            or stored["operation"] != "bind"
            or _pod(stored["pod"]) != pod
        ):
            raise KubernetesRuntimeError("Kubernetes attester recovery conflicts")
        contract = _contract(stored["contract"])
        sandbox = _sandbox(_decode(record.sandbox_payload))
        self._restore_record(record, pod, contract, sandbox)
        return {
            "contract": _contract_mapping(contract),
            "sandbox": _sandbox_mapping(sandbox),
        }

    def _recover(self, request: Mapping[str, object]) -> dict[str, object]:
        if set(request) != {
            "contract",
            "lifecycle_state",
            "operation",
            "pod",
            "protocol",
            "sandbox",
            "version",
        }:
            raise ValueError
        pod = _pod(request["pod"])
        if pod.node_name != self._node_name:
            raise KubernetesRuntimeError("Kubernetes attester node binding is invalid")
        contract = _contract(request["contract"])
        sandbox = _sandbox(request["sandbox"])
        requested_state = ManagedUnitState(_required_text(request, "lifecycle_state"))
        record = self._ledger.get(pod.pod_uid)
        if record is None:
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        bind_request = {
            "contract": request["contract"],
            "operation": "bind",
            "pod": request["pod"],
            "protocol": _PROTOCOL,
            "version": _VERSION,
        }
        if record.binding_payload != _encode(
            bind_request
        ) or record.sandbox_payload != _encode(_sandbox_mapping(sandbox)):
            raise KubernetesRuntimeError("Kubernetes attester recovery conflicts")
        if record.state is AttesterLifecycleState.REMOVED:
            if requested_state is not ManagedUnitState.EMPTY_CONFIRMED:
                raise KubernetesRuntimeError("Kubernetes attester recovery conflicts")
            self._bound[pod.pod_uid] = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
            return {"sandbox": _sandbox_mapping(sandbox)}
        actual_state = {
            AttesterLifecycleState.BOUND: ManagedUnitState.CREATED,
            AttesterLifecycleState.EXIT: ManagedUnitState.EXIT_CONFIRMED,
            AttesterLifecycleState.EMPTY: ManagedUnitState.EMPTY_CONFIRMED,
        }[record.state]
        order = {
            ManagedUnitState.CREATED: 0,
            ManagedUnitState.EXIT_CONFIRMED: 1,
            ManagedUnitState.EMPTY_CONFIRMED: 2,
        }
        if requested_state not in order or order[actual_state] < order[requested_state]:
            raise KubernetesRuntimeError("Kubernetes attester recovery conflicts")
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        recovered = self._engine.recover(unit, contract, actual_state)
        self._bound[pod.pod_uid] = unit
        return {"sandbox": _sandbox_mapping(recovered)}

    def _acknowledge(self, request: Mapping[str, object]) -> dict[str, object]:
        if set(request) != {
            "operation",
            "pod_uid",
            "protocol",
            "removed_evidence",
            "version",
        }:
            raise ValueError
        pod_uid = UUID(_required_text(request, "pod_uid"))
        removed = EvidenceDigest(_required_text(request, "removed_evidence"))
        record = self._ledger.get(pod_uid)
        if record is not None:
            unit = self._bound.get(pod_uid)
            if (
                unit is None
                or record.state is not AttesterLifecycleState.REMOVED
                or record.removed_evidence != removed
                or not self._ledger.discard_removed(pod_uid, removed)
            ):
                raise KubernetesRuntimeError(
                    "Kubernetes attester acknowledgement conflicts"
                )
            self._engine.acknowledge(unit, removed)
        self._bound.pop(pod_uid, None)
        self._exit_evidence.pop(pod_uid, None)
        self._empty_evidence.pop(pod_uid, None)
        self._removed_evidence.pop(pod_uid, None)
        return {"acknowledged": True}

    def _restore_ledger(self) -> None:
        for record in self._ledger.records():
            request = _decode(record.binding_payload)
            pod = _pod(request["pod"])
            contract = _contract(request["contract"])
            sandbox = _sandbox(_decode(record.sandbox_payload))
            if pod.node_name != self._node_name:
                raise KubernetesRuntimeError("Kubernetes attester ledger failed")
            self._restore_record(record, pod, contract, sandbox)

    def _reconcile_reservation_failure(
        self,
        expected: AttesterLifecycleRecord,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
        sandbox: KubernetesSandboxIdentity,
        unit: KubernetesRuntimeUnit,
    ) -> None:
        persisted = self._ledger.get(pod.pod_uid)
        if persisted is None:
            if self._bound.get(pod.pod_uid) == unit:
                self._bound.pop(pod.pod_uid)
            self._engine.discard_uncommitted_binding(
                pod.pod_uid, contract, sandbox.node_uid
            )
            return
        if persisted != expected:
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        self._restore_record(persisted, pod, contract, sandbox)

    def _restore_record(
        self,
        record: AttesterLifecycleRecord,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
        sandbox: KubernetesSandboxIdentity,
    ) -> None:
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        previous = self._bound.get(pod.pod_uid)
        if previous is not None and previous != unit:
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        if record.state is not AttesterLifecycleState.REMOVED:
            actual_state = {
                AttesterLifecycleState.BOUND: ManagedUnitState.CREATED,
                AttesterLifecycleState.EXIT: ManagedUnitState.EXIT_CONFIRMED,
                AttesterLifecycleState.EMPTY: ManagedUnitState.EMPTY_CONFIRMED,
            }[record.state]
            try:
                recovered = self._engine.recover(unit, contract, actual_state)
            except KubernetesRuntimeError:
                raise KubernetesRuntimeError(
                    "Kubernetes attester binding conflicts"
                ) from None
            if recovered != sandbox:
                raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        self._bound[pod.pod_uid] = unit
        cached = (
            (self._exit_evidence, record.exit_evidence),
            (self._empty_evidence, record.empty_evidence),
        )
        for evidence_by_pod, evidence in cached:
            if evidence is not None:
                previous_evidence = evidence_by_pod.setdefault(pod.pod_uid, evidence)
                if previous_evidence != evidence:
                    raise KubernetesRuntimeError(
                        "Kubernetes attester binding conflicts"
                    )
        if record.removed_evidence is not None and record.empty_evidence is not None:
            removed = (record.empty_evidence, record.removed_evidence)
            previous_removed = self._removed_evidence.setdefault(pod.pod_uid, removed)
            if previous_removed != removed:
                raise KubernetesRuntimeError("Kubernetes attester binding conflicts")

    def _proof(  # noqa: PLR0912
        self, operation: str, request: Mapping[str, object]
    ) -> dict[str, object]:
        expected = {"operation", "pod_uid", "protocol", "version"}
        if operation != "confirm_exit":
            expected.add("prior_evidence")
        if set(request) != expected:
            raise ValueError
        pod_uid = UUID(_required_text(request, "pod_uid"))
        prior = (
            None
            if operation == "confirm_exit"
            else EvidenceDigest(_required_text(request, "prior_evidence"))
        )
        record = self._ledger.get(pod_uid)
        if record is None:
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        if operation == "confirm_exit" and record.exit_evidence is not None:
            return {"evidence": record.exit_evidence.value}
        if operation == "confirm_empty" and record.empty_evidence is not None:
            if record.exit_evidence != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester exit evidence is invalid"
                )
            return {"evidence": record.empty_evidence.value}
        removed = self._removed_evidence.get(pod_uid)
        if operation == "confirm_removed" and removed is not None:
            if removed[0] != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester empty evidence is invalid"
                )
            return {"evidence": removed[1].value}
        unit = self._bound.get(pod_uid)
        if unit is None:
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        if operation == "confirm_exit":
            evidence = self._engine.confirm_exit(unit)
        elif operation == "confirm_empty":
            if prior is None:
                raise ValueError
            if self._exit_evidence.get(pod_uid) != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester exit evidence is invalid"
                )
            evidence = self._engine.confirm_empty(unit, prior)
        else:
            if prior is None:
                raise ValueError
            if self._empty_evidence.get(pod_uid) != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester empty evidence is invalid"
                )
            evidence = self._engine.confirm_removed(unit, prior)
            record = self._ledger.transition(
                pod_uid,
                expected_revision=record.revision,
                target=AttesterLifecycleState.REMOVED,
                evidence=evidence,
            )
            self._removed_evidence[pod_uid] = (prior, evidence)
            self._exit_evidence.pop(pod_uid, None)
            self._empty_evidence.pop(pod_uid, None)
        if operation == "confirm_exit":
            record = self._ledger.transition(
                pod_uid,
                expected_revision=record.revision,
                target=AttesterLifecycleState.EXIT,
                evidence=evidence,
            )
            previous = self._exit_evidence.setdefault(pod_uid, evidence)
            if previous != evidence:
                raise KubernetesRuntimeError(
                    "Kubernetes attester exit evidence conflicts"
                )
        elif operation == "confirm_empty":
            self._ledger.transition(
                pod_uid,
                expected_revision=record.revision,
                target=AttesterLifecycleState.EMPTY,
                evidence=evidence,
            )
            previous = self._empty_evidence.setdefault(pod_uid, evidence)
            if previous != evidence:
                raise KubernetesRuntimeError(
                    "Kubernetes attester empty evidence conflicts"
                )
        return {"evidence": evidence.value}
