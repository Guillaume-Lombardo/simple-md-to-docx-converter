"""Distributed restore of recovery sets created by an earlier supported schema.

Production backup captures only the current model, so earlier-revision sets are
written by the test-only historical producer shared with the final-image smoke.
"""

from __future__ import annotations

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
from markweave.persistence.schema import (
    ComposerRevisionRow,
    ConversionJobRow,
    TemplateRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.recovery_adapters import S3Configuration
from markweave.recovery_manifest import RecoveryError
from markweave.recovery_schema import OLDEST_RECOVERY_SET_REVISION, head_revision
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest
from tests.e2e.recovery_cli_setup import write_historical_distributed_set
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
_BELOW_FLOOR_REVISION = "20260828_13"
_UNKNOWN_REVISION = "20991231_99"


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


def _historical_set(
    root: Path,
    client,
    database: str,
    bucket: str,
    **options,
) -> Path:
    destination = (root / "sets").resolve()
    backup_id = write_historical_distributed_set(
        destination,
        database,
        client,
        (
            os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
            os.environ["MARKWEAVE_TEST_S3_REGION"],
            bucket,
        ),
        **options,
    )
    return destination / backup_id


def _restore(source: Path, database: str, bucket: str) -> None:
    RecoveryService().restore(
        RestoreRequest(
            StorageProfile.DISTRIBUTED,
            source,
            120,
            "isolated-older-schema-proof",
            database_url=database,
            s3=_configuration(bucket),
        )
    )


def _assert_untouched(client, database: str, bucket: str) -> None:
    assert not client.list_objects_v2(Bucket=bucket).get("Contents", [])
    engine = create_database_engine(database)
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == []
    finally:
        engine.dispose()


def test_distributed_restores_pre_fill_plan_set_to_head_with_references(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, records = _older_source(stack, client)
        source = _historical_set(tmp_path, client, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "older-target")
        _restore(source, target_database, target_bucket)
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == head_revision()
                )
                assert "composer_fill_plans" in inspect(connection).get_table_names()
                assert connection.execute(
                    select(
                        ConversionJobRow.state,
                        ConversionJobRow.owner_id,
                        ConversionJobRow.template_id,
                        ConversionJobRow.template_version_id,
                    ).where(ConversionJobRow.id == records.job_id)
                ).one() == (
                    "succeeded",
                    records.owner_id,
                    records.template_id,
                    records.template_version_id,
                )
                assert (
                    connection.scalar(
                        select(TemplateRow.current_version_id).where(
                            TemplateRow.id == records.template_id
                        )
                    )
                    == records.template_version_id
                )
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


def test_distributed_restores_current_template_pointer_from_a_head_backup(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database = _schema_url(stack)
        bucket = _bucket(stack, client, "head-source")
        engine = create_database_engine(database)
        try:
            upgrade_database(engine)
            records = populate_older_schema(engine)
        finally:
            engine.dispose()
        for key, content in records.objects.items():
            client.put_object(Bucket=bucket, Key=key, Body=content)
        manifest = RecoveryService().backup(
            BackupRequest(
                StorageProfile.DISTRIBUTED,
                (tmp_path / "sets").resolve(),
                120,
                database_url=database,
                s3=_configuration(bucket),
                consistency_proof="workers-drained-head",
            )
        )
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "head-target")
        _restore(
            (tmp_path / "sets" / manifest.backup_id).resolve(),
            target_database,
            target_bucket,
        )
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert connection.execute(
                    select(TemplateRow.current_version_id).where(
                        TemplateRow.id == records.template_id
                    )
                ).scalar_one() == (records.template_version_id)
                assert (
                    connection.scalar(
                        select(ConversionJobRow.template_version_id).where(
                            ConversionJobRow.id == records.job_id
                        )
                    )
                    == records.template_version_id
                )
        finally:
            engine.dispose()


def test_distributed_refuses_earlier_set_missing_a_retained_object(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, records = _older_source(stack, client)
        client.delete_object(Bucket=bucket, Key=records.key("composer-artifacts"))
        source = _historical_set(tmp_path, client, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "older-missing")
        with pytest.raises(RecoveryError, match="stable object references"):
            _restore(source, target_database, target_bucket)
        _assert_untouched(client, target_database, target_bucket)


def test_distributed_refuses_earlier_set_into_a_migrated_target(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, _records = _older_source(stack, client)
        source = _historical_set(tmp_path, client, database, bucket)
        target_database = _schema_url(stack)
        engine = create_database_engine(target_database)
        try:
            upgrade_database(engine)
        finally:
            engine.dispose()
        target_bucket = _bucket(stack, client, "older-migrated")
        with pytest.raises(RecoveryError, match="must be empty for an earlier schema"):
            _restore(source, target_database, target_bucket)
        assert not client.list_objects_v2(Bucket=target_bucket).get("Contents", [])


def test_distributed_incomplete_earlier_set_rolls_back_and_removes_objects(
    tmp_path: Path, s3_client
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database, bucket, _records = _older_source(stack, client)
        source = _historical_set(
            tmp_path,
            client,
            database,
            bucket,
            omit_tables=frozenset({"composer_questions"}),
        )
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "older-incomplete")
        with pytest.raises(RecoveryError, match="schema is incompatible"):
            _restore(source, target_database, target_bucket)
        _assert_untouched(client, target_database, target_bucket)


@pytest.mark.parametrize(
    ("revision", "message"),
    [
        (_UNKNOWN_REVISION, "unknown to this release"),
        (_BELOW_FLOOR_REVISION, "predates supported recovery sets"),
    ],
)
def test_distributed_restore_refuses_unsupported_revisions_before_writes(
    tmp_path: Path, s3_client, revision: str, message: str
) -> None:
    client = s3_client
    with ExitStack() as stack:
        database = _schema_url(stack)
        bucket = _bucket(stack, client, "unsupported-source")
        engine = create_database_engine(database)
        try:
            if revision == _UNKNOWN_REVISION:
                upgrade_database(engine)
                with engine.begin() as connection:
                    connection.execute(
                        text("UPDATE alembic_version SET version_num = :revision"),
                        {"revision": revision},
                    )
            else:
                upgrade_database_to(engine, revision)
        finally:
            engine.dispose()
        client.put_object(
            Bucket=bucket, Key=f"uploads/{uuid4()}/{uuid4()}", Body=b"object"
        )
        if revision == _UNKNOWN_REVISION:
            # The unchanged production backup records the newer label it observes.
            manifest = RecoveryService().backup(
                BackupRequest(
                    StorageProfile.DISTRIBUTED,
                    (tmp_path / "sets").resolve(),
                    120,
                    database_url=database,
                    s3=_configuration(bucket),
                    consistency_proof="workers-drained-newer",
                )
            )
            source = (tmp_path / "sets" / manifest.backup_id).resolve()
        else:
            source = _historical_set(tmp_path, client, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "unsupported-target")
        with pytest.raises(RecoveryError, match=message):
            _restore(source, target_database, target_bucket)
        _assert_untouched(client, target_database, target_bucket)


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
        source = _historical_set(tmp_path, client, database, bucket)
        target_database = _schema_url(stack)
        target_bucket = _bucket(stack, client, "oldest-target")
        _restore(source, target_database, target_bucket)
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == head_revision()
                )
        finally:
            engine.dispose()
