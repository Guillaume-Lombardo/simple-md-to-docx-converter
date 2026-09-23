"""Real PostgreSQL/S3 Composer publication and connection fencing."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import boto3
import pytest
from sqlalchemy.orm import Session

from markweave.composer.connections import (
    ConnectionRecord,
    ConnectionScope,
    IdentityMode,
)
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    RevisionSnapshot,
)
from markweave.composer.secrets import EncryptedCredentials
from markweave.config import StorageProfile
from markweave.persistence.composer import (
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest
from markweave.storage import S3ObjectStore
from tests.integration.postgres.test_recovery_distributed import (
    _configuration,
    _delete_bucket,
    _target_database,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def _store() -> S3ObjectStore:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    return S3ObjectStore(client, os.environ["MARKWEAVE_TEST_S3_BUCKET"])


def test_postgresql_s3_revision_publication_survives_restart_and_serializes_writers() -> (
    None
):
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    owner = uuid4()
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner),
                username="composer-owner",
                normalized_username="composer-owner",
                password_hash="hash",  # noqa: S106 - isolated fixture
                role="user",
                active=True,
                auth_version=0,
                password_change_required=False,
            )
        )
    store = _store()
    try:
        repository = SqlComposerRepository(engine, store)
        draft = repository.create_draft_with_source(
            owner,
            b"scanned source",
            "scanner receipt",
            title="Draft",
            content="hello",
            media_type="text/markdown",
        )
        snapshot = RevisionSnapshot(
            draft.source, None, "{}", "{}", None, "approved", "generate"
        )
        artifacts = (
            ArtifactContent("download", "text/plain", b"result"),
            ArtifactContent("preview", "text/html", b"preview"),
        )

        def publish(key: str) -> str:
            try:
                revision = repository.publish_revision(
                    owner,
                    draft.id,
                    actor_id=owner,
                    if_match=draft.etag,
                    idempotency_key=key,
                    snapshot=snapshot,
                    artifacts=artifacts,
                )
                return str(revision.id)
            except ComposerConflictError:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(publish, ("first", "second")))
        assert sum(result != "conflict" for result in results) == 1
        restarted = SqlComposerRepository(engine, store)
        revisions = restarted.list_revisions(owner, draft.id)
        assert len(revisions) == 1
        assert (
            restarted.read_artifact(owner, draft.id, revisions[0].id, "download")
            == b"result"
        )
        assert (
            restarted.get_draft(owner, draft.id).current_revision_id == revisions[0].id
        )
    finally:
        store.close()
        engine.dispose()


def test_postgresql_connection_generation_and_explicit_grants() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    owner, other = uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id in (owner, other):
            database.add(
                UserRow(
                    id=str(user_id),
                    username=str(user_id),
                    normalized_username=str(user_id),
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role="user",
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
    try:
        connections = SqlConnectionRepository(engine, maximum_allowed_users=1000)
        record = ConnectionRecord(
            uuid4(),
            ConnectionScope.INSTANCE,
            None,
            IdentityMode.INDIVIDUAL,
            "https://example.internal/v1",
            "model-a",
            ("model-a",),
            True,
            frozenset({owner}),
            0,
            0,
            name="Instance model",
        )
        saved = connections.save_connection(
            record, expected_version=None, actor_id=owner
        )
        assert saved.allowed_user_ids == frozenset({owner})
        assert other not in saved.allowed_user_ids
        saved = connections.save_connection(
            saved,
            expected_version=saved.version,
            actor_id=owner,
            credential_user_id=owner,
            credentials=EncryptedCredentials(api_key=b"opaque-ciphertext"),
        )
        assert connections.set_outage(
            saved.id, owner, True, expected_generation=saved.generation
        )
        changed = connections.save_connection(
            saved, expected_version=saved.version, actor_id=owner
        )
        assert changed.generation > saved.generation
        assert not connections.set_outage(
            saved.id, owner, True, expected_generation=saved.generation
        )
        assert not connections.get_outage(saved.id, owner)
        revoked = connections.revoke_connection(
            changed, expected_version=changed.version, actor_id=owner
        )
        assert (
            not revoked.enabled
            and connections.get_credentials(revoked.id, owner) is None
        )
    finally:
        engine.dispose()


def test_postgresql_s3_isolated_restore_retains_composer_references(
    tmp_path: Path,
) -> None:
    source_url = os.environ["MARKWEAVE_TEST_POSTGRES_URL"]
    engine = create_database_engine(source_url)
    upgrade_database(engine)
    owner = uuid4()
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner),
                username="restore-owner",
                normalized_username="restore-owner",
                password_hash="hash",  # noqa: S106 - isolated fixture
                role="user",
                active=True,
                auth_version=0,
                password_change_required=False,
            )
        )
    store = _store()
    source_bucket = os.environ["MARKWEAVE_TEST_S3_BUCKET"]
    try:
        repository = SqlComposerRepository(engine, store)
        draft = repository.create_draft_with_source(
            owner,
            b"scanned",
            "scan-ok",
            title="Draft",
            content="hello",
            media_type="text/markdown",
        )
        revision = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="restore",
            snapshot=RevisionSnapshot(
                draft.source, None, "{}", "{}", None, "human", "generate"
            ),
            artifacts=(
                ArtifactContent("download", "text/plain", b"download"),
                ArtifactContent("preview", "text/html", b"preview"),
            ),
        )
        service = RecoveryService()
        manifest = service.backup(
            BackupRequest(
                StorageProfile.DISTRIBUTED,
                (tmp_path / "sets").resolve(),
                60,
                database_url=source_url,
                s3=_configuration(source_bucket),
                consistency_proof="composer-workers-drained",
            )
        )
        with ExitStack() as cleanup:
            target_url = _target_database(cleanup)
            target_bucket = f"restore-{uuid4().hex}"
            client = store._client  # existing isolated test client, closed with store
            client.create_bucket(Bucket=target_bucket)
            cleanup.callback(_delete_bucket, client, target_bucket)
            service.restore(
                RestoreRequest(
                    StorageProfile.DISTRIBUTED,
                    (tmp_path / "sets" / manifest.backup_id).resolve(),
                    60,
                    "isolated-composer-test",
                    database_url=target_url,
                    s3=_configuration(target_bucket),
                )
            )
            target_engine = create_database_engine(target_url)
            target_store = S3ObjectStore(client, target_bucket)
            try:
                restored = SqlComposerRepository(target_engine, target_store)
                assert restored.read_source(owner, draft.source.object_id) == b"scanned"
                assert (
                    restored.get_draft(owner, draft.id).current_revision_id
                    == revision.id
                )
                assert (
                    restored.read_artifact(owner, draft.id, revision.id, "download")
                    == b"download"
                )
            finally:
                target_engine.dispose()
    finally:
        store.close()
        engine.dispose()
