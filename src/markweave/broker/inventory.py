"""Crash-consistent authenticated SQLite inventory for managed broker units."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Never
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    TerminationProof,
    is_next_unit_state,
)

_APPLICATION_ID = 0x4D574249  # MWBI
_SCHEMA_VERSION = 3
_MAC_VERSION = 1
_MINIMUM_HMAC_KEY_BYTES = 32
_SQLITE_FULL_SYNCHRONOUS = 2
_PRINCIPAL_COLUMNS = (
    "principal_id",
    "high_water",
    "mac_version",
    "mac",
)
_ACKNOWLEDGEMENT_COLUMNS = (
    "proof_id",
    "principal_id",
    "attempt_id",
    "unit_id",
    "create_sequence",
    "mac_version",
    "mac",
)
_MANIFEST_COLUMNS = (
    "singleton_id",
    "generation",
    "principal_count",
    "unit_count",
    "acknowledgement_count",
    "principals_digest",
    "units_digest",
    "acknowledgements_digest",
    "mac_version",
    "mac",
)
_SELECT_PRINCIPALS = (
    "SELECT "  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(_PRINCIPAL_COLUMNS)
    + " FROM principals"
)
_SELECT_ACKNOWLEDGEMENTS = (
    "SELECT "  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(_ACKNOWLEDGEMENT_COLUMNS)
    + " FROM acknowledgements"
)
_INSERT_ACKNOWLEDGEMENT = (
    "INSERT INTO acknowledgements ("  # noqa: S608 - fixed identifiers
    + ", ".join(_ACKNOWLEDGEMENT_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _ACKNOWLEDGEMENT_COLUMNS)
    + ")"
)
_SELECT_MANIFEST = (
    "SELECT "  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(_MANIFEST_COLUMNS)
    + " FROM inventory_manifest"
)
_INSERT_MANIFEST = (
    "INSERT INTO inventory_manifest ("  # noqa: S608 - fixed identifiers
    + ", ".join(_MANIFEST_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _MANIFEST_COLUMNS)
    + ")"
)
_UNIT_COLUMNS = (
    "unit_id",
    "attempt_id",
    "principal_id",
    "create_sequence",
    "policy_revision",
    "policy_specification",
    "runtime_name",
    "state",
    "revision",
    "incarnation_id",
    "specification",
    "exit_evidence",
    "empty_evidence",
    "removal_evidence",
    "proof_id",
    "mac_version",
    "mac",
)
_SELECT_UNITS = (
    "SELECT "  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(_UNIT_COLUMNS)
    + " FROM units"
)
_INSERT_UNIT = (
    "INSERT INTO units ("  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(_UNIT_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _UNIT_COLUMNS)
    + ")"
)
_UPDATE_UNIT = (
    "UPDATE units SET "  # noqa: S608 - identifiers are fixed module constants
    + ", ".join(f"{column} = ?" for column in _UNIT_COLUMNS[1:])
    + " WHERE unit_id = ? AND revision = ?"
)
_SELECT_UNIT_BY = {
    column: _SELECT_UNITS + f" WHERE {column} = ?" for column in ("unit_id", "proof_id")
}
_CREATE_PRINCIPALS = (
    "CREATE TABLE principals ("
    "principal_id TEXT PRIMARY KEY NOT NULL,"
    "high_water INTEGER NOT NULL CHECK (high_water > 0),"
    "mac_version INTEGER NOT NULL,"
    "mac BLOB NOT NULL"
    ") STRICT"
)
_CREATE_ACKNOWLEDGEMENTS = (
    "CREATE TABLE acknowledgements ("
    "proof_id TEXT PRIMARY KEY NOT NULL,"
    "principal_id TEXT NOT NULL REFERENCES principals(principal_id),"
    "attempt_id TEXT UNIQUE NOT NULL,"
    "unit_id TEXT UNIQUE NOT NULL,"
    "create_sequence INTEGER NOT NULL CHECK (create_sequence > 0),"
    "mac_version INTEGER NOT NULL,"
    "mac BLOB NOT NULL,"
    "UNIQUE (principal_id, create_sequence)"
    ") STRICT"
)
_CREATE_UNITS = (
    "CREATE TABLE units ("
    "unit_id TEXT PRIMARY KEY NOT NULL,"
    "attempt_id TEXT UNIQUE NOT NULL,"
    "principal_id TEXT NOT NULL REFERENCES principals(principal_id),"
    "create_sequence INTEGER NOT NULL CHECK (create_sequence > 0),"
    "policy_revision TEXT NOT NULL,"
    "policy_specification TEXT NOT NULL,"
    "runtime_name TEXT UNIQUE NOT NULL,"
    "state TEXT NOT NULL,"
    "revision INTEGER NOT NULL CHECK (revision >= 0),"
    "incarnation_id TEXT,"
    "specification TEXT,"
    "exit_evidence TEXT,"
    "empty_evidence TEXT,"
    "removal_evidence TEXT,"
    "proof_id TEXT UNIQUE,"
    "mac_version INTEGER NOT NULL,"
    "mac BLOB NOT NULL,"
    "UNIQUE (principal_id, create_sequence)"
    ") STRICT"
)
_CREATE_MANIFEST = (
    "CREATE TABLE inventory_manifest ("
    "singleton_id INTEGER PRIMARY KEY NOT NULL CHECK (singleton_id = 1),"
    "generation INTEGER NOT NULL CHECK (generation >= 0),"
    "principal_count INTEGER NOT NULL CHECK (principal_count >= 0),"
    "unit_count INTEGER NOT NULL CHECK (unit_count >= 0),"
    "acknowledgement_count INTEGER NOT NULL CHECK (acknowledgement_count >= 0),"
    "principals_digest TEXT NOT NULL,"
    "units_digest TEXT NOT NULL,"
    "acknowledgements_digest TEXT NOT NULL,"
    "mac_version INTEGER NOT NULL,"
    "mac BLOB NOT NULL"
    ") STRICT"
)


def _fail(category: BrokerErrorCategory = BrokerErrorCategory.PROTOCOL_ERROR) -> Never:
    raise BrokerError(category)


def _inventory_fail() -> Never:
    _fail(BrokerErrorCategory.INVENTORY_FAILURE)


class SQLiteBrokerInventory:
    """Bounded local inventory with a canonical HMAC over every durable row."""

    def __init__(
        self, path: Path, authentication_key: bytes, *, max_records: int
    ) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or str(path) == ":memory:"
        ):
            raise ValueError("Broker inventory path must be an absolute local file")
        if (
            type(authentication_key) is not bytes
            or len(authentication_key) < _MINIMUM_HMAC_KEY_BYTES
        ):
            raise ValueError("Broker inventory authentication key is invalid")
        if type(max_records) is not int or max_records <= 0:
            raise ValueError("Broker inventory capacity must be a positive integer")
        self._path = path
        self._key = authentication_key
        self._max_records = max_records
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                self._initialize_or_verify_schema(connection)
                self._verify_all(connection)
        except OSError, sqlite3.DatabaseError, ValueError:
            _inventory_fail()

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

    def transition(
        self,
        unit_id: UUID,
        *,
        expected_revision: int,
        target: ManagedUnitState,
        evidence: EvidenceDigest | None = None,
        runtime_incarnation: RuntimeIncarnation | None = None,
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
            self._validate_transition_payload(target, evidence, runtime_incarnation)
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
            receipt = connection.execute(
                _SELECT_ACKNOWLEDGEMENTS + " WHERE proof_id = ?", (str(proof_id),)
            ).fetchone()
            if receipt is not None:
                self._verified_acknowledgement(receipt)
                return self._acknowledgement_identity(receipt) == (
                    str(principal_id),
                    str(attempt_id),
                    str(unit_id),
                    str(proof_id),
                )
            row = self._select_unit(connection, "unit_id", str(unit_id))
            if row is None:
                return False
            proof = self._proof_from_row(row)
            if (
                proof.principal.principal_id != principal_id
                or proof.attempt_id != attempt_id
                or proof.proof_id != proof_id
            ):
                return False
            if (
                self._scalar(connection, "SELECT COUNT(*) FROM acknowledgements")
                >= self._max_records
            ):
                _fail(BrokerErrorCategory.INVENTORY_FULL)
            unit = self._unit_from_row(row)
            values = (
                str(proof_id),
                str(principal_id),
                str(attempt_id),
                str(unit_id),
                unit.create_sequence,
                _MAC_VERSION,
            )
            connection.execute(
                _INSERT_ACKNOWLEDGEMENT,
                (*values, self._mac("acknowledgement", values)),
            )
            connection.execute("DELETE FROM units WHERE unit_id = ?", (str(unit_id),))
            self._refresh_manifest(connection)
            return True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            connection.execute("PRAGMA synchronous = FULL")
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            if journal_mode != "wal" or synchronous != _SQLITE_FULL_SYNCHRONOUS:
                _inventory_fail()
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except sqlite3.DatabaseError:
            _inventory_fail()

    @contextmanager
    def _verified_connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._connect() as connection:
                self._verify_all(connection)
                yield connection
        except sqlite3.DatabaseError, ValueError:
            _inventory_fail()

    def _initialize_or_verify_schema(self, connection: sqlite3.Connection) -> None:
        if not self._table_names(connection):
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(_CREATE_PRINCIPALS)
                connection.execute(_CREATE_ACKNOWLEDGEMENTS)
                connection.execute(_CREATE_UNITS)
                connection.execute(_CREATE_MANIFEST)
                self._insert_empty_manifest(connection)
                connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        self._verify_schema(connection)

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        if (
            connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0]
            != _SCHEMA_VERSION
            or self._table_names(connection)
            != {"acknowledgements", "inventory_manifest", "principals", "units"}
            or self._schema_objects(connection)
            != {
                ("table", "inventory_manifest"),
                ("table", "acknowledgements"),
                ("table", "principals"),
                ("table", "units"),
            }
        ):
            _inventory_fail()
        principal_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(principals)")
        )
        unit_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(units)")
        )
        manifest_columns = tuple(
            row[1]
            for row in connection.execute("PRAGMA table_info(inventory_manifest)")
        )
        acknowledgement_columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(acknowledgements)")
        )
        if (
            principal_columns != _PRINCIPAL_COLUMNS
            or unit_columns != _UNIT_COLUMNS
            or manifest_columns != _MANIFEST_COLUMNS
            or acknowledgement_columns != _ACKNOWLEDGEMENT_COLUMNS
        ):
            _inventory_fail()
        schemas = dict(
            connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' "
                "AND name IN ("
                "'acknowledgements', 'inventory_manifest', 'principals', 'units')"
            ).fetchall()
        )
        if schemas != {
            "acknowledgements": _CREATE_ACKNOWLEDGEMENTS,
            "inventory_manifest": _CREATE_MANIFEST,
            "principals": _CREATE_PRINCIPALS,
            "units": _CREATE_UNITS,
        }:
            _inventory_fail()

    def _verify_all(self, connection: sqlite3.Connection) -> None:
        self._verify_schema(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if [row[0] for row in integrity] != ["ok"] or connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchone() is not None:
            _inventory_fail()
        principals = connection.execute(
            _SELECT_PRINCIPALS + " ORDER BY principal_id"
        ).fetchall()
        units = connection.execute(_SELECT_UNITS + " ORDER BY unit_id").fetchall()
        acknowledgements = connection.execute(
            _SELECT_ACKNOWLEDGEMENTS + " ORDER BY proof_id"
        ).fetchall()
        if (
            len(principals) > self._max_records
            or len(units) > self._max_records
            or len(acknowledgements) > self._max_records
        ):
            _fail(BrokerErrorCategory.INVENTORY_FULL)
        for row in principals:
            self._verified_principal(row)
        for row in units:
            self._unit_from_row(row)
        for row in acknowledgements:
            self._verified_acknowledgement(row)
        self._verify_manifest(connection, principals, units, acknowledgements)

    def _principal_high_water(
        self, connection: sqlite3.Connection, principal_id: str
    ) -> int:
        row = connection.execute(
            _SELECT_PRINCIPALS + " WHERE principal_id = ?",
            (principal_id,),
        ).fetchone()
        return 0 if row is None else self._verified_principal(row)

    def _verified_principal(self, row: Sequence[Any]) -> int:
        values = tuple(row[:-1])
        if (
            len(row) != len(_PRINCIPAL_COLUMNS)
            or type(row[0]) is not str
            or type(row[1]) is not int
            or row[1] <= 0
            or row[_PRINCIPAL_COLUMNS.index("mac_version")] != _MAC_VERSION
            or type(row[-1]) is not bytes
            or not hmac.compare_digest(row[-1], self._mac("principal", values))
        ):
            _inventory_fail()
        try:
            UUID(row[0])
        except ValueError:
            _inventory_fail()
        return row[1]

    def _unit_from_row(self, row: Sequence[Any]) -> ManagedUnit:
        values = tuple(row[:-1])
        if (
            len(row) != len(_UNIT_COLUMNS)
            or row[_UNIT_COLUMNS.index("mac_version")] != _MAC_VERSION
            or type(row[-1]) is not bytes
            or not hmac.compare_digest(row[-1], self._mac("unit", values))
        ):
            _inventory_fail()
        try:
            unit_id = UUID(row[_UNIT_COLUMNS.index("unit_id")])
            if row[_UNIT_COLUMNS.index("runtime_name")] != self._runtime_name(unit_id):
                _inventory_fail()
            incarnation_id = row[_UNIT_COLUMNS.index("incarnation_id")]
            specification = row[_UNIT_COLUMNS.index("specification")]
            incarnation = (
                None
                if incarnation_id is None and specification is None
                else RuntimeIncarnation(
                    UUID(incarnation_id), EvidenceDigest(specification)
                )
            )
            unit = ManagedUnit(
                attempt_id=UUID(row[_UNIT_COLUMNS.index("attempt_id")]),
                unit_id=unit_id,
                principal=AuthenticatedPrincipal(
                    UUID(row[_UNIT_COLUMNS.index("principal_id")])
                ),
                create_sequence=row[_UNIT_COLUMNS.index("create_sequence")],
                policy_revision=row[_UNIT_COLUMNS.index("policy_revision")],
                policy_specification=EvidenceDigest(
                    row[_UNIT_COLUMNS.index("policy_specification")]
                ),
                state=ManagedUnitState(row[_UNIT_COLUMNS.index("state")]),
                revision=row[_UNIT_COLUMNS.index("revision")],
                runtime_incarnation=incarnation,
                exit_evidence=self._optional_evidence(row, "exit_evidence"),
                empty_evidence=self._optional_evidence(row, "empty_evidence"),
                removal_evidence=self._optional_evidence(row, "removal_evidence"),
            )
            self._validate_proof_shape(row, unit.state)
            return unit
        except TypeError, ValueError:
            _inventory_fail()

    def _proof_from_row(self, row: Sequence[Any]) -> TerminationProof:
        unit = self._unit_from_row(row)
        if unit.state is not ManagedUnitState.REMOVED:
            _inventory_fail()
        exit_evidence = unit.exit_evidence
        empty_evidence = unit.empty_evidence
        removal_evidence = unit.removal_evidence
        if not isinstance(exit_evidence, EvidenceDigest):
            _inventory_fail()
        if not isinstance(empty_evidence, EvidenceDigest):
            _inventory_fail()
        if not isinstance(removal_evidence, EvidenceDigest):
            _inventory_fail()
        try:
            return TerminationProof(
                proof_id=UUID(row[_UNIT_COLUMNS.index("proof_id")]),
                attempt_id=unit.attempt_id,
                unit_id=unit.unit_id,
                principal=unit.principal,
                policy_revision=unit.policy_revision,
                exit_evidence=exit_evidence,
                empty_evidence=empty_evidence,
                removal_evidence=removal_evidence,
            )
        except TypeError, ValueError:
            _inventory_fail()

    @staticmethod
    def _validate_proof_shape(row: Sequence[Any], state: ManagedUnitState) -> None:
        proof_id = row[_UNIT_COLUMNS.index("proof_id")]
        if (state is ManagedUnitState.REMOVED) != (proof_id is not None):
            _inventory_fail()
        if proof_id is not None:
            UUID(proof_id)

    @staticmethod
    def _validate_transition_payload(
        target: ManagedUnitState,
        evidence: EvidenceDigest | None,
        runtime_incarnation: RuntimeIncarnation | None,
    ) -> None:
        requires_incarnation = target is ManagedUnitState.CREATED
        requires_evidence = target in {
            ManagedUnitState.EXIT_CONFIRMED,
            ManagedUnitState.EMPTY_CONFIRMED,
        }
        if requires_incarnation != (type(runtime_incarnation) is RuntimeIncarnation):
            _fail()
        if requires_evidence != (type(evidence) is EvidenceDigest):
            _fail()

    @staticmethod
    def _evidence_column(target: ManagedUnitState) -> str | None:
        return {
            ManagedUnitState.EXIT_CONFIRMED: "exit_evidence",
            ManagedUnitState.EMPTY_CONFIRMED: "empty_evidence",
        }.get(target)

    def _store_principal(
        self, connection: sqlite3.Connection, principal_id: str, high_water: int
    ) -> None:
        values = (principal_id, high_water, _MAC_VERSION)
        connection.execute(
            "INSERT INTO principals (principal_id, high_water, mac_version, mac) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(principal_id) DO UPDATE SET "
            "high_water = excluded.high_water, "
            "mac_version = excluded.mac_version, mac = excluded.mac",
            (*values, self._mac("principal", values)),
        )

    def _verified_acknowledgement(self, row: Sequence[Any]) -> None:
        values = tuple(row[:-1])
        if (
            len(row) != len(_ACKNOWLEDGEMENT_COLUMNS)
            or row[_ACKNOWLEDGEMENT_COLUMNS.index("mac_version")] != _MAC_VERSION
            or type(row[-1]) is not bytes
            or not hmac.compare_digest(row[-1], self._mac("acknowledgement", values))
            or type(row[_ACKNOWLEDGEMENT_COLUMNS.index("create_sequence")]) is not int
            or row[_ACKNOWLEDGEMENT_COLUMNS.index("create_sequence")] <= 0
        ):
            _inventory_fail()
        try:
            UUID(row[_ACKNOWLEDGEMENT_COLUMNS.index("proof_id")])
            UUID(row[_ACKNOWLEDGEMENT_COLUMNS.index("principal_id")])
            UUID(row[_ACKNOWLEDGEMENT_COLUMNS.index("attempt_id")])
            UUID(row[_ACKNOWLEDGEMENT_COLUMNS.index("unit_id")])
        except TypeError, ValueError:
            _inventory_fail()

    @staticmethod
    def _acknowledgement_identity(
        row: Sequence[Any],
    ) -> tuple[str, str, str, str]:
        principal_id = row[_ACKNOWLEDGEMENT_COLUMNS.index("principal_id")]
        attempt_id = row[_ACKNOWLEDGEMENT_COLUMNS.index("attempt_id")]
        unit_id = row[_ACKNOWLEDGEMENT_COLUMNS.index("unit_id")]
        proof_id = row[_ACKNOWLEDGEMENT_COLUMNS.index("proof_id")]
        if not all(
            type(value) is str
            for value in (principal_id, attempt_id, unit_id, proof_id)
        ):
            _inventory_fail()
        return principal_id, attempt_id, unit_id, proof_id

    def _write_updated_unit(
        self,
        connection: sqlite3.Connection,
        updated: list[Any],
        unit_id: UUID,
        expected_revision: int,
    ) -> ManagedUnit:
        values = tuple(updated)
        cursor = connection.execute(
            _UPDATE_UNIT,
            (*values[1:], self._mac("unit", values), str(unit_id), expected_revision),
        )
        if cursor.rowcount != 1:
            _fail(BrokerErrorCategory.REPLAY_REJECTED)
        self._refresh_manifest(connection)
        row = self._select_unit(connection, "unit_id", str(unit_id))
        if row is None:
            _inventory_fail()
        return self._unit_from_row(row)

    def _insert_empty_manifest(self, connection: sqlite3.Connection) -> None:
        values = (
            1,
            0,
            0,
            0,
            0,
            self._aggregate_digest("principals", ()),
            self._aggregate_digest("units", ()),
            self._aggregate_digest("acknowledgements", ()),
            _MAC_VERSION,
        )
        connection.execute(
            _INSERT_MANIFEST,
            (*values, self._mac("manifest", values)),
        )

    def _refresh_manifest(self, connection: sqlite3.Connection) -> None:
        current = connection.execute(_SELECT_MANIFEST).fetchall()
        if len(current) != 1:
            _inventory_fail()
        self._verified_manifest_row(current[0])
        principals = connection.execute(
            _SELECT_PRINCIPALS + " ORDER BY principal_id"
        ).fetchall()
        units = connection.execute(_SELECT_UNITS + " ORDER BY unit_id").fetchall()
        acknowledgements = connection.execute(
            _SELECT_ACKNOWLEDGEMENTS + " ORDER BY proof_id"
        ).fetchall()
        values = (
            1,
            current[0][_MANIFEST_COLUMNS.index("generation")] + 1,
            len(principals),
            len(units),
            len(acknowledgements),
            self._aggregate_digest("principals", principals),
            self._aggregate_digest("units", units),
            self._aggregate_digest("acknowledgements", acknowledgements),
            _MAC_VERSION,
        )
        cursor = connection.execute(
            "UPDATE inventory_manifest SET generation = ?, principal_count = ?, "
            "unit_count = ?, acknowledgement_count = ?, principals_digest = ?, "
            "units_digest = ?, acknowledgements_digest = ?, mac_version = ?, "
            "mac = ? WHERE singleton_id = 1",
            (*values[1:], self._mac("manifest", values)),
        )
        if cursor.rowcount != 1:
            _inventory_fail()

    def _verify_manifest(
        self,
        connection: sqlite3.Connection,
        principals: Sequence[Sequence[Any]],
        units: Sequence[Sequence[Any]],
        acknowledgements: Sequence[Sequence[Any]],
    ) -> None:
        rows = connection.execute(_SELECT_MANIFEST).fetchall()
        if len(rows) != 1:
            _inventory_fail()
        row = rows[0]
        self._verified_manifest_row(row)
        if (
            row[_MANIFEST_COLUMNS.index("principal_count")] != len(principals)
            or row[_MANIFEST_COLUMNS.index("unit_count")] != len(units)
            or row[_MANIFEST_COLUMNS.index("acknowledgement_count")]
            != len(acknowledgements)
            or row[_MANIFEST_COLUMNS.index("principals_digest")]
            != self._aggregate_digest("principals", principals)
            or row[_MANIFEST_COLUMNS.index("units_digest")]
            != self._aggregate_digest("units", units)
            or row[_MANIFEST_COLUMNS.index("acknowledgements_digest")]
            != self._aggregate_digest("acknowledgements", acknowledgements)
        ):
            _inventory_fail()

    def _verified_manifest_row(self, row: Sequence[Any]) -> None:
        values = tuple(row[:-1])
        if (
            len(row) != len(_MANIFEST_COLUMNS)
            or row[0] != 1
            or type(row[1]) is not int
            or row[1] < 0
            or any(type(value) is not int or value < 0 for value in row[2:5])
            or not all(type(value) is str for value in row[5:8])
            or row[8] != _MAC_VERSION
            or type(row[9]) is not bytes
            or not hmac.compare_digest(row[9], self._mac("manifest", values))
        ):
            _inventory_fail()

    @staticmethod
    def _aggregate_digest(record_type: str, rows: Sequence[Sequence[Any]]) -> str:
        digest = hashlib.sha256()
        for row in rows:
            serializable = [
                {"bytes": value.hex()} if type(value) is bytes else value
                for value in row
            ]
            canonical = json.dumps(
                [record_type, *serializable],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
            digest.update(len(canonical).to_bytes(8, "big"))
            digest.update(canonical)
        return "sha256:" + digest.hexdigest()

    def _select_unit(
        self, connection: sqlite3.Connection, column: str, value: str
    ) -> sqlite3.Row | None:
        statement = _SELECT_UNIT_BY.get(column)
        if statement is None:
            _fail()
        return connection.execute(statement, (value,)).fetchone()

    @staticmethod
    def _optional_evidence(row: Sequence[Any], column: str) -> EvidenceDigest | None:
        value = row[_UNIT_COLUMNS.index(column)]
        return None if value is None else EvidenceDigest(value)

    def _mac(self, record_type: str, values: Sequence[Any]) -> bytes:
        canonical = json.dumps(
            [_SCHEMA_VERSION, _MAC_VERSION, record_type, *values],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hmac.new(self._key, canonical, hashlib.sha256).digest()

    @staticmethod
    def _runtime_name(unit_id: UUID) -> str:
        return f"markweave-reverse-{unit_id.hex}"

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    @staticmethod
    def _schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
        return {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_autoindex_%'"
            )
        }

    @staticmethod
    def _scalar(connection: sqlite3.Connection, statement: str) -> int:
        value = connection.execute(statement).fetchone()[0]
        if type(value) is not int:
            _fail()
        return value
