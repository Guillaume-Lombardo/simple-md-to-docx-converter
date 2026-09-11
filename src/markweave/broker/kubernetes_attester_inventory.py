"""Bounded authenticated lifecycle ledger for the Kubernetes node attester."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from markweave.broker.kubernetes_runtime import KubernetesRuntimeError
from markweave.broker.models import MAX_RECOVERY_BINDING_BYTES, EvidenceDigest

_APPLICATION_ID = 0x4D57414C  # MWAL
_SCHEMA_VERSION = 1
_MAC_VERSION = 1
_MAX_PAYLOAD_BYTES = MAX_RECOVERY_BINDING_BYTES
_COLUMNS = (
    "pod_uid",
    "state",
    "binding_payload",
    "sandbox_payload",
    "exit_evidence",
    "empty_evidence",
    "removed_evidence",
    "revision",
    "mac_version",
    "mac",
)
_SELECT = (
    "SELECT " + ", ".join(_COLUMNS) + " FROM lifecycle"  # noqa: S608
)
_MINIMUM_KEY_BYTES = 32
_CREATE_LIFECYCLE = (
    "CREATE TABLE lifecycle ("
    "pod_uid TEXT PRIMARY KEY NOT NULL CHECK (typeof(pod_uid) = 'text'),"
    "state TEXT NOT NULL CHECK (typeof(state) = 'text'),"
    "binding_payload BLOB NOT NULL CHECK (typeof(binding_payload) = 'blob'),"
    "sandbox_payload BLOB NOT NULL CHECK (typeof(sandbox_payload) = 'blob'),"
    "exit_evidence TEXT CHECK (exit_evidence IS NULL OR typeof(exit_evidence) = 'text'),"
    "empty_evidence TEXT CHECK (empty_evidence IS NULL OR typeof(empty_evidence) = 'text'),"
    "removed_evidence TEXT CHECK (removed_evidence IS NULL OR typeof(removed_evidence) = 'text'),"
    "revision INTEGER NOT NULL CHECK (revision >= 0 AND typeof(revision) = 'integer'),"
    "mac_version INTEGER NOT NULL CHECK (typeof(mac_version) = 'integer'),"
    "mac BLOB NOT NULL CHECK (typeof(mac) = 'blob')"
    ")"
)
_CREATE_MANIFEST = (
    "CREATE TABLE lifecycle_manifest ("
    "singleton_id INTEGER PRIMARY KEY NOT NULL "
    "CHECK (singleton_id = 1 AND typeof(singleton_id) = 'integer'),"
    "generation INTEGER NOT NULL "
    "CHECK (generation >= 0 AND typeof(generation) = 'integer'),"
    "record_count INTEGER NOT NULL "
    "CHECK (record_count >= 0 AND typeof(record_count) = 'integer'),"
    "records_digest TEXT NOT NULL CHECK (typeof(records_digest) = 'text'),"
    "mac_version INTEGER NOT NULL CHECK (typeof(mac_version) = 'integer'),"
    "mac BLOB NOT NULL CHECK (typeof(mac) = 'blob')"
    ")"
)
_LEGACY_STRICT_SCHEMAS = {
    "lifecycle": (
        "CREATE TABLE lifecycle ("
        "pod_uid TEXT PRIMARY KEY NOT NULL,"
        "state TEXT NOT NULL,"
        "binding_payload BLOB NOT NULL,"
        "sandbox_payload BLOB NOT NULL,"
        "exit_evidence TEXT,"
        "empty_evidence TEXT,"
        "removed_evidence TEXT,"
        "revision INTEGER NOT NULL CHECK (revision >= 0),"
        "mac_version INTEGER NOT NULL,"
        "mac BLOB NOT NULL"
        ") STRICT"
    ),
    "lifecycle_manifest": (
        "CREATE TABLE lifecycle_manifest ("
        "singleton_id INTEGER PRIMARY KEY NOT NULL CHECK (singleton_id = 1),"
        "generation INTEGER NOT NULL CHECK (generation >= 0),"
        "record_count INTEGER NOT NULL CHECK (record_count >= 0),"
        "records_digest TEXT NOT NULL,"
        "mac_version INTEGER NOT NULL,"
        "mac BLOB NOT NULL"
        ") STRICT"
    ),
}


class AttesterLifecycleState(StrEnum):
    BOUND = "bound"
    EXIT = "exit"
    EMPTY = "empty"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class AttesterLifecycleRecord:
    pod_uid: UUID
    state: AttesterLifecycleState
    binding_payload: bytes
    sandbox_payload: bytes
    exit_evidence: EvidenceDigest | None = None
    empty_evidence: EvidenceDigest | None = None
    removed_evidence: EvidenceDigest | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        required = {
            AttesterLifecycleState.BOUND: 0,
            AttesterLifecycleState.EXIT: 1,
            AttesterLifecycleState.EMPTY: 2,
            AttesterLifecycleState.REMOVED: 3,
        }[self.state]
        evidence = (self.exit_evidence, self.empty_evidence, self.removed_evidence)
        if (
            type(self.pod_uid) is not UUID
            or type(self.state) is not AttesterLifecycleState
            or any(
                type(value) is not bytes
                or not value
                or len(value) > _MAX_PAYLOAD_BYTES
                or not value.isascii()
                for value in (self.binding_payload, self.sandbox_payload)
            )
            or any(
                (index < required and type(value) is not EvidenceDigest)
                or (index >= required and value is not None)
                for index, value in enumerate(evidence)
            )
            or type(self.revision) is not int
            or self.revision != required
        ):
            raise ValueError("Kubernetes attester lifecycle record is invalid")


class SQLiteNodeAttesterLedger:
    """Fail-closed HMAC-authenticated SQLite lifecycle ledger."""

    def __init__(self, path: Path, authentication_key: bytes, *, max_records: int):
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or type(authentication_key) is not bytes
            or len(authentication_key) < _MINIMUM_KEY_BYTES
            or type(max_records) is not int
            or max_records <= 0
        ):
            raise ValueError("Kubernetes attester ledger configuration is invalid")
        self._path = path
        self._key = authentication_key
        self._max_records = max_records
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                if not self._tables(connection):
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(_CREATE_LIFECYCLE)
                    connection.execute(_CREATE_MANIFEST)
                    values = (1, 0, 0, self._aggregate(()), _MAC_VERSION)
                    connection.execute(
                        "INSERT INTO lifecycle_manifest VALUES (?, ?, ?, ?, ?, ?)",
                        (*values, self._mac("manifest", values)),
                    )
                    connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                    connection.commit()
                self._verify_all(connection)
        except OSError, sqlite3.DatabaseError, ValueError:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed") from None

    def records(self) -> tuple[AttesterLifecycleRecord, ...]:
        with self._verified() as connection:
            return tuple(
                self._record(row)
                for row in connection.execute(_SELECT + " ORDER BY pod_uid")
            )

    def get(self, pod_uid: UUID) -> AttesterLifecycleRecord | None:
        if type(pod_uid) is not UUID:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        with self._verified() as connection:
            row = connection.execute(
                _SELECT + " WHERE pod_uid = ?", (str(pod_uid),)
            ).fetchone()
            return None if row is None else self._record(row)

    def reserve(self, record: AttesterLifecycleRecord) -> AttesterLifecycleRecord:
        if type(record) is not AttesterLifecycleRecord:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        with self._transaction() as connection:
            self._verify_all(connection)
            existing = connection.execute(
                _SELECT + " WHERE pod_uid = ?", (str(record.pod_uid),)
            ).fetchone()
            if existing is not None:
                current = self._record(existing)
                if current != record:
                    raise KubernetesRuntimeError(
                        "Kubernetes attester binding conflicts"
                    )
                return current
            if (
                connection.execute("SELECT COUNT(*) FROM lifecycle").fetchone()[0]
                >= self._max_records
            ):
                raise KubernetesRuntimeError("Kubernetes attester ledger is full")
            values = self._values(record)
            connection.execute(
                "INSERT INTO lifecycle VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, self._mac("record", values)),
            )
            self._refresh(connection)
            return record

    def transition(
        self,
        pod_uid: UUID,
        *,
        expected_revision: int,
        target: AttesterLifecycleState,
        evidence: EvidenceDigest,
    ) -> AttesterLifecycleRecord:
        with self._transaction() as connection:
            self._verify_all(connection)
            row = connection.execute(
                _SELECT + " WHERE pod_uid = ?", (str(pod_uid),)
            ).fetchone()
            if row is None:
                raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
            current = self._record(row)
            successor = {
                AttesterLifecycleState.BOUND: AttesterLifecycleState.EXIT,
                AttesterLifecycleState.EXIT: AttesterLifecycleState.EMPTY,
                AttesterLifecycleState.EMPTY: AttesterLifecycleState.REMOVED,
            }.get(current.state)
            if (
                successor is not target
                or current.revision != expected_revision
                or type(evidence) is not EvidenceDigest
            ):
                raise KubernetesRuntimeError("Kubernetes attester lifecycle conflicts")
            updated = AttesterLifecycleRecord(
                current.pod_uid,
                target,
                current.binding_payload,
                current.sandbox_payload,
                evidence
                if target is AttesterLifecycleState.EXIT
                else current.exit_evidence,
                evidence
                if target is AttesterLifecycleState.EMPTY
                else current.empty_evidence,
                evidence if target is AttesterLifecycleState.REMOVED else None,
                current.revision + 1,
            )
            values = self._values(updated)
            cursor = connection.execute(
                "UPDATE lifecycle SET state=?, binding_payload=?, sandbox_payload=?, "
                "exit_evidence=?, empty_evidence=?, removed_evidence=?, revision=?, "
                "mac_version=?, mac=? WHERE pod_uid=? AND revision=?",
                (
                    *values[1:],
                    self._mac("record", values),
                    str(pod_uid),
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise KubernetesRuntimeError("Kubernetes attester lifecycle conflicts")
            self._refresh(connection)
            return updated

    def discard_removed(self, pod_uid: UUID, removed: EvidenceDigest) -> bool:
        with self._transaction() as connection:
            self._verify_all(connection)
            row = connection.execute(
                _SELECT + " WHERE pod_uid = ?", (str(pod_uid),)
            ).fetchone()
            if row is None:
                return True
            record = self._record(row)
            if (
                record.state is not AttesterLifecycleState.REMOVED
                or record.removed_evidence != removed
            ):
                return False
            connection.execute(
                "DELETE FROM lifecycle WHERE pod_uid = ?", (str(pod_uid),)
            )
            self._refresh(connection)
            return True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            if connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal":
                raise KubernetesRuntimeError("Kubernetes attester ledger failed")
            connection.execute("PRAGMA synchronous = FULL")
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
            raise KubernetesRuntimeError("Kubernetes attester ledger failed") from None

    @contextmanager
    def _verified(self) -> Iterator[sqlite3.Connection]:
        try:
            with self._connect() as connection:
                self._verify_all(connection)
                yield connection
        except sqlite3.DatabaseError, ValueError:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed") from None

    def _verify_all(self, connection: sqlite3.Connection) -> None:
        if (
            connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0]
            != _SCHEMA_VERSION
            or self._tables(connection) != {"lifecycle", "lifecycle_manifest"}
        ):
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        schemas = dict(
            connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name IN ('lifecycle', 'lifecycle_manifest')"
            )
        )
        current_schemas = {
            "lifecycle": _CREATE_LIFECYCLE,
            "lifecycle_manifest": _CREATE_MANIFEST,
        }
        if schemas not in (current_schemas, _LEGACY_STRICT_SCHEMAS):
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        rows = connection.execute(_SELECT + " ORDER BY pod_uid").fetchall()
        if len(rows) > self._max_records:
            raise KubernetesRuntimeError("Kubernetes attester ledger is full")
        for row in rows:
            self._record(row)
        manifests = connection.execute(
            "SELECT singleton_id,generation,record_count,records_digest,mac_version,mac "
            "FROM lifecycle_manifest"
        ).fetchall()
        if len(manifests) != 1:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        manifest = manifests[0]
        values = tuple(manifest[:-1])
        if (
            manifest[0] != 1
            or manifest[2] != len(rows)
            or manifest[3] != self._aggregate(rows)
            or manifest[4] != _MAC_VERSION
            or type(manifest[5]) is not bytes
            or not hmac.compare_digest(manifest[5], self._mac("manifest", values))
        ):
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")

    def _record(self, row: Sequence[object]) -> AttesterLifecycleRecord:
        values = tuple(row[:-1])
        if (
            len(row) != len(_COLUMNS)
            or row[-2] != _MAC_VERSION
            or type(row[2]) is not bytes
            or type(row[3]) is not bytes
            or type(row[7]) is not int
            or type(row[-1]) is not bytes
            or not hmac.compare_digest(row[-1], self._mac("record", values))
        ):
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        try:
            return AttesterLifecycleRecord(
                UUID(str(row[0])),
                AttesterLifecycleState(str(row[1])),
                row[2],
                row[3],
                None if row[4] is None else EvidenceDigest(str(row[4])),
                None if row[5] is None else EvidenceDigest(str(row[5])),
                None if row[6] is None else EvidenceDigest(str(row[6])),
                row[7],
            )
        except TypeError, ValueError:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed") from None

    @staticmethod
    def _values(record: AttesterLifecycleRecord) -> tuple[object, ...]:
        return (
            str(record.pod_uid),
            record.state.value,
            record.binding_payload,
            record.sandbox_payload,
            None if record.exit_evidence is None else record.exit_evidence.value,
            None if record.empty_evidence is None else record.empty_evidence.value,
            None if record.removed_evidence is None else record.removed_evidence.value,
            record.revision,
            _MAC_VERSION,
        )

    def _refresh(self, connection: sqlite3.Connection) -> None:
        current = connection.execute(
            "SELECT singleton_id,generation,record_count,records_digest,mac_version,mac "
            "FROM lifecycle_manifest"
        ).fetchone()
        if current is None:
            raise KubernetesRuntimeError("Kubernetes attester ledger failed")
        rows = connection.execute(_SELECT + " ORDER BY pod_uid").fetchall()
        values = (1, current[1] + 1, len(rows), self._aggregate(rows), _MAC_VERSION)
        connection.execute(
            "UPDATE lifecycle_manifest SET generation=?,record_count=?,records_digest=?,"
            "mac_version=?,mac=? WHERE singleton_id=1",
            (*values[1:], self._mac("manifest", values)),
        )

    def _mac(self, kind: str, values: Sequence[object]) -> bytes:
        serializable = [
            {"bytes": value.hex()} if type(value) is bytes else value
            for value in values
        ]
        payload = json.dumps(
            [_SCHEMA_VERSION, _MAC_VERSION, kind, *serializable],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hmac.new(self._key, payload, hashlib.sha256).digest()

    @staticmethod
    def _aggregate(rows: Sequence[Sequence[object]]) -> str:
        digest = hashlib.sha256()
        for row in rows:
            serializable = [
                {"bytes": value.hex()} if type(value) is bytes else value
                for value in row
            ]
            payload = json.dumps(
                serializable, ensure_ascii=True, separators=(",", ":")
            ).encode("ascii")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _tables(connection: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
