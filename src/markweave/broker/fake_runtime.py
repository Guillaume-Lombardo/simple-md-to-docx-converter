"""Deterministic fault-injecting isolation runtime for broker lifecycle tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid5

from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    policy_specification_evidence,
)
from markweave.broker.ports import RuntimeUnit
from markweave.reversions.models import ReverseAttemptRequest, ReverseAttemptResponse

_INCARNATION_NAMESPACE = UUID("90ad36ea-46fe-46d2-90d0-445945e75ee0")
FaultPoint = Literal["before", "after"]


class FakeRuntimeError(RuntimeError):
    """Content-free deterministic fake-runtime fault."""


@dataclass(frozen=True, slots=True)
class FakeRuntimeUnit:
    """Opaque fake backend identity implementing the runtime-unit port."""

    unit_id: UUID
    incarnation: RuntimeIncarnation


@dataclass(frozen=True, slots=True)
class FakeRuntimeState:
    """Validated restart state used to seed a fake runtime record."""

    terminated: bool = False
    exit_confirmed: bool = False
    empty_confirmed: bool = False
    removed: bool = False

    def __post_init__(self) -> None:
        values = (
            self.terminated,
            self.exit_confirmed,
            self.empty_confirmed,
            self.removed,
        )
        if any(type(value) is not bool for value in values):
            raise ValueError("Fake runtime state is invalid")
        if self.removed and not self.empty_confirmed:
            raise ValueError("Removed fake unit requires emptiness evidence")
        if self.empty_confirmed and not self.exit_confirmed:
            raise ValueError("Empty fake unit requires exit evidence")
        if self.exit_confirmed and not self.terminated:
            raise ValueError("Exited fake unit requires termination")


_INITIAL_RUNTIME_STATE = FakeRuntimeState()


@dataclass(slots=True)
class _RuntimeRecord:
    unit: FakeRuntimeUnit
    attempt_id: UUID | None = None
    channel_limits: RuntimeChannelLimits | None = None
    terminated: bool = False
    exit_confirmed: bool = False
    empty_confirmed: bool = False
    removed: bool = False
    request: ReverseAttemptRequest | None = None
    response: ReverseAttemptResponse | None = None


def _digest(*parts: object) -> EvidenceDigest:
    encoded = "\0".join(str(part) for part in parts).encode("ascii")
    return EvidenceDigest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


class FakeIsolationRuntime:
    """In-memory runtime with exact operation ordering and deterministic faults."""

    def __init__(self, event_sink: Callable[[str], None] | None = None) -> None:
        self._records: dict[UUID, _RuntimeRecord] = {}
        self._faults: dict[tuple[str, FaultPoint], int] = {}
        self._calls: list[str] = []
        self._event_sink = event_sink

    @property
    def calls(self) -> tuple[str, ...]:
        """Return the ordered content-free operation trace."""

        return tuple(self._calls)

    def inject_fault(
        self, operation: str, *, point: FaultPoint = "before", count: int = 1
    ) -> None:
        """Raise deterministically at a named operation checkpoint."""

        if not operation or type(operation) is not str:
            raise ValueError("Fake runtime operation is invalid")
        if point not in {"before", "after"} or type(count) is not int or count <= 0:
            raise ValueError("Fake runtime fault is invalid")
        self._faults[(operation, point)] = count

    def _checkpoint(self, operation: str, point: FaultPoint) -> None:
        event = f"{operation}:{point}"
        self._calls.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
        key = (operation, point)
        remaining = self._faults.get(key, 0)
        if remaining:
            if remaining == 1:
                del self._faults[key]
            else:
                self._faults[key] = remaining - 1
            raise FakeRuntimeError("Injected isolation runtime failure")

    def _record(self, runtime_unit: RuntimeUnit) -> _RuntimeRecord:
        record = self._records.get(runtime_unit.unit_id)
        if record is None or record.unit.incarnation != runtime_unit.incarnation:
            raise FakeRuntimeError("Unknown isolation runtime unit")
        return record

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> FakeRuntimeUnit:
        """Create one exact incarnation only after durable CREATE_INTENT."""

        self._checkpoint("create", "before")
        if (
            unit.state is not ManagedUnitState.CREATE_INTENT
            or unit.policy_revision != policy.revision
            or unit.policy_specification != policy_specification_evidence(policy)
            or unit.unit_id in self._records
        ):
            raise FakeRuntimeError("Isolation runtime create contract failed")
        incarnation = RuntimeIncarnation(
            uuid5(_INCARNATION_NAMESPACE, str(unit.unit_id)),
            policy_specification_evidence(policy),
        )
        runtime_unit = FakeRuntimeUnit(unit.unit_id, incarnation)
        self._records[unit.unit_id] = _RuntimeRecord(
            runtime_unit,
            attempt_id=unit.attempt_id,
            channel_limits=policy.channel_limits,
        )
        self._checkpoint("create", "after")
        return runtime_unit

    def hard_terminate(self, runtime_unit: RuntimeUnit) -> None:
        """Mark the complete stable unit terminated, idempotently."""

        self._checkpoint("hard_terminate", "before")
        record = self._record(runtime_unit)
        if record.removed:
            raise FakeRuntimeError("Isolation runtime unit was already removed")
        record.terminated = True
        self._checkpoint("hard_terminate", "after")

    def stage_request(
        self, runtime_unit: RuntimeUnit, request: ReverseAttemptRequest
    ) -> None:
        """Stage one exact request for port-level orchestration tests."""

        self._checkpoint("stage_request", "before")
        record = self._record(runtime_unit)
        if (
            record.terminated
            or record.removed
            or type(request) is not ReverseAttemptRequest
            or record.request is not None
            or record.attempt_id != request.attempt_id
            or record.channel_limits is None
            or len(request.source) > record.channel_limits.max_input_bytes
            or request.limits.max_input_bytes > record.channel_limits.max_input_bytes
            or request.limits.max_output_bytes > record.channel_limits.max_output_bytes
        ):
            raise FakeRuntimeError("Isolation runtime workspace contract failed")
        record.request = request
        self._checkpoint("stage_request", "after")

    def try_collect_response(
        self, runtime_unit: RuntimeUnit, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        """Collect only an attempt-bound response configured by a test."""

        self._checkpoint("try_collect_response", "before")
        record = self._record(runtime_unit)
        response = record.response
        if (
            record.terminated
            or record.removed
            or type(expected_attempt_id) is not UUID
            or record.attempt_id != expected_attempt_id
            or (response is not None and response.attempt_id != expected_attempt_id)
        ):
            raise FakeRuntimeError("Isolation runtime workspace contract failed")
        self._checkpoint("try_collect_response", "after")
        return response

    def confirm_exit(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Return stable positive exit evidence after whole-unit termination."""

        self._checkpoint("confirm_exit", "before")
        record = self._record(runtime_unit)
        if not record.terminated or record.removed:
            raise FakeRuntimeError("Isolation runtime exit is unconfirmed")
        record.exit_confirmed = True
        evidence = _digest(
            "exit", record.unit.unit_id, record.unit.incarnation.incarnation_id
        )
        self._checkpoint("confirm_exit", "after")
        return evidence

    def confirm_empty(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Return stable positive descendant-emptiness evidence after exit."""

        self._checkpoint("confirm_empty", "before")
        record = self._record(runtime_unit)
        if not record.exit_confirmed or record.removed:
            raise FakeRuntimeError("Isolation runtime emptiness is unconfirmed")
        record.empty_confirmed = True
        evidence = _digest(
            "empty", record.unit.unit_id, record.unit.incarnation.incarnation_id
        )
        self._checkpoint("confirm_empty", "after")
        return evidence

    def remove(self, runtime_unit: RuntimeUnit) -> None:
        """Remove only a unit with positive emptiness evidence, idempotently."""

        self._checkpoint("remove", "before")
        record = self._record(runtime_unit)
        if not record.empty_confirmed:
            raise FakeRuntimeError("Isolation runtime removal contract failed")
        record.removed = True
        self._checkpoint("remove", "after")

    def confirm_removed(
        self, runtime_unit: RuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        """Return evidence stronger than a removal request acknowledgement."""

        self._checkpoint("confirm_removed", "before")
        record = self._record(runtime_unit)
        expected_empty = _digest(
            "empty", record.unit.unit_id, record.unit.incarnation.incarnation_id
        )
        if not record.removed or empty_evidence != expected_empty:
            raise FakeRuntimeError("Isolation runtime removal is unconfirmed")
        evidence = _digest(
            "removed",
            record.unit.unit_id,
            record.unit.incarnation.incarnation_id,
            empty_evidence.value,
        )
        self._checkpoint("confirm_removed", "after")
        return evidence

    def discover(self, *, limit: int) -> tuple[FakeRuntimeUnit, ...]:
        """Return every non-removed labelled fake unit in stable order."""

        self._checkpoint("discover", "before")
        if type(limit) is not int or limit <= 0:
            raise FakeRuntimeError("Isolation runtime discovery limit is invalid")
        discovered = tuple(
            self._records[unit_id].unit
            for unit_id in sorted(self._records, key=str)
            if not self._records[unit_id].removed
        )
        if len(discovered) > limit:
            raise FakeRuntimeError("Isolation runtime discovery exceeds its limit")
        self._checkpoint("discover", "after")
        return discovered

    def seed(
        self,
        unit_id: UUID,
        incarnation: RuntimeIncarnation,
        state: FakeRuntimeState = _INITIAL_RUNTIME_STATE,
    ) -> FakeRuntimeUnit:
        """Seed exact restart state without recording a runtime call."""

        if (
            unit_id in self._records
            or type(incarnation) is not RuntimeIncarnation
            or type(state) is not FakeRuntimeState
        ):
            raise ValueError("Fake runtime seed is invalid")
        runtime_unit = FakeRuntimeUnit(unit_id, incarnation)
        self._records[unit_id] = _RuntimeRecord(
            runtime_unit,
            terminated=state.terminated,
            exit_confirmed=state.exit_confirmed,
            empty_confirmed=state.empty_confirmed,
            removed=state.removed,
        )
        return runtime_unit

    def forget(self, unit_id: UUID) -> None:
        """Simulate runtime absence without manufacturing evidence."""

        self._records.pop(unit_id, None)
