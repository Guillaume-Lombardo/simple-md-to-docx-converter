"""Typed standalone, PostgreSQL, and S3 recovery adapters."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from pathlib import Path, PurePosixPath
from time import monotonic
from typing import Any, Protocol, cast
from uuid import UUID

from sqlalchemy import Connection, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import Base
from markweave.persistence.sql import create_database_engine
from markweave.recovery_manifest import (
    RecoveryError,
    RecoveryMember,
    canonical_json,
    sha256_file,
)
from markweave.storage import ObjectScope

RECOVERY_ADVISORY_LOCK = 1_297_337_037
DATABASE_PAYLOAD = "database/metadata.json"
SQLITE_PAYLOAD = "database/metadata.sqlite3"
_SCOPES = frozenset(scope.value for scope in ObjectScope)
OBJECT_KEY_PARTS = 3
_S3_DEPENDENCY_ROOTS = frozenset(("boto3", "botocore"))


class _Boto3Module(Protocol):
    def client(self, service_name: str, **options: Any) -> Any: ...


class _ConfigFactory(Protocol):
    def __call__(self, **options: Any) -> Any: ...


def _load_s3_dependencies() -> tuple[
    _Boto3Module,
    _ConfigFactory,
    type[BaseException],
    type[BaseException],
]:
    """Load the distributed object-store SDK only when S3 recovery is selected."""
    try:
        boto3 = import_module("boto3")
        config = import_module("botocore.config")
        exceptions = import_module("botocore.exceptions")
    except ModuleNotFoundError as error:
        missing_root = error.name.split(".", 1)[0] if error.name is not None else None
        if missing_root in _S3_DEPENDENCY_ROOTS:
            raise RecoveryError(
                "S3 recovery requires the 'distributed' extra; "
                "install 'markweave[distributed]'."
            ) from None
        raise
    return (
        cast(_Boto3Module, boto3),
        cast(_ConfigFactory, config.Config),
        cast(type[BaseException], exceptions.BotoCoreError),
        cast(type[BaseException], exceptions.ClientError),
    )


@dataclass(frozen=True, slots=True)
class RecoveryDeadline:
    """Monotonic operation budget checked at every bounded boundary."""

    expires_at: float

    @classmethod
    def after(cls, timeout_seconds: float) -> RecoveryDeadline:
        if timeout_seconds <= 0:
            raise RecoveryError("Recovery timeout is invalid")
        return cls(monotonic() + timeout_seconds)

    def remaining(self) -> float:
        remaining = self.expires_at - monotonic()
        if remaining <= 0:
            raise RecoveryError("Recovery operation timed out")
        return remaining


@dataclass(frozen=True, slots=True)
class S3Configuration:
    """Explicit provider-neutral AWS S3-compatible configuration."""

    bucket: str
    endpoint_url: str | None
    region: str | None
    access_key_id: str | None = None
    secret_access_key: str | None = None

    def __repr__(self) -> str:
        return (
            f"S3Configuration(bucket={self.bucket!r}, "
            f"endpoint_url={self.endpoint_url!r}, region={self.region!r}, "
            "access_key_id=<redacted>, secret_access_key=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class AdapterBackup:
    """Adapter-produced identity and payload members."""

    identity: str
    source_identity: str
    members: tuple[RecoveryMember, ...]


class StandaloneRecoveryAdapter:
    """SQLite online snapshots plus stable immutable filesystem objects."""

    def backup(
        self, data_directory: Path, staging: Path, deadline: RecoveryDeadline
    ) -> tuple[AdapterBackup, AdapterBackup]:
        source = _safe_absolute_directory(data_directory, "Standalone data directory")
        database = source / "metadata.sqlite3"
        objects = source / "objects"
        if database.is_symlink() or not database.is_file():
            raise RecoveryError("Standalone database is unavailable")
        if objects.is_symlink() or not objects.is_dir():
            raise RecoveryError("Standalone object tree is unavailable")
        with filesystem_lock(source / ".markweave-recovery.lock"):
            database_target = staging / SQLITE_PAYLOAD
            database_target.parent.mkdir(mode=0o700, parents=True)
            self._sqlite_snapshot(database, database_target, deadline)
            db_size, db_digest = sha256_file(database_target)
            database_member = RecoveryMember(SQLITE_PAYLOAD, db_size, db_digest)
            before = tuple(self._objects(objects, deadline))
            object_members: list[RecoveryMember] = []
            for relative, source_path, size, digest in before:
                deadline.remaining()
                destination = staging / "objects" / relative
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _copy_regular(source_path, destination)
                copied_size, copied_digest = sha256_file(destination)
                if (copied_size, copied_digest) != (size, digest):
                    raise RecoveryError("Standalone object changed during backup")
                object_members.append(
                    RecoveryMember(f"objects/{relative}", size, digest)
                )
            after = tuple(self._objects(objects, deadline))
            if tuple((item[0], item[2], item[3]) for item in before) != tuple(
                (item[0], item[2], item[3]) for item in after
            ):
                raise RecoveryError("Standalone object tree changed during backup")
        object_identity = hashlib.sha256(
            canonical_json(
                [[member.path, member.size, member.sha256] for member in object_members]
            )
        ).hexdigest()
        source_identity = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
        return (
            AdapterBackup(db_digest, source_identity, (database_member,)),
            AdapterBackup(object_identity, source_identity, tuple(object_members)),
        )

    def restore(
        self,
        source: Path,
        target: Path,
        members: tuple[RecoveryMember, ...],
        deadline: RecoveryDeadline,
    ) -> str:
        _safe_new_destination(target, "Standalone restore destination")
        stage = target.with_name(f".{target.name}.restore-{os.getpid()}")
        if stage.exists() or stage.is_symlink():
            raise RecoveryError("Standalone restore staging path is not empty")
        stage.mkdir(mode=0o700)
        try:
            for member in members:
                deadline.remaining()
                if member.path != SQLITE_PAYLOAD and not member.path.startswith(
                    "objects/"
                ):
                    continue
                destination = (
                    stage / member.path
                    if member.path.startswith("objects/")
                    else stage / "metadata.sqlite3"
                )
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _copy_regular(source / member.path, destination)
            database = stage / "metadata.sqlite3"
            self._verify_sqlite(database)
            self._verify_stable_references_sqlite(database, stage / "objects")
            engine = create_database_engine(f"sqlite+pysqlite:///{database}")
            try:
                upgrade_database(engine)
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
            finally:
                engine.dispose()
            deadline.remaining()
            os.replace(stage, target)
            _sync_directory(target.parent)
            return hashlib.sha256(
                f"{target.resolve()}:{sha256_file(target / 'metadata.sqlite3')[1]}".encode()
            ).hexdigest()
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    @staticmethod
    def _sqlite_snapshot(
        source: Path, target: Path, deadline: RecoveryDeadline
    ) -> None:
        try:
            with (
                closing(sqlite3.connect(source)) as database,
                closing(sqlite3.connect(target)) as snapshot,
            ):
                database.backup(
                    snapshot, pages=256, sleep=min(0.05, deadline.remaining())
                )
            StandaloneRecoveryAdapter._verify_sqlite(target)
        except OSError, sqlite3.Error:
            raise RecoveryError("SQLite online snapshot failed") from None

    @staticmethod
    def _verify_sqlite(database: Path) -> None:
        try:
            with closing(
                sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            ) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RecoveryError("SQLite snapshot integrity check failed")
        except sqlite3.Error:
            raise RecoveryError("SQLite snapshot integrity check failed") from None

    @staticmethod
    def _objects(
        root: Path, deadline: RecoveryDeadline
    ) -> Iterator[tuple[str, Path, int, str]]:
        observed: list[tuple[str, Path, int, str]] = []
        for directory, names, files in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            for name in names:
                if (directory_path / name).is_symlink():
                    raise RecoveryError("Standalone object tree contains a symlink")
            for name in files:
                deadline.remaining()
                path = directory_path / name
                relative = path.relative_to(root).as_posix()
                _validate_object_key(relative)
                size, digest = sha256_file(path)
                observed.append((relative, path, size, digest))
        yield from sorted(observed)

    @staticmethod
    def _verify_stable_references_sqlite(database: Path, objects: Path) -> None:
        try:
            with closing(
                sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            ) as connection:
                _verify_database_references(connection.execute, objects)
        except sqlite3.Error:
            raise RecoveryError(
                "Restored stable object references are invalid"
            ) from None


class PostgreSQLRecoveryAdapter:
    """Typed logical PostgreSQL snapshots without operator shell commands."""

    def backup(
        self, database_url: str, staging: Path, deadline: RecoveryDeadline
    ) -> AdapterBackup:
        engine = create_database_engine(
            database_url, timeout_seconds=deadline.remaining()
        )
        target = staging / DATABASE_PAYLOAD
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with (
                engine.connect().execution_options(
                    isolation_level="REPEATABLE READ"
                ) as connection,
                connection.begin(),
            ):
                self._lock(connection)
                resource = connection.execute(
                    text(
                        "SELECT current_database(), current_schema(), "
                        "COALESCE(inet_server_addr()::text, 'local'), inet_server_port()"
                    )
                ).one()
                snapshot = connection.execute(
                    text(
                        "SELECT pg_current_wal_lsn()::text, txid_current_snapshot()::text"
                    )
                ).one()
                tables: list[dict[str, Any]] = []
                for table in Base.metadata.sorted_tables:
                    deadline.remaining()
                    rows = connection.execute(select(table)).mappings()
                    tables.append(
                        {
                            "name": table.name,
                            "columns": [column.name for column in table.columns],
                            "rows": [
                                {key: _encode(value) for key, value in row.items()}
                                for row in rows
                            ],
                        }
                    )
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                payload = {
                    "schema": "markweave-postgresql-logical-v1",
                    "revision": revision,
                    "tables": tables,
                }
                _write_private(target, canonical_json(payload) + b"\n")
                identity = hashlib.sha256(
                    canonical_json(
                        {
                            "wal": snapshot[0],
                            "transaction": snapshot[1],
                            "payload": sha256_file(target)[1],
                        }
                    )
                ).hexdigest()
                source_identity = hashlib.sha256(
                    canonical_json(list(resource))
                ).hexdigest()
        except SQLAlchemyError, OSError, TypeError:
            raise RecoveryError("PostgreSQL backup failed") from None
        finally:
            engine.dispose()
        size, digest = sha256_file(target)
        return AdapterBackup(
            identity, source_identity, (RecoveryMember(DATABASE_PAYLOAD, size, digest),)
        )

    def restore(
        self,
        database_url: str,
        payload_path: Path,
        *,
        source_identity: str,
        object_keys: frozenset[str],
        deadline: RecoveryDeadline,
    ) -> str:
        payload = _load_database_payload(payload_path)
        self._verify_payload_references(payload, object_keys)
        engine = create_database_engine(
            database_url, timeout_seconds=deadline.remaining()
        )
        try:
            with engine.connect() as connection:
                resource = connection.execute(
                    text(
                        "SELECT current_database(), current_schema(), "
                        "COALESCE(inet_server_addr()::text, 'local'), inet_server_port()"
                    )
                ).one()
                target_identity = hashlib.sha256(
                    canonical_json(list(resource))
                ).hexdigest()
                if target_identity == source_identity:
                    raise RecoveryError("Distributed restore target is not isolated")
                existing = set(inspect(connection).get_table_names())
                migrated = set(Base.metadata.tables) | {
                    "alembic_version",
                    "audit_cleanup_guards",
                }
                if existing and existing != migrated:
                    raise RecoveryError("Distributed database target is not empty")
                for table_name in existing - {"alembic_version"}:
                    count_statement = text(
                        f'SELECT count(*) FROM "{table_name}"'  # noqa: S608 - inspected fixed schema names
                    )
                    if connection.scalar(count_statement):
                        raise RecoveryError("Distributed database target is not empty")
            if not existing:
                upgrade_database(engine)
            with engine.begin() as connection:
                self._lock(connection)
                tables_by_name = {
                    table.name: table for table in Base.metadata.tables.values()
                }
                payload_tables = payload["tables"]
                if {item["name"] for item in payload_tables} != set(tables_by_name):
                    raise RecoveryError("PostgreSQL backup schema is incompatible")
                for item in payload_tables:
                    deadline.remaining()
                    table = tables_by_name[item["name"]]
                    if item["columns"] != [column.name for column in table.columns]:
                        raise RecoveryError("PostgreSQL backup schema is incompatible")
                    rows = [
                        {key: _decode(value) for key, value in row.items()}
                        for row in item["rows"]
                    ]
                    if rows:
                        connection.execute(table.insert(), rows)
            return target_identity
        except RecoveryError:
            raise
        except SQLAlchemyError:
            raise RecoveryError("PostgreSQL restore failed") from None
        finally:
            engine.dispose()

    @staticmethod
    def _lock(connection: Connection) -> None:
        locked = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock)"),
            {"lock": RECOVERY_ADVISORY_LOCK},
        )
        if locked is not True:
            raise RecoveryError("Another recovery operation is active")

    @staticmethod
    def _verify_payload_references(
        payload: Mapping[str, Any], object_keys: frozenset[str]
    ) -> None:
        tables = {item["name"]: item for item in payload["tables"]}
        expected = _referenced_keys_from_rows(tables)
        if not expected.issubset(object_keys):
            raise RecoveryError("Restored stable object references are incomplete")


class S3RecoveryAdapter:
    """Bounded AWS S3-compatible object snapshot and isolated restore adapter."""

    def __init__(
        self, configuration: S3Configuration, deadline: RecoveryDeadline
    ) -> None:
        if not configuration.bucket.strip():
            raise RecoveryError("S3 bucket is invalid")
        boto3, config_factory, boto_core_error, client_error = _load_s3_dependencies()
        options: dict[str, Any] = {
            "config": config_factory(
                connect_timeout=max(1, min(deadline.remaining(), 30)),
                read_timeout=max(1, min(deadline.remaining(), 30)),
                retries={"max_attempts": 1},
            )
        }
        if configuration.endpoint_url is not None:
            options["endpoint_url"] = configuration.endpoint_url
        if configuration.region is not None:
            options["region_name"] = configuration.region
        if configuration.access_key_id is not None:
            if configuration.secret_access_key is None:
                raise RecoveryError("S3 credentials are incomplete")
            options["aws_access_key_id"] = configuration.access_key_id
            options["aws_secret_access_key"] = configuration.secret_access_key
        self._client = boto3.client("s3", **options)
        self._configuration = configuration
        self._deadline = deadline
        self._boto_core_error = boto_core_error
        self._client_error = client_error
        self._provider_errors = (boto_core_error, client_error)

    def close(self) -> None:
        """Release the provider client's HTTP connection pools."""

        self._client.close()

    def backup(self, staging: Path) -> AdapterBackup:
        before = self._inventory()
        members: list[RecoveryMember] = []
        try:
            for key, size, _etag in before:
                self._deadline.remaining()
                response = self._client.get_object(
                    Bucket=self._configuration.bucket, Key=key
                )
                target = staging / "objects" / key
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                descriptor = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with (
                    os.fdopen(descriptor, "wb") as output,
                    closing(response["Body"]) as body,
                ):
                    while block := body.read(1024 * 1024):
                        self._deadline.remaining()
                        written += len(block)
                        digest.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                if written != size:
                    raise RecoveryError("S3 object changed during backup")
                members.append(
                    RecoveryMember(f"objects/{key}", written, digest.hexdigest())
                )
            if before != self._inventory():
                raise RecoveryError("S3 bucket changed during backup")
        except (*self._provider_errors, OSError):
            raise RecoveryError("S3 backup failed") from None
        identity = hashlib.sha256(canonical_json(before)).hexdigest()
        return AdapterBackup(identity, self._resource_identity(), tuple(members))

    def ensure_empty_and_restore(
        self,
        source: Path,
        members: tuple[RecoveryMember, ...],
        *,
        source_identity: str,
    ) -> tuple[str, frozenset[str]]:
        if self._resource_identity() == source_identity:
            raise RecoveryError("Distributed restore bucket is not isolated")
        if self._inventory():
            raise RecoveryError("Distributed bucket target is not empty")
        placed: list[str] = []
        try:
            for member in members:
                if not member.path.startswith("objects/"):
                    continue
                self._deadline.remaining()
                key = member.path.removeprefix("objects/")
                _validate_object_key(key)
                with (source / member.path).open("rb") as payload:
                    self._client.put_object(
                        Bucket=self._configuration.bucket,
                        Key=key,
                        Body=payload,
                        IfNoneMatch="*",
                    )
                placed.append(key)
                head = self._client.head_object(
                    Bucket=self._configuration.bucket, Key=key
                )
                if int(head["ContentLength"]) != member.size:
                    raise RecoveryError("S3 restored object integrity check failed")
                response = self._client.get_object(
                    Bucket=self._configuration.bucket, Key=key
                )
                digest = hashlib.sha256()
                with closing(response["Body"]) as body:
                    while block := body.read(1024 * 1024):
                        self._deadline.remaining()
                        digest.update(block)
                if digest.hexdigest() != member.sha256:
                    raise RecoveryError("S3 restored object integrity check failed")
            inventory = self._inventory()
            keys = frozenset(key for key, _, _ in inventory)
            if keys != frozenset(placed):
                raise RecoveryError("S3 restored object set is incomplete")
            return hashlib.sha256(canonical_json(inventory)).hexdigest(), keys
        except BaseException as error:
            for key in reversed(placed):
                with suppress(*self._provider_errors):
                    self._client.delete_object(
                        Bucket=self._configuration.bucket, Key=key
                    )
            if isinstance(error, (*self._provider_errors, OSError)):
                raise RecoveryError("S3 restore failed") from None
            raise

    def remove(self, keys: frozenset[str]) -> None:
        for key in keys:
            try:
                self._client.delete_object(Bucket=self._configuration.bucket, Key=key)
            except self._provider_errors:
                raise RecoveryError("Distributed restore cleanup failed") from None

    def _inventory(self) -> tuple[tuple[str, int, str], ...]:
        observed: list[tuple[str, int, str]] = []
        continuation: str | None = None
        try:
            while True:
                self._deadline.remaining()
                arguments: dict[str, Any] = {"Bucket": self._configuration.bucket}
                if continuation is not None:
                    arguments["ContinuationToken"] = continuation
                response = self._client.list_objects_v2(**arguments)
                for item in response.get("Contents", []):
                    key = item["Key"]
                    _validate_object_key(key)
                    observed.append((key, int(item["Size"]), str(item["ETag"])))
                if not response.get("IsTruncated"):
                    break
                continuation = response.get("NextContinuationToken")
                if not isinstance(continuation, str):
                    raise RecoveryError("S3 listing is incomplete")
        except (*self._provider_errors, KeyError, TypeError, ValueError):
            raise RecoveryError("S3 inventory failed") from None
        return tuple(sorted(observed))

    def _resource_identity(self) -> str:
        return hashlib.sha256(
            canonical_json(
                [
                    self._configuration.endpoint_url or "aws",
                    self._configuration.region or "provider-default",
                    self._configuration.bucket,
                ]
            )
        ).hexdigest()


