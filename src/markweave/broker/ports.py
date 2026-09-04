"""Runtime and inventory ports for the reverse-isolation broker."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    TerminationProof,
)


class BrokerInventory(Protocol):
    """Crash-consistent content-free managed-unit inventory contract."""

    def reserve(self, unit: ManagedUnit, replay: ReplayPosition) -> ManagedUnit:
        """Reserve and advance create high water, idempotent for the exact attempt."""

    def create_sequence_high_watermark(self, principal_id: UUID) -> int:
        """Return durable create high water retained after tombstone deletion."""

    def discard_reserved(self, unit_id: UUID, *, expected_revision: int) -> bool:
        """Delete only a pre-create reservation while retaining create high water."""

    def get(self, unit_id: UUID) -> ManagedUnit | None: ...

    def find_attempt(
        self, principal_id: UUID, attempt_id: UUID
    ) -> ManagedUnit | None: ...

    def unacknowledged(self, *, limit: int) -> tuple[ManagedUnit, ...]:
        """Return every verified unit including REMOVED proof tombstones."""

    def transition(
        self,
        unit_id: UUID,
        *,
        expected_revision: int,
        target: ManagedUnitState,
        evidence: EvidenceDigest | None = None,
        runtime_incarnation: RuntimeIncarnation | None = None,
    ) -> ManagedUnit:
        """Persist one legal monotonic transition with compare-and-swap fencing."""

    def mark_removed(
        self,
        unit_id: UUID,
        *,
        expected_revision: int,
        removal_evidence: EvidenceDigest,
        proof: TerminationProof,
    ) -> ManagedUnit:
        """Atomically persist REMOVED with its exact retained proof."""

    def get_proof(self, unit_id: UUID) -> TerminationProof | None: ...

    def acknowledge(
        self,
        principal_id: UUID,
        attempt_id: UUID,
        unit_id: UUID,
        proof_id: UUID,
    ) -> bool:
        """Idempotently acknowledge the exact principal-bound retained proof."""


class RuntimeUnit(Protocol):
    """Opaque backend identity for one exact broker-authored unit."""

    @property
    def unit_id(self) -> UUID: ...

    @property
    def incarnation(self) -> RuntimeIncarnation: ...


class IsolationRuntime(Protocol):
    """Backend contract requiring positive exit, emptiness and removal evidence."""

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> RuntimeUnit:
        """Create only a CREATE_INTENT unit using the fixed image/argument policy."""

    def hard_terminate(self, runtime_unit: RuntimeUnit) -> None:
        """Request termination of the whole stable unit and every descendant."""

    def confirm_exit(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Return positive runtime-confirmed exit evidence, never PID exit alone."""

    def confirm_empty(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Return positive stable-unit emptiness evidence, never absence alone."""

    def remove(self, runtime_unit: RuntimeUnit) -> None:
        """Request removal; successful acknowledgement is not proof."""

    def confirm_removed(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Return positive removal evidence, never absence or delete receipt alone."""

    def discover(self, *, limit: int) -> tuple[RuntimeUnit, ...]:
        """Discover broker-labelled units only as supplementary evidence."""
