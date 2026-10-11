"""Distributed restore of recovery sets created by an earlier supported schema."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from markweave.config import StorageProfile
from markweave.persistence.migrations import upgrade_database, upgrade_database_to
from markweave.persistence.schema import ComposerRevisionRow, ConversionJobRow
from markweave.persistence.sql import create_database_engine
from markweave.recovery_adapters import (
    PostgreSQLRecoveryAdapter,
    RecoveryDeadline,
    S3Configuration,
)
from markweave.recovery_manifest import RecoveryError, RecoveryManifest
from markweave.recovery_schema import OLDEST_RECOVERY_SET_REVISION, head_revision
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest
from tests.recovery_older_schema import (
    PRE_FILL_PLAN_REVISION,
    OlderSchemaRecords,
    populate_older_schema,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


@pytest.fixture
def s3_client() -> Iterator[object]:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    try:
        yield client
    finally:
        client.close()


def _configuration(bucket: str) -> S3Configuration:
    return S3Configuration(
        bucket,
        os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        os.environ["MARKWEAVE_TEST_S3_REGION"],
        os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )


def _delete_bucket(client, bucket: str) -> None:
    listed = client.list_objects_v2(Bucket=bucket)
    objects = [{"Key": item["Key"]} for item in listed.get("Contents", [])]
    if objects:
        client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
    client.delete_bucket(Bucket=bucket)


def _schema_url(stack: ExitStack) -> str:
    base = make_url(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    schema = f"older_{uuid4().hex}"
    admin = create_database_engine(base)
    stack.callback(admin.dispose)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    def drop() -> None:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

    stack.callback(drop)
    return base.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)


def _bucket(stack: ExitStack, client, prefix: str) -> str:
    bucket = f"{prefix}-{uuid4().hex}"
    client.create_bucket(Bucket=bucket)
    stack.callback(_delete_bucket, client, bucket)
    return bucket


def _older_source(stack: ExitStack, client) -> tuple[str, str, OlderSchemaRecords]:
    database = _schema_url(stack)
    bucket = _bucket(stack, client, "older-source")
    engine = create_database_engine(database)
    try:
        upgrade_database_to(engine, PRE_FILL_PLAN_REVISION)
        records = populate_older_schema(engine)
    finally:
        engine.dispose()
    for key, content in records.objects.items():
        client.put_object(Bucket=bucket, Key=key, Body=content)
    return database, bucket, records


def _backup(root: Path, database: str, bucket: str) -> RecoveryManifest:
    return RecoveryService().backup(
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            (root / "sets").resolve(),
            120,
            database_url=database,
            s3=_configuration(bucket),
            consistency_proof="workers-drained-older-schema",
        )
    )


def _restore_request(
    root: Path, manifest: RecoveryManifest, database: str, bucket: str
) -> RestoreRequest:
    return RestoreRequest(
        StorageProfile.DISTRIBUTED,
        (root / "sets" / manifest.backup_id).resolve(),
        120,
        "isolated-older-schema-proof",
        database_url=database,
        s3=_configuration(bucket),
    )


def test_distributed_restores_pre_fill_plan_set_to_head_with_references(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, records = _older_source(stack, client)
        manifest = _backup(tmp_path, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "older-target")
        result = RecoveryService().restore(
            _restore_request(tmp_path, manifest, target_database, target_bucket)
        )
        assert result.backup_id == manifest.backup_id
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == head_revision()
                )
                assert "composer_fill_plans" in inspect(connection).get_table_names()
                assert connection.execute(
                    select(ConversionJobRow.state, ConversionJobRow.owner_id).where(
                        ConversionJobRow.id == records.job_id
                    )
                ).one() == ("succeeded", records.owner_id)
                assert (
                    connection.scalar(
                        select(ComposerRevisionRow.publication_state).where(
                            ComposerRevisionRow.id == records.revision_id
                        )
                    )
                    == "published"
                )
        finally:
            engine.dispose()
        for key, content in records.objects.items():
            response = client.get_object(Bucket=target_bucket, Key=key)
            with closing(response["Body"]) as body:
                assert body.read() == content


def test_distributed_refuses_earlier_set_missing_a_retained_object(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, records = _older_source(stack, client)
        client.delete_object(Bucket=bucket, Key=records.key("composer-artifacts"))
        manifest = _backup(tmp_path, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "older-missing")
        with pytest.raises(RecoveryError, match="stable object references"):
            RecoveryService().restore(
                _restore_request(tmp_path, manifest, target_database, target_bucket)
            )
        assert not client.list_objects_v2(Bucket=target_bucket).get("Contents", [])
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert inspect(connection).get_table_names() == []
        finally:
            engine.dispose()


def test_distributed_refuses_earlier_set_into_a_migrated_target(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, _records = _older_source(stack, client)
        manifest = _backup(tmp_path, database, bucket)
        target_database = _schema_url(stack)
        engine = create_database_engine(target_database)
        try:
            upgrade_database(engine)
        finally:
            engine.dispose()
        target_bucket = _bucket(stack, client, "older-migrated")
        with pytest.raises(RecoveryError, match="must be empty for an earlier schema"):
            RecoveryService().restore(
                _restore_request(tmp_path, manifest, target_database, target_bucket)
            )
        assert not client.list_objects_v2(Bucket=target_bucket).get("Contents", [])


def test_distributed_backup_refuses_unknown_newer_schema(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database = _schema_url(stack)
        bucket = _bucket(stack, client, "newer-source")
        engine = create_database_engine(database)
        try:
            upgrade_database(engine)
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE alembic_version SET version_num = '20991231_99'")
                )
        finally:
            engine.dispose()
        with pytest.raises(RecoveryError, match="unknown to this release"):
            _backup(tmp_path, database, bucket)
        assert [path.name for path in (tmp_path / "sets").iterdir()] == [
            ".markweave-recovery.lock"
        ]


def test_distributed_incompatible_earlier_payload_rolls_back_its_migration(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, _bucket, records = _older_source(stack, client)
        staging = tmp_path / "staging"
        staging.mkdir()
        adapter = PostgreSQLRecoveryAdapter()
        backup = adapter.backup(database, staging, RecoveryDeadline.after(60))
        payload = staging / "database" / "metadata.json"
        document = json.loads(payload.read_bytes())
        assert document["revision"] == PRE_FILL_PLAN_REVISION
        questions = next(
            table
            for table in document["tables"]
            if table["name"] == "composer_questions"
        )
        questions["columns"].pop()
        payload.write_text(json.dumps(document), encoding="utf-8")
        target_database = _schema_url(stack)
        with pytest.raises(RecoveryError, match="schema is incompatible"):
            adapter.restore(
                target_database,
                payload,
                source_identity=backup.source_identity,
                object_keys=frozenset(records.objects),
                deadline=RecoveryDeadline.after(60),
            )
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert inspect(connection).get_table_names() == []
        finally:
            engine.dispose()


def test_distributed_restores_oldest_supported_recovery_set(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database = _schema_url(stack)
        bucket = _bucket(stack, client, "oldest-source")
        engine = create_database_engine(database)
        try:
            upgrade_database_to(engine, OLDEST_RECOVERY_SET_REVISION)
        finally:
            engine.dispose()
        manifest = _backup(tmp_path, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "oldest-target")
        RecoveryService().restore(
            _restore_request(tmp_path, manifest, target_database, target_bucket)
        )
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == head_revision()
                )
        finally:
            engine.dispose()
