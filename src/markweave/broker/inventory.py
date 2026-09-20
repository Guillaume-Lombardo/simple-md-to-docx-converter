"""Crash-consistent authenticated SQLite inventory for managed broker units."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import markweave.broker.inventory_storage as _inventory_storage
from markweave.broker.errors import BrokerErrorCategory
from markweave.broker.models import (
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    RuntimeRecoveryBinding,
    TerminationProof,
    is_next_unit_state,
)
from markweave.broker.reconciliation_protocol import ReconciliationTombstone

_APPLICATION_ID = _inventory_storage._APPLICATION_ID
_SCHEMA_VERSION = _inventory_storage._SCHEMA_VERSION
_LEGACY_SCHEMA_VERSION = _inventory_storage._LEGACY_SCHEMA_VERSION
_MAC_VERSION = _inventory_storage._MAC_VERSION
_MINIMUM_HMAC_KEY_BYTES = _inventory_storage._MINIMUM_HMAC_KEY_BYTES
_SQLITE_FULL_SYNCHRONOUS = _inventory_storage._SQLITE_FULL_SYNCHRONOUS
_PRINCIPAL_COLUMNS = _inventory_storage._PRINCIPAL_COLUMNS
_MANIFEST_COLUMNS = _inventory_storage._MANIFEST_COLUMNS
_SELECT_PRINCIPALS = _inventory_storage._SELECT_PRINCIPALS
_SELECT_MANIFEST = _inventory_storage._SELECT_MANIFEST
_INSERT_MANIFEST = _inventory_storage._INSERT_MANIFEST
_UNIT_COLUMNS = _inventory_storage._UNIT_COLUMNS
_SELECT_UNITS = _inventory_storage._SELECT_UNITS
_INSERT_UNIT = _inventory_storage._INSERT_UNIT
_UPDATE_UNIT = _inventory_storage._UPDATE_UNIT
_SELECT_UNIT_BY = _inventory_storage._SELECT_UNIT_BY
_CREATE_PRINCIPALS = _inventory_storage._CREATE_PRINCIPALS
_CREATE_UNITS = _inventory_storage._CREATE_UNITS
_LEGACY_UNIT_COLUMNS = _inventory_storage._LEGACY_UNIT_COLUMNS
_LEGACY_SELECT_UNITS = _inventory_storage._LEGACY_SELECT_UNITS
_LEGACY_CREATE_UNITS = _inventory_storage._LEGACY_CREATE_UNITS
_CREATE_MANIFEST = _inventory_storage._CREATE_MANIFEST
_fail = _inventory_storage._fail
_inventory_fail = _inventory_storage._inventory_fail
_SQLiteInventoryStorage = _inventory_storage._SQLiteInventoryStorage


class SQLiteBrokerInventory(_SQLiteInventoryStorage):
    """Bounded local inventory with a canonical HMAC over every durable row."""

    def reserve(self, unit: ManagedUnit, replay: ReplayPosition) -> ManagedUnit:
        """Reserve before runtime mutation, with exact-attempt replay idempotency."""

        if (
            type(unit) is not ManagedUnit
            or type(replay) is not ReplayPosition
            or unit.state is not ManagedUnitState.RESERVED
            or unit.revision != 0
            or unit.runtime_incarnation is not None
            or unit.principal != replay.principal
            or unit.create_sequence != replay.sequence
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            principal_id = str(replay.principal.principal_id)
            same_sequence = connection.execute(
                _SELECT_UNITS + " WHERE principal_id = ? AND create_sequence = ?",
                (principal_id, replay.sequence),
            ).fetchone()
            if same_sequence is not None:
                existing = self._unit_from_row(same_sequence)
                if existing.attempt_id == unit.attempt_id:
                    return existing
                _fail(BrokerErrorCategory.REPLAY_REJECTED)
            high_water = self._principal_high_water(connection, principal_id)
            if replay.sequence <= high_water:
                _fail(BrokerErrorCategory.REPLAY_REJECTED)
            if (
                connection.execute(
                    "SELECT 1 FROM units WHERE unit_id = ? OR attempt_id = ?",
                    (str(unit.unit_id), str(unit.attempt_id)),
                ).fetchone()
                is not None
            ):
                _fail(BrokerErrorCategory.REPLAY_REJECTED)
            if (
                self._scalar(connection, "SELECT COUNT(*) FROM units")
                >= self._max_records
            ):
                _fail(BrokerErrorCategory.INVENTORY_FULL)
            if high_water == 0 and (
                self._scalar(connection, "SELECT COUNT(*) FROM principals")
                >= self._max_records
            ):
                _fail(BrokerErrorCategory.INVENTORY_FULL)

            self._store_principal(connection, principal_id, replay.sequence)
            values: tuple[Any, ...] = (
                str(unit.unit_id),
                str(unit.attempt_id),
                principal_id,
                unit.create_sequence,
                unit.policy_revision,
                unit.policy_specification.value,
                self._runtime_name(unit.unit_id),
                unit.state.value,
                unit.revision,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                _MAC_VERSION,
            )
            connection.execute(_INSERT_UNIT, (*values, self._mac("unit", values)))
            self._refresh_manifest(connection)
            row = self._select_unit(connection, "unit_id", str(unit.unit_id))
            if row is None:
                _inventory_fail()
            return self._unit_from_row(row)

    def create_sequence_high_watermark(self, principal_id: UUID) -> int:
        if type(principal_id) is not UUID:
            _fail()
        with self._verified_connection() as connection:
            return self._principal_high_water(connection, str(principal_id))

    def reconciliation_page(
        self, principal_id: UUID, after_create_sequence: int
    ) -> tuple[int, ReconciliationTombstone | None]:
        """Return only the authenticated principal's next retained proof."""

        if (
            type(principal_id) is not UUID
            or type(after_create_sequence) is not int
            or not 0 <= after_create_sequence <= (1 << 63) - 1
        ):
            _fail()
        principal = str(principal_id)
        with self._verified_connection() as connection:
            high_water = self._principal_high_water(connection, principal)
            row = connection.execute(
                _SELECT_UNITS + " WHERE principal_id = ? AND create_sequence > ? "
                "AND state = ? AND proof_id IS NOT NULL "
                "ORDER BY create_sequence, unit_id LIMIT 1",
                (principal, after_create_sequence, ManagedUnitState.REMOVED.value),
            ).fetchone()
            if row is None:
                return high_water, None
            unit = self._unit_from_row(row)
            proof = self._proof_from_row(row)
            return high_water, ReconciliationTombstone(
                unit.create_sequence, unit.policy_specification, proof
            )

    def discard_reserved(self, unit_id: UUID, *, expected_revision: int) -> bool:
        """Discard only a proven pre-create reservation and retain replay state."""

        if (
            type(unit_id) is not UUID
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            cursor = connection.execute(
                "DELETE FROM units WHERE unit_id = ? AND state = ? AND revision = ?",
                (
                    str(unit_id),
                    ManagedUnitState.RESERVED.value,
                    expected_revision,
                ),
            )
            if cursor.rowcount == 1:
                self._refresh_manifest(connection)
            return cursor.rowcount == 1

    def get(self, unit_id: UUID) -> ManagedUnit | None:
        if type(unit_id) is not UUID:
            _fail()
        with self._verified_connection() as connection:
            row = self._select_unit(connection, "unit_id", str(unit_id))
            return None if row is None else self._unit_from_row(row)

    def find_attempt(self, principal_id: UUID, attempt_id: UUID) -> ManagedUnit | None:
        if type(principal_id) is not UUID or type(attempt_id) is not UUID:
            _fail()
        with self._verified_connection() as connection:
            row = connection.execute(
                _SELECT_UNITS + " WHERE principal_id = ? AND attempt_id = ?",
                (str(principal_id), str(attempt_id)),
            ).fetchone()
            return None if row is None else self._unit_from_row(row)

    def unacknowledged(self, *, limit: int) -> tuple[ManagedUnit, ...]:
        """Return a bounded ordered sweep, including REMOVED proof tombstones."""

        if type(limit) is not int or not 1 <= limit <= self._max_records:
            _fail()
        with self._verified_connection() as connection:
            rows = connection.execute(
                _SELECT_UNITS
                + " ORDER BY principal_id, create_sequence, unit_id LIMIT ?",
                (limit + 1,),
            ).fetchall()
            if len(rows) > limit:
                _fail(BrokerErrorCategory.INVENTORY_FULL)
            return tuple(self._unit_from_row(row) for row in rows)

    def transition(  # noqa: PLR0913 - protocol transition has explicit fenced fields
        self,
        unit_id: UUID,
        *,
        expected_revision: int,
        target: ManagedUnitState,
        evidence: EvidenceDigest | None = None,
        runtime_incarnation: RuntimeIncarnation | None = None,
        runtime_recovery: RuntimeRecoveryBinding | None = None,
    ) -> ManagedUnit:
        """Commit one legal pre-removal transition with revision fencing."""

        if (
            type(unit_id) is not UUID
            or type(expected_revision) is not int
            or expected_revision < 0
            or type(target) is not ManagedUnitState
            or target is ManagedUnitState.REMOVED
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None:
                _fail()
            current = self._unit_from_row(row)
            if current.revision != expected_revision or not is_next_unit_state(
                current.state, target
            ):
                _fail(BrokerErrorCategory.REPLAY_REJECTED)
            self._validate_transition_payload(
                target, evidence, runtime_incarnation, runtime_recovery
            )
            updated = list(row[:-1])
            updated[_UNIT_COLUMNS.index("state")] = target.value
            updated[_UNIT_COLUMNS.index("revision")] = expected_revision + 1
            if runtime_incarnation is not None:
                updated[_UNIT_COLUMNS.index("incarnation_id")] = str(
                    runtime_incarnation.incarnation_id
                )
                updated[_UNIT_COLUMNS.index("specification")] = (
                    runtime_incarnation.specification.value
                )
            if runtime_recovery is not None:
                updated[_UNIT_COLUMNS.index("recovery_backend")] = (
                    runtime_recovery.backend
                )
                updated[_UNIT_COLUMNS.index("recovery_version")] = (
                    runtime_recovery.schema_version
                )
                updated[_UNIT_COLUMNS.index("recovery_payload")] = (
                    runtime_recovery.payload
                )
            evidence_column = self._evidence_column(target)
            if evidence_column is not None and evidence is not None:
                updated[_UNIT_COLUMNS.index(evidence_column)] = evidence.value
            return self._write_updated_unit(
                connection, updated, unit_id, expected_revision
            )

    def mark_removed(
        self,
        unit_id: UUID,
        *,
        expected_revision: int,
        removal_evidence: EvidenceDigest,
        proof: TerminationProof,
    ) -> ManagedUnit:
        """Atomically persist REMOVED, positive evidence, and the proof tombstone."""

        if (
            type(unit_id) is not UUID
            or type(expected_revision) is not int
            or expected_revision < 0
            or type(removal_evidence) is not EvidenceDigest
            or type(proof) is not TerminationProof
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None:
                _fail()
            unit = self._unit_from_row(row)
            if (
                unit.state is not ManagedUnitState.EMPTY_CONFIRMED
                or unit.revision != expected_revision
                or unit.unit_id != proof.unit_id
                or unit.attempt_id != proof.attempt_id
                or unit.principal != proof.principal
                or unit.policy_revision != proof.policy_revision
                or unit.exit_evidence != proof.exit_evidence
                or unit.empty_evidence != proof.empty_evidence
                or removal_evidence != proof.removal_evidence
            ):
                _fail()
            updated = list(row[:-1])
            updated[_UNIT_COLUMNS.index("state")] = ManagedUnitState.REMOVED.value
            updated[_UNIT_COLUMNS.index("revision")] = expected_revision + 1
            updated[_UNIT_COLUMNS.index("removal_evidence")] = removal_evidence.value
            updated[_UNIT_COLUMNS.index("proof_id")] = str(proof.proof_id)
            return self._write_updated_unit(
                connection, updated, unit_id, expected_revision
            )

    def get_proof(self, unit_id: UUID) -> TerminationProof | None:
        if type(unit_id) is not UUID:
            _fail()
        with self._verified_connection() as connection:
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None or row[_UNIT_COLUMNS.index("proof_id")] is None:
                return None
            return self._proof_from_row(row)

    def acknowledge(
        self,
        principal_id: UUID,
        attempt_id: UUID,
        unit_id: UUID,
        proof_id: UUID,
    ) -> bool:
        """Delete only the exact principal/attempt/unit/proof-bound tombstone."""

        if any(
            type(value) is not UUID
            for value in (principal_id, attempt_id, unit_id, proof_id)
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None:
                return True
            unit = self._unit_from_row(row)
            if unit.state is not ManagedUnitState.REMOVED:
                return False
            proof = self._proof_from_row(row)
            if (
                proof.principal.principal_id != principal_id
                or proof.attempt_id != attempt_id
                or proof.proof_id != proof_id
            ):
                return False
            connection.execute("DELETE FROM units WHERE unit_id = ?", (str(unit_id),))
            self._refresh_manifest(connection)
            return True

    def mark_acknowledged(
        self,
        principal_id: UUID,
        attempt_id: UUID,
        unit_id: UUID,
        proof_id: UUID,
    ) -> ManagedUnit | None:
        """Durably mark an exact retained proof acknowledged, idempotently."""

        if any(
            type(value) is not UUID
            for value in (principal_id, attempt_id, unit_id, proof_id)
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None:
                return None
            unit = self._unit_from_row(row)
            if unit.state is not ManagedUnitState.REMOVED:
                return None
            proof = self._proof_from_row(row)
            if (
                proof.principal.principal_id != principal_id
                or proof.attempt_id != attempt_id
                or proof.proof_id != proof_id
            ):
                return None
            if unit.proof_acknowledged:
                return unit
            updated = list(row[:-1])
            updated[_UNIT_COLUMNS.index("revision")] = unit.revision + 1
            updated[_UNIT_COLUMNS.index("proof_acknowledged")] = 1
            return self._write_updated_unit(connection, updated, unit_id, unit.revision)

    def discard_acknowledged(self, unit_id: UUID, *, expected_revision: int) -> bool:
        """Delete only an exact durably acknowledged proof tombstone."""

        if (
            type(unit_id) is not UUID
            or type(expected_revision) is not int
            or expected_revision < 0
        ):
            _fail()
        with self._transaction() as connection:
            self._verify_all(connection)
            cursor = connection.execute(
                "DELETE FROM units WHERE unit_id = ? AND state = ? "
                "AND proof_acknowledged = 1 AND revision = ?",
                (str(unit_id), ManagedUnitState.REMOVED.value, expected_revision),
            )
            if cursor.rowcount == 1:
                self._refresh_manifest(connection)
            return cursor.rowcount == 1