@contextmanager
def filesystem_lock(path: Path) -> Iterator[None]:
    """Acquire a non-blocking symlink-safe local recovery lock."""

    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError:
        raise RecoveryError("Recovery lock could not be acquired") from None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryError("Another recovery operation is active") from None
        except OSError:
            raise RecoveryError("Recovery lock could not be acquired") from None
        yield
    finally:
        os.close(descriptor)


def _safe_absolute_directory(path: Path, label: str) -> Path:
    if (
        not path.is_absolute()
        or path.resolve() != path
        or path.is_symlink()
        or not path.is_dir()
    ):
        raise RecoveryError(f"{label} is unsafe")
    return path


def _safe_new_destination(path: Path, label: str) -> None:
    if (
        not path.is_absolute()
        or path.parent.resolve() != path.parent
        or path.is_symlink()
        or path.exists()
    ):
        raise RecoveryError(f"{label} must be an absent isolated path")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RecoveryError(f"{label} parent is unsafe")


def _copy_regular(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise RecoveryError("Recovery source contains an unsafe member")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with source.open("rb") as input_file, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise RecoveryError("Recovery member copy failed") from None


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_object_key(key: str) -> None:
    path = PurePosixPath(key)
    if (
        path.is_absolute()
        or len(path.parts) != OBJECT_KEY_PARTS
        or path.parts[0] not in _SCOPES
    ):
        raise RecoveryError("Stable object key is invalid")
    try:
        UUID(path.parts[1])
        UUID(path.parts[2])
    except ValueError:
        raise RecoveryError("Stable object key is invalid") from None


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise RecoveryError("PostgreSQL value type is unsupported")


def _decode(value: Any) -> Any:
    try:
        if isinstance(value, dict) and set(value) == {"$datetime"}:
            return datetime.fromisoformat(value["$datetime"])
        if isinstance(value, dict) and set(value) == {"$bytes"}:
            return base64.b64decode(value["$bytes"], validate=True)
    except TypeError, ValueError:
        raise RecoveryError("PostgreSQL backup value is invalid") from None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise RecoveryError("PostgreSQL backup value is invalid")


def _load_database_payload(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_bytes())
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema", "revision", "tables"}
            or raw["schema"] != "markweave-postgresql-logical-v1"
            or not isinstance(raw["revision"], str)
            or not isinstance(raw["tables"], list)
        ):
            raise RecoveryError("PostgreSQL backup payload is invalid")
        for table in raw["tables"]:
            if (
                not isinstance(table, dict)
                or set(table) != {"name", "columns", "rows"}
                or not isinstance(table["name"], str)
                or not isinstance(table["columns"], list)
                or not isinstance(table["rows"], list)
                or any(not isinstance(row, dict) for row in table["rows"])
            ):
                raise RecoveryError("PostgreSQL backup payload is invalid")
        return raw
    except OSError, UnicodeError, json.JSONDecodeError:
        raise RecoveryError("PostgreSQL backup payload is invalid") from None


