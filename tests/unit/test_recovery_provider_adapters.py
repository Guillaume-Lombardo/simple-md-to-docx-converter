"""Deterministic provider-adapter unit coverage with in-process fakes."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import func, insert, select
from sqlalchemy import inspect as sqlalchemy_inspect

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.recovery_adapters import (
    PostgreSQLRecoveryAdapter,
    RecoveryDeadline,
    S3Configuration,
    S3RecoveryAdapter,
    StandaloneRecoveryAdapter,
    _decode,
    _encode,
    _load_database_payload,
    _referenced_keys_from_rows,
    _safe_absolute_directory,
    _safe_new_destination,
    _validate_object_key,
    filesystem_lock,
)
from markweave.recovery_manifest import RecoveryError, RecoveryMember

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def one(self):
        return self._value


class _PostgreSQLConnection:
    def __init__(self, connection, resource: tuple[Any, ...]) -> None:
        self.raw = connection
        self.resource = resource

    def execution_options(self, **_options):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.raw.close()

    def begin(self):
        return self.raw.begin()

    def execute(self, statement, parameters=None):
        rendered = str(statement)
        if "current_database()" in rendered:
            return _Result(self.resource)
        if "pg_current_wal_lsn" in rendered:
            return _Result(("0/42", "42:42:"))
        return self.raw.execute(statement, parameters or {})

    def scalar(self, statement, parameters=None):
        rendered = str(statement)
        if "pg_try_advisory" in rendered:
            return True
        return self.raw.scalar(statement, parameters or {})


class _PostgreSQLEngine:
    def __init__(self, engine, resource: tuple[Any, ...]) -> None:
        self.raw = engine
        self.resource = resource

    def connect(self):
        return _PostgreSQLConnection(self.raw.connect(), self.resource)

    @contextmanager
    def begin(self):
        connection = self.raw.connect()
        wrapper = _PostgreSQLConnection(connection, self.resource)
        try:
            with connection.begin():
                yield wrapper
        finally:
            connection.close()

    def dispose(self) -> None:
        pass


class _S3:
    def __init__(self) -> None:
        self.closed = False
        self.buckets: dict[str, dict[str, bytes]] = {
            "source": {},
            "target": {},
        }

    def list_objects_v2(self, *, Bucket, **_arguments):
        return {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(value),
                    "ETag": f'"{len(value)}"',
                }
                for key, value in self.buckets[Bucket].items()
            ],
            "IsTruncated": False,
            "KeyCount": len(self.buckets[Bucket]),
        }

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.buckets[Bucket][Key])}

    def put_object(self, *, Bucket, Key, Body, **_arguments):
        self.buckets[Bucket][Key] = Body.read() if hasattr(Body, "read") else Body

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.buckets[Bucket][Key])}

    def delete_object(self, *, Bucket, Key):
        self.buckets[Bucket].pop(Key, None)

    def close(self) -> None:
        self.closed = True


def _database(path: Path, username: str):
    engine = create_database_engine(standalone_database_url(path))
    upgrade_database(engine)
    if username:
        with engine.begin() as connection:
            connection.execute(
                insert(UserRow),
                {
                    "id": str(uuid4()),
                    "username": username,
                    "normalized_username": username.casefold(),
                    "password_hash": "hash:test",
                    "role": "user",
                    "active": True,
                    "auth_version": 0,
                    "password_change_required": False,
                },
            )
    return engine


def test_postgresql_logical_adapter_round_trips_current_schema(
    tmp_path: Path, mocker
) -> None:
    source_path = tmp_path / "source"
    target_path = tmp_path / "target"
    source_path.mkdir()
    target_path.mkdir()
    source = _database(source_path, "Provider User")
    target = _database(target_path, "")
    source_wrapper = _PostgreSQLEngine(source, ("source", "main", "local", 5432))
    target_wrapper = _PostgreSQLEngine(target, ("target", "main", "local", 5432))
    engines = iter((source_wrapper, target_wrapper))
    mocker.patch(
        "markweave.recovery_adapters.create_database_engine",
        side_effect=lambda *_args, **_kwargs: next(engines),
    )
    mocker.patch(
        "markweave.recovery_adapters.inspect",
        side_effect=lambda connection: sqlalchemy_inspect(connection.raw),
    )
    adapter = PostgreSQLRecoveryAdapter()
    staging = tmp_path / "staging"
    staging.mkdir()
    backup = adapter.backup(
        "postgresql://redacted", staging, RecoveryDeadline.after(30)
    )
    assert backup.identity
    evidence = adapter.restore(
        "postgresql://redacted-target",
        staging / "database" / "metadata.json",
        source_identity=backup.source_identity,
        object_keys=frozenset(),
        deadline=RecoveryDeadline.after(30),
    )
    assert evidence != backup.source_identity
    with target.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(UserRow)) == 1
    source.dispose()
    target.dispose()


def test_s3_adapter_copies_checksums_and_removes_failed_restore_objects(
    tmp_path: Path, mocker
) -> None:
    fake = _S3()
    key = f"uploads/{uuid4()}/{uuid4()}"
    fake.buckets["source"][key] = b"provider-object"
    mocker.patch("markweave.recovery_adapters.boto3.client", return_value=fake)
    staging = tmp_path / "staging"
    staging.mkdir()
    source = S3RecoveryAdapter(
        S3Configuration("source", "https://objects.example", "region"),
        RecoveryDeadline.after(30),
    )
    backup = source.backup(staging)
    target = S3RecoveryAdapter(
        S3Configuration("target", "https://objects.example", "region"),
        RecoveryDeadline.after(30),
    )
    evidence, keys = target.ensure_empty_and_restore(
        staging, backup.members, source_identity=backup.source_identity
    )
    assert evidence
    assert keys == {key}
    assert fake.buckets["target"][key] == b"provider-object"
    target.remove(keys)
    assert fake.buckets["target"] == {}
    target.close()
    assert fake.closed


@pytest.mark.parametrize(
    "key",
    (
        "/absolute",
        "uploads/not-a-uuid/not-a-uuid",
        "unknown/00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000000",
    ),
)
def test_provider_payload_validation_fails_closed(tmp_path: Path, key: str) -> None:
    with pytest.raises(RecoveryError, match="object key"):
        _validate_object_key(key)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    with pytest.raises(RecoveryError, match="payload"):
        _load_database_payload(payload)
    with pytest.raises(RecoveryError, match="unsupported"):
        _encode(object())
    with pytest.raises(RecoveryError, match="invalid"):
        _decode({"unexpected": "value"})


def test_deadline_configuration_and_scalar_encodings(mocker) -> None:
    with pytest.raises(RecoveryError, match="timeout"):
        RecoveryDeadline.after(0)
    deadline = RecoveryDeadline(0)
    with pytest.raises(RecoveryError, match="timed out"):
        deadline.remaining()

    now = datetime.now(UTC)
    assert _decode(_encode(now)) == now
    assert _decode(_encode(b"bytes")) == b"bytes"
    assert _decode(_encode(None)) is None
    with pytest.raises(RecoveryError, match="invalid"):
        _decode({"$bytes": "!"})

    client = mocker.patch("markweave.recovery_adapters.boto3.client")
    configuration = S3Configuration(
        "bucket",
        "https://objects.example",
        "region",
        "ACCESS_VALUE",
        "SUPER_PRIVATE_VALUE",
    )
    S3RecoveryAdapter(configuration, RecoveryDeadline.after(1))
    assert "SUPER_PRIVATE_VALUE" not in repr(configuration)
    assert client.call_args.kwargs["aws_access_key_id"] == "ACCESS_VALUE"
    with pytest.raises(RecoveryError, match="credentials"):
        S3RecoveryAdapter(
            S3Configuration("bucket", None, None, "key"),
            RecoveryDeadline.after(1),
        )
    with pytest.raises(RecoveryError, match="bucket"):
        S3RecoveryAdapter(S3Configuration(" ", None, None), RecoveryDeadline.after(1))


def test_referenced_object_keys_cover_all_durable_payloads() -> None:
    owner = str(uuid4())
    template = str(uuid4())
    source = str(uuid4())
    result = str(uuid4())
    manifest = str(uuid4())
    keys = _referenced_keys_from_rows(
        {
            "template_versions": {
                "rows": [
                    {
                        "id": template,
                        "object_owner_id": owner,
                        "publication_state": "published",
                    },
                    {
                        "id": str(uuid4()),
                        "object_owner_id": owner,
                        "publication_state": "draft",
                    },
                ]
            },
            "conversion_jobs": {
                "rows": [
                    {
                        "owner_id": owner,
                        "source_object_id": source,
                        "source_ready": True,
                        "result_object_id": result,
                        "result_manifest_object_id": manifest,
                    },
                    {
                        "owner_id": owner,
                        "source_object_id": str(uuid4()),
                        "source_ready": False,
                        "result_object_id": None,
                        "result_manifest_object_id": None,
                    },
                ]
            },
        }
    )
    assert keys == {
        f"template-versions/{owner}/{template}",
        f"uploads/{owner}/{source}",
        f"results/{owner}/{result}",
        f"result-manifests/{owner}/{manifest}",
    }


def test_path_guards_reject_relative_existing_and_symlinked_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(RecoveryError, match="unsafe"):
        _safe_absolute_directory(Path("relative"), "Source")
    with pytest.raises(RecoveryError, match="absent"):
        _safe_new_destination(tmp_path.resolve(), "Target")
    parent_link = tmp_path / "linked"
    parent_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RecoveryError, match="absent"):
        _safe_new_destination(parent_link / "new", "Target")


def test_s3_inventory_pagination_and_restore_failures_cleanup(
    tmp_path: Path, mocker
) -> None:
    fake = _S3()
    key = f"uploads/{uuid4()}/{uuid4()}"
    fake.buckets["target"][key] = b"occupied"
    mocker.patch("markweave.recovery_adapters.boto3.client", return_value=fake)
    target = S3RecoveryAdapter(
        S3Configuration("target", None, None), RecoveryDeadline.after(30)
    )
    with pytest.raises(RecoveryError, match="not empty"):
        target.ensure_empty_and_restore(tmp_path, (), source_identity="other")
    fake.buckets["target"].clear()
    with pytest.raises(RecoveryError, match="not isolated"):
        target.ensure_empty_and_restore(
            tmp_path, (), source_identity=target._resource_identity()
        )

    source = tmp_path / "objects" / key
    source.parent.mkdir(parents=True)
    source.write_bytes(b"expected")
    member = RecoveryMember(f"objects/{key}", len(b"expected"), "0" * 64)
    with pytest.raises(RecoveryError, match="integrity"):
        target.ensure_empty_and_restore(
            tmp_path, (member,), source_identity="different"
        )
    assert fake.buckets["target"] == {}


def test_s3_restore_translates_provider_failure_after_rollback(
    tmp_path: Path, mocker
) -> None:
    fake = _S3()
    key = f"uploads/{uuid4()}/{uuid4()}"
    source = tmp_path / "objects" / key
    source.parent.mkdir(parents=True)
    source.write_bytes(b"expected")
    member = RecoveryMember(
        f"objects/{key}",
        len(b"expected"),
        "0" * 64,
    )
    mocker.patch("markweave.recovery_adapters.boto3.client", return_value=fake)
    mocker.patch.object(
        fake,
        "head_object",
        side_effect=ClientError(
            {"Error": {"Code": "InternalError", "Message": "provider failed"}},
            "HeadObject",
        ),
    )
    target = S3RecoveryAdapter(
        S3Configuration("target", None, None), RecoveryDeadline.after(30)
    )

    with pytest.raises(RecoveryError, match="S3 restore failed"):
        target.ensure_empty_and_restore(
            tmp_path, (member,), source_identity="different"
        )
    assert fake.buckets["target"] == {}


def test_s3_inventory_rejects_incomplete_pagination(mocker) -> None:
    fake = _S3()
    mocker.patch("markweave.recovery_adapters.boto3.client", return_value=fake)
    mocker.patch.object(
        fake,
        "list_objects_v2",
        return_value={
            "Contents": [],
            "IsTruncated": True,
            "NextContinuationToken": None,
        },
    )
    adapter = S3RecoveryAdapter(
        S3Configuration("source", None, None), RecoveryDeadline.after(30)
    )
    with pytest.raises(RecoveryError, match="incomplete"):
        adapter._inventory()


def test_standalone_adapter_rejects_missing_sources_and_stale_stage(
    tmp_path: Path,
) -> None:
    data = (tmp_path / "data").resolve()
    data.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    adapter = StandaloneRecoveryAdapter()
    with pytest.raises(RecoveryError, match="database"):
        adapter.backup(data, staging, RecoveryDeadline.after(1))
    (data / "metadata.sqlite3").touch()
    with pytest.raises(RecoveryError, match="object tree"):
        adapter.backup(data, staging, RecoveryDeadline.after(1))

    target = (tmp_path / "target").resolve()
    stale_stage = target.with_name(
        f".{target.name}.restore-{__import__('os').getpid()}"
    )
    stale_stage.mkdir()
    with pytest.raises(RecoveryError, match="staging path"):
        adapter.restore(tmp_path, target, (), RecoveryDeadline.after(1))


def test_filesystem_lock_does_not_translate_body_oserror(tmp_path: Path) -> None:
    lock = tmp_path / "recovery.lock"
    with (
        pytest.raises(OSError, match="operation body failed"),
        filesystem_lock(lock),
    ):
        raise OSError("operation body failed")

    with filesystem_lock(lock):
        pass


def test_standalone_adapter_skips_foreign_members_and_rejects_symlinked_directory(
    tmp_path: Path,
) -> None:
    adapter = StandaloneRecoveryAdapter()
    unrelated = RecoveryMember("database/other", 0, "a" * 64)
    with pytest.raises(RecoveryError, match="integrity"):
        adapter.restore(
            tmp_path.resolve(),
            (tmp_path / "target").resolve(),
            (unrelated,),
            RecoveryDeadline.after(1),
        )

    objects = tmp_path / "objects"
    objects.mkdir()
    (objects / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RecoveryError, match="symlink"):
        tuple(adapter._objects(objects, RecoveryDeadline.after(1)))


def test_postgresql_lock_and_reference_guards_fail_closed() -> None:
    class Unlocked:
        def scalar(self, *_args, **_kwargs):
            return False

    unlocked: Any = Unlocked()
    with pytest.raises(RecoveryError, match="Another recovery"):
        PostgreSQLRecoveryAdapter._lock(unlocked)

    owner = str(uuid4())
    payload = {
        "tables": [
            {
                "name": "conversion_jobs",
                "columns": [],
                "rows": [
                    {
                        "owner_id": owner,
                        "source_object_id": str(uuid4()),
                        "source_ready": True,
                        "result_object_id": None,
                        "result_manifest_object_id": None,
                    }
                ],
            }
        ]
    }
    with pytest.raises(RecoveryError, match="references"):
        PostgreSQLRecoveryAdapter._verify_payload_references(payload, frozenset())


def test_s3_restore_skips_database_members_and_inventory_paginates(
    tmp_path: Path, mocker
) -> None:
    fake = _S3()
    first_key = f"uploads/{uuid4()}/{uuid4()}"
    second_key = f"results/{uuid4()}/{uuid4()}"
    responses = iter(
        (
            {
                "Contents": [{"Key": first_key, "Size": 1, "ETag": '"1"'}],
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
            },
            {
                "Contents": [{"Key": second_key, "Size": 2, "ETag": '"2"'}],
                "IsTruncated": False,
            },
        )
    )
    listing = mocker.patch.object(
        fake, "list_objects_v2", side_effect=lambda **_arguments: next(responses)
    )
    mocker.patch("markweave.recovery_adapters.boto3.client", return_value=fake)
    adapter = S3RecoveryAdapter(
        S3Configuration("target", None, None), RecoveryDeadline.after(30)
    )
    assert adapter._inventory() == tuple(
        sorted(
            (
                (first_key, 1, '"1"'),
                (second_key, 2, '"2"'),
            )
        )
    )
    assert listing.call_args_list[1].kwargs["ContinuationToken"] == "page-2"
    listing.side_effect = None
    listing.return_value = {"Contents": [], "IsTruncated": False}

    evidence, keys = adapter.ensure_empty_and_restore(
        tmp_path,
        (RecoveryMember("database/metadata.json", 0, "a" * 64),),
        source_identity="different",
    )
    assert evidence
    assert keys == frozenset()
