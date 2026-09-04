"""Runtime-neutral lifecycle and reconciliation service for isolated attempts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Never
from uuid import UUID, uuid4, uuid5

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    TerminationProof,
    policy_specification_evidence,
)
from markweave.broker.ports import BrokerInventory, IsolationRuntime, RuntimeUnit

_PROOF_NAMESPACE = UUID("54bd5544-7973-41ae-a7fc-c56664411769")


@dataclass(frozen=True, slots=True)
class _StoredRuntimeUnit:
    unit_id: UUID
    incarnation: RuntimeIncarnation


class IsolationBrokerService:
    """Serialize admission and prove every unit terminated before proof return."""

    def __init__(
        self,
        inventory: BrokerInventory,
        runtime: IsolationRuntime,
        policy: BrokerPolicy,
        *,
        max_discovered_units: int,
        unit_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(policy) is not BrokerPolicy:
            raise ValueError("Broker policy is invalid")
        if type(max_discovered_units) is not int or max_discovered_units <= 0:
            raise ValueError("Broker discovery limit must be a positive integer")
        if not callable(unit_id_factory):
            raise ValueError("Broker unit identity factory is invalid")
        self._inventory = inventory
        self._runtime = runtime
        self._policy = policy
        self._policy_specification = policy_specification_evidence(policy)
        self._max_discovered_units = max_discovered_units
        self._unit_id_factory = unit_id_factory
        self._gate = RLock()
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return readiness without triggering or bypassing reconciliation."""

        with self._gate:
            return self._ready

    def start(self) -> None:
        """Reconcile the complete bounded inventory before enabling admission."""

        with self._gate:
            self._ready = False
            try:
                self._reconcile()
            except BrokerError:
                raise
            except Exception as error:
                raise BrokerError(
                    BrokerErrorCategory.RECONCILIATION_INCOMPLETE
                ) from error
            self._ready = True

    def runtime_reconnected(self) -> None:
        """Clear readiness and repeat the complete reconciliation sweep."""

        self.start()

    def create(
        self,
        replay: ReplayPosition,
        attempt_id: UUID,
    ) -> ManagedUnit:
        """Persist identity and create only after durable CREATE_INTENT."""

        with self._gate:
            self._require_ready()
            self._validate_request(replay, attempt_id)
            try:
                unit_id = self._unit_id_factory()
                if type(unit_id) is not UUID:
                    raise BrokerError(BrokerErrorCategory.RUNTIME_FAILURE)
                unit = ManagedUnit(
                    attempt_id,
                    unit_id,
                    replay.principal,
                    replay.sequence,
                    self._policy.revision,
                    self._policy_specification,
                    ManagedUnitState.RESERVED,
                    0,
                )
                reserved = self._inventory_call(
                    lambda: self._inventory.reserve(unit, replay)
                )
            except BrokerError:
                raise
            except Exception as error:
                raise BrokerError(BrokerErrorCategory.RUNTIME_FAILURE) from error
            if reserved.state is not ManagedUnitState.RESERVED:
                return reserved
            try:
                intent = self._inventory_call(
                    lambda: self._inventory.transition(
                        reserved.unit_id,
                        expected_revision=reserved.revision,
                        target=ManagedUnitState.CREATE_INTENT,
                    )
                )
                runtime_unit = self._runtime.create(intent, self._policy)
                self._validate_runtime_unit(intent, runtime_unit)
                return self._inventory_call(
                    lambda: self._inventory.transition(
                        reserved.unit_id,
                        expected_revision=intent.revision,
                        target=ManagedUnitState.CREATED,
                        runtime_incarnation=runtime_unit.incarnation,
                    )
                )
            except BrokerError as error:
                if error.category is BrokerErrorCategory.INVENTORY_FAILURE:
                    raise
                self._fail(BrokerErrorCategory.RUNTIME_FAILURE, cause=error)
            except Exception as error:
                self._fail(BrokerErrorCategory.RUNTIME_FAILURE, cause=error)

    def status(
        self,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID,
    ) -> ManagedUnit:
        """Return durable content-free status with replay fencing."""

        with self._gate:
            self._require_ready()
            return self._request_unit(principal, attempt_id, unit_id)

    def terminate(
        self,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID,
    ) -> TerminationProof:
        """Hard-terminate and return only a durably retained complete proof."""

        with self._gate:
            self._require_ready()
            unit = self._request_unit(principal, attempt_id, unit_id)
            try:
                if unit.state is ManagedUnitState.REMOVED:
                    return self._retained_proof(unit)
                runtime_unit = self._runtime_for(unit)
                return self._terminate_and_prove(unit, runtime_unit)
            except BrokerError as error:
                if error.category is BrokerErrorCategory.INVENTORY_FAILURE:
                    raise
                self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN, cause=error)
            except Exception as error:
                self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN, cause=error)

    def proof(
        self,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID,
    ) -> TerminationProof | None:
        """Return only the exact durable proof, never partial evidence."""

        with self._gate:
            self._require_ready()
            unit = self._request_unit(principal, attempt_id, unit_id)
            if unit.state is not ManagedUnitState.REMOVED:
                return None
            return self._retained_proof(unit)

    def acknowledge(
        self,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID,
        proof_id: UUID,
    ) -> bool:
        """Acknowledge one exact proof while retaining the replay high water."""

        with self._gate:
            self._require_ready()
            self._validate_request(principal, attempt_id, unit_id, proof_id)
            return self._inventory_call(
                lambda: self._inventory.acknowledge(
                    principal.principal_id, attempt_id, unit_id, proof_id
                )
            )

    def _request_unit(
        self,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID,
    ) -> ManagedUnit:
        self._validate_request(principal, attempt_id, unit_id)
        unit = self._inventory_call(lambda: self._inventory.get(unit_id))
        if unit is None:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        self._require_owner(unit, principal, attempt_id, unit_id)
        return unit

    def _runtime_for(self, unit: ManagedUnit) -> RuntimeUnit:
        if unit.runtime_incarnation is None:
            self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN)
        discovered = self._discovered()
        runtime_unit = discovered.get(unit.unit_id)
        if runtime_unit is None:
            if unit.state is not ManagedUnitState.EMPTY_CONFIRMED:
                self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN)
            return _StoredRuntimeUnit(unit.unit_id, unit.runtime_incarnation)
        self._validate_runtime_unit(unit, runtime_unit, require_persisted=True)
        return runtime_unit

    def _discovered(self) -> dict[UUID, RuntimeUnit]:
        discovered: dict[UUID, RuntimeUnit] = {}
        runtime_units = self._runtime.discover(limit=self._max_discovered_units)
        if len(runtime_units) > self._max_discovered_units:
            self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
        for runtime_unit in runtime_units:
            if runtime_unit.unit_id in discovered:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            discovered[runtime_unit.unit_id] = runtime_unit
        return discovered

    def _reconcile(self) -> None:
        units = self._inventory_call(
            lambda: self._inventory.unacknowledged(limit=self._max_discovered_units)
        )
        if len(units) > self._max_discovered_units:
            self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
        by_id: dict[UUID, ManagedUnit] = {}
        for unit in units:
            if unit.unit_id in by_id:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            by_id[unit.unit_id] = unit

        discovered = self._discovered()
        for unit_id, runtime_unit in discovered.items():
            inventory_unit = by_id.get(unit_id)
            if inventory_unit is None:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            if inventory_unit.state in {
                ManagedUnitState.RESERVED,
                ManagedUnitState.REMOVED,
            }:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            self._validate_runtime_unit(
                inventory_unit, runtime_unit, require_persisted=True
            )

        for unit in units:
            self._reconcile_unit(unit, discovered.get(unit.unit_id))

    def _reconcile_unit(
        self, unit: ManagedUnit, runtime_unit: RuntimeUnit | None
    ) -> None:
        if unit.state is ManagedUnitState.RESERVED:
            if runtime_unit is not None or not self._inventory_call(
                lambda: self._inventory.discard_reserved(
                    unit.unit_id,
                    expected_revision=unit.revision,
                )
            ):
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            return
        if unit.state is ManagedUnitState.CREATE_INTENT:
            if runtime_unit is None:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            created = self._inventory_call(
                lambda: self._inventory.transition(
                    unit.unit_id,
                    expected_revision=unit.revision,
                    target=ManagedUnitState.CREATED,
                    runtime_incarnation=runtime_unit.incarnation,
                )
            )
            self._terminate_and_prove(created, runtime_unit)
            return
        if unit.state is ManagedUnitState.REMOVED:
            if runtime_unit is not None:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            self._retained_proof(unit)
            return
        if runtime_unit is None:
            if unit.state is not ManagedUnitState.EMPTY_CONFIRMED:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            if unit.runtime_incarnation is None:
                self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
            runtime_unit = _StoredRuntimeUnit(unit.unit_id, unit.runtime_incarnation)
        self._terminate_and_prove(unit, runtime_unit)

    def _terminate_and_prove(
        self, unit: ManagedUnit, runtime_unit: RuntimeUnit
    ) -> TerminationProof:
        current = unit
        if current.state is ManagedUnitState.CREATED:
            self._runtime.hard_terminate(runtime_unit)
            exit_evidence = self._runtime.confirm_exit(runtime_unit)
            current = self._inventory_call(
                lambda: self._inventory.transition(
                    current.unit_id,
                    expected_revision=current.revision,
                    target=ManagedUnitState.EXIT_CONFIRMED,
                    evidence=exit_evidence,
                )
            )
        if current.state is ManagedUnitState.EXIT_CONFIRMED:
            empty_evidence = self._runtime.confirm_empty(runtime_unit)
            current = self._inventory_call(
                lambda: self._inventory.transition(
                    current.unit_id,
                    expected_revision=current.revision,
                    target=ManagedUnitState.EMPTY_CONFIRMED,
                    evidence=empty_evidence,
                )
            )
        if current.state is ManagedUnitState.EMPTY_CONFIRMED:
            self._runtime.remove(runtime_unit)
            removal_evidence = self._runtime.confirm_removed(runtime_unit)
            proof = self._proof_for(current, removal_evidence)
            current = self._inventory_call(
                lambda: self._inventory.mark_removed(
                    current.unit_id,
                    expected_revision=current.revision,
                    removal_evidence=removal_evidence,
                    proof=proof,
                )
            )
        if current.state is not ManagedUnitState.REMOVED:
            self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN)
        return self._retained_proof(current)

    def _proof_for(
        self, unit: ManagedUnit, removal_evidence: EvidenceDigest
    ) -> TerminationProof:
        if unit.exit_evidence is None or unit.empty_evidence is None:
            self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN)
        proof_id = uuid5(
            _PROOF_NAMESPACE,
            "\0".join(
                (
                    str(unit.attempt_id),
                    str(unit.unit_id),
                    unit.exit_evidence.value,
                    unit.empty_evidence.value,
                    removal_evidence.value,
                )
            ),
        )
        return TerminationProof(
            proof_id,
            unit.attempt_id,
            unit.unit_id,
            unit.principal,
            unit.policy_revision,
            unit.exit_evidence,
            unit.empty_evidence,
            removal_evidence,
        )

    def _retained_proof(self, unit: ManagedUnit) -> TerminationProof:
        proof = self._inventory_call(lambda: self._inventory.get_proof(unit.unit_id))
        if (
            proof is None
            or proof.attempt_id != unit.attempt_id
            or proof.unit_id != unit.unit_id
            or proof.principal != unit.principal
            or proof.policy_revision != unit.policy_revision
            or proof.exit_evidence != unit.exit_evidence
            or proof.empty_evidence != unit.empty_evidence
            or proof.removal_evidence != unit.removal_evidence
        ):
            self._fail(BrokerErrorCategory.TERMINATION_UNPROVEN)
        return proof

    def _validate_runtime_unit(
        self,
        unit: ManagedUnit,
        runtime_unit: RuntimeUnit,
        *,
        require_persisted: bool = False,
    ) -> None:
        if runtime_unit.unit_id != unit.unit_id:
            self._fail(BrokerErrorCategory.RUNTIME_FAILURE)
        if runtime_unit.incarnation.specification != unit.policy_specification:
            category = (
                BrokerErrorCategory.RECONCILIATION_INCOMPLETE
                if require_persisted
                else BrokerErrorCategory.RUNTIME_FAILURE
            )
            self._fail(category)
        if (
            require_persisted
            and unit.runtime_incarnation is not None
            and runtime_unit.incarnation != unit.runtime_incarnation
        ):
            self._fail(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)

    def _inventory_call[T](self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except BrokerError as error:
            if error.category is BrokerErrorCategory.INVENTORY_FAILURE:
                self._ready = False
            raise
        except Exception as error:
            self._fail(BrokerErrorCategory.INVENTORY_FAILURE, cause=error)

    @staticmethod
    def _validate_request(*values: object) -> None:
        if any(
            type(value) not in {UUID, ReplayPosition, AuthenticatedPrincipal}
            for value in values
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)

    @staticmethod
    def _require_owner(
        unit: ManagedUnit,
        principal: AuthenticatedPrincipal,
        attempt_id: UUID,
        unit_id: UUID | None = None,
    ) -> None:
        if (
            unit.principal != principal
            or unit.attempt_id != attempt_id
            or (unit_id is not None and unit.unit_id != unit_id)
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)

    def _require_ready(self) -> None:
        if not self._ready:
            raise BrokerError(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)

    def _fail(
        self,
        category: BrokerErrorCategory,
        *,
        cause: Exception | None = None,
    ) -> Never:
        self._ready = False
        error = BrokerError(category)
        if cause is None:
            raise error
        raise error from cause