def _referenced_keys_from_rows(tables: Mapping[str, Any]) -> frozenset[str]:
    keys: set[str] = set()
    versions = tables.get("template_versions", {}).get("rows", [])
    for row in versions:
        if row.get("publication_state") == "published":
            keys.add(f"template-versions/{row['object_owner_id']}/{row['id']}")
    jobs = tables.get("conversion_jobs", {}).get("rows", [])
    for row in jobs:
        owner = row["owner_id"]
        if row.get("source_ready"):
            keys.add(f"uploads/{owner}/{row['source_object_id']}")
        if row.get("result_object_id"):
            keys.add(f"results/{owner}/{row['result_object_id']}")
        if row.get("result_manifest_object_id"):
            keys.add(f"result-manifests/{owner}/{row['result_manifest_object_id']}")
    for key in keys:
        _validate_object_key(key)
    return frozenset(keys)


def _verify_database_references(execute: Any, objects: Path) -> None:
    rows: dict[str, dict[str, Any]] = {}
    try:
        rows["template_versions"] = {
            "rows": [
                dict(
                    zip(
                        ("id", "object_owner_id", "publication_state"), row, strict=True
                    )
                )
                for row in execute(
                    "SELECT id, object_owner_id, publication_state FROM template_versions"
                ).fetchall()
            ]
        }
        rows["conversion_jobs"] = {
            "rows": [
                dict(
                    zip(
                        (
                            "owner_id",
                            "source_object_id",
                            "source_ready",
                            "result_object_id",
                            "result_manifest_object_id",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in execute(
                    "SELECT owner_id, source_object_id, source_ready, result_object_id, "
                    "result_manifest_object_id FROM conversion_jobs"
                ).fetchall()
            ]
        }
    except Exception:
        raise RecoveryError("Restored database schema is incomplete") from None
    for key in _referenced_keys_from_rows(rows):
        path = objects / key
        if path.is_symlink() or not path.is_file():
            raise RecoveryError("Restored stable object references are incomplete")
