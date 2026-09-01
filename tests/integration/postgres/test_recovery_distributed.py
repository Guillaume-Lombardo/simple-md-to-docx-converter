"""Real PostgreSQL and RustFS backup/restore integration coverage."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from markweave.auth.models import (
    IdleSessionPolicy,
    IdleSessionPolicyAudit,
    IdleSessionPolicyOperation,
)
from markweave.config import StorageProfile
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import IdleSessionPolicyAuditRow, UserRow
from markweave.persistence.sql import (
    SqlIdleSessionPolicyRepository,
    create_database_engine,
)
from markweave.recovery_adapters import S3Configuration
from markweave.recovery_manifest import RecoveryError
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )


@pytest.fixture
def s3_client() -> Iterator[object]:
    """Own the real provider client for the complete integration test."""

    client = _client()
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


def _target_database(stack: ExitStack) -> str:
    source_url = make_url(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    schema = f"recovery_{uuid4().hex}"
    admin = create_database_engine(source_url)
    stack.callback(admin.dispose)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    def drop() -> None:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

    stack.callback(drop)
    return source_url.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)


def _prepare_source(client, bucket: str) -> tuple[str, bytes, str, str, datetime]:
    database_url = os.environ["MARKWEAVE_TEST_POSTGRES_URL"]
    engine = create_database_engine(database_url)
    try:
        upgrade_database(engine)
        with engine.begin() as connection:
            connection.execute(
                insert(UserRow),
                {
                    "id": str(uuid4()),
                    "username": "Recovery User",
                    "normalized_username": f"recovery-{uuid4().hex}",
                    "password_hash": "hash:recovery",
                    "role": "user",
                    "active": True,
                    "auth_version": 0,
                    "password_change_required": False,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO audit_cleanup_guards (id) VALUES ('recovery-policy') ON CONFLICT DO NOTHING"
                )
            )
            connection.execute(text("DELETE FROM idle_session_policy_audit_records"))
            connection.execute(
                text("DELETE FROM audit_cleanup_guards WHERE id = 'recovery-policy'")
            )
            connection.execute(text("DELETE FROM idle_session_policy"))
        actor = uuid4()
        audit_id = uuid4()
        created_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        assert SqlIdleSessionPolicyRepository(engine).update(
            IdleSessionPolicy(300, 5),
            expected_revision=0,
            audit=IdleSessionPolicyAudit(
                audit_id,
                actor,
                IdleSessionPolicyOperation.UPDATE,
                30,
                15,
                300,
                5,
                1,
                created_at,
            ),
        ) == IdleSessionPolicy(300, 5, 1)
    finally:
        engine.dispose()
    key = f"uploads/{uuid4()}/{uuid4()}"
    content = b"distributed-stable-object"
    client.put_object(Bucket=bucket, Key=key, Body=content)
    return key, content, str(actor), str(audit_id), created_at


def test_distributed_backup_and_isolated_restore_bind_both_provider_identities(
    tmp_path: Path,
    s3_client,
) -> None:
    client = s3_client
    source_bucket = os.environ["MARKWEAVE_TEST_S3_BUCKET"]
    key, content, actor_id, audit_id, created_at = _prepare_source(
        client, source_bucket
    )
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            (tmp_path / "sets").resolve(),
            60,
            database_url=os.environ["MARKWEAVE_TEST_POSTGRES_URL"],
            s3=_configuration(source_bucket),
            consistency_proof="workers-drained-42",
        )
    )
    assert manifest.database_identity
    assert manifest.object_identity
    assert manifest.consistency_proof == "workers-drained-42"

    with ExitStack() as cleanup:
        target_database = _target_database(cleanup)
        target_bucket = f"restore-{uuid4().hex}"
        client.create_bucket(Bucket=target_bucket)
        cleanup.callback(_delete_bucket, client, target_bucket)
        result = service.restore(
            RestoreRequest(
                StorageProfile.DISTRIBUTED,
                (tmp_path / "sets" / manifest.backup_id).resolve(),
                60,
                "isolated-environment-42",
                database_url=target_database,
                s3=_configuration(target_bucket),
            )
        )
        assert result.backup_id == manifest.backup_id
        with closing(client.get_object(Bucket=target_bucket, Key=key)["Body"]) as body:
            assert body.read() == content
        engine = create_database_engine(target_database)
        try:
            with engine.connect() as connection:
                assert connection.scalar(select(UserRow.username)) == "Recovery User"
                assert SqlIdleSessionPolicyRepository(engine).get() == (
                    IdleSessionPolicy(300, 5, 1)
                )
                audit = connection.execute(
                    select(
                        IdleSessionPolicyAuditRow.id,
                        IdleSessionPolicyAuditRow.actor_id,
                        IdleSessionPolicyAuditRow.operation,
                        IdleSessionPolicyAuditRow.old_user_idle_minutes,
                        IdleSessionPolicyAuditRow.old_admin_idle_minutes,
                        IdleSessionPolicyAuditRow.new_user_idle_minutes,
                        IdleSessionPolicyAuditRow.new_admin_idle_minutes,
                        IdleSessionPolicyAuditRow.revision,
                        IdleSessionPolicyAuditRow.created_at,
                    )
                ).one()
                assert audit.id == audit_id and audit.actor_id == actor_id
                assert audit.operation == "idle_session_policy_update"
                assert (
                    audit.old_user_idle_minutes,
                    audit.old_admin_idle_minutes,
                    audit.new_user_idle_minutes,
                    audit.new_admin_idle_minutes,
                    audit.revision,
                ) == (30, 15, 300, 5, 1)
                assert audit.created_at == created_at
            with pytest.raises(SQLAlchemyError), engine.begin() as connection:
                connection.execute(
                    update(IdleSessionPolicyAuditRow)
                    .where(IdleSessionPolicyAuditRow.id == audit_id)
                    .values(new_admin_idle_minutes=6)
                )
        finally:
            engine.dispose()


def test_distributed_restore_cleans_target_bucket_when_database_is_not_isolated(
    tmp_path: Path,
    s3_client,
) -> None:
    client = s3_client
    source_bucket = os.environ["MARKWEAVE_TEST_S3_BUCKET"]
    _prepare_source(client, source_bucket)
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            (tmp_path / "sets").resolve(),
            60,
            database_url=os.environ["MARKWEAVE_TEST_POSTGRES_URL"],
            s3=_configuration(source_bucket),
            consistency_proof="workers-drained-43",
        )
    )
    target_bucket = f"failed-{uuid4().hex}"
    client.create_bucket(Bucket=target_bucket)
    try:
        with pytest.raises(RecoveryError, match="not isolated"):
            service.restore(
                RestoreRequest(
                    StorageProfile.DISTRIBUTED,
                    (tmp_path / "sets" / manifest.backup_id).resolve(),
                    60,
                    "isolated-environment-43",
                    database_url=os.environ["MARKWEAVE_TEST_POSTGRES_URL"],
                    s3=_configuration(target_bucket),
                )
            )
        assert not client.list_objects_v2(Bucket=target_bucket).get("Contents", [])
    finally:
        _delete_bucket(client, target_bucket)
