"""Real SQLite and filesystem recovery-set integration coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from markweave.auth.models import (
    IdleSessionPolicy,
    IdleSessionPolicyAudit,
    IdleSessionPolicyOperation,
    Role,
    User,
)
from markweave.config import StorageProfile
from markweave.jobs.models import JobOutput, JobProcessResult, JobRequest, JobState
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import ConversionJobRow, IdleSessionPolicyAuditRow
from markweave.persistence.sql import (
    SqlIdleSessionPolicyRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.recovery_adapters import filesystem_lock
from markweave.recovery_manifest import RecoveryError, load_and_verify_manifest
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope
from tests.template_records import publish_template_pair

pytestmark = [pytest.mark.unit, pytest.mark.integration]


def _standalone_data(root: Path) -> Path:
    data = root / "data"
    data.mkdir()
    objects = data / "objects" / "uploads" / str(uuid4())
    objects.mkdir(parents=True)
    (objects / str(uuid4())).write_bytes(b"stable-object")
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()
    return data


def _data_with_queued_job(root: Path):
    data = root / "data"
    data.mkdir()
    engine = create_database_engine(standalone_database_url(data))
    upgrade_database(engine)
    owner = User(uuid4(), "Recovery owner", "recovery-owner", "hash", Role.USER)
    SqlUserRepository(engine).create(owner)
    template_id, version_id = uuid4(), uuid4()
    publish_template_pair(engine, owner.id, template_id, version_id)
    objects = FilesystemObjectStore(data)
    objects.put(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner.id, version_id),
        b"template",
    )
    repository = SqlJobRepository(engine, JobAdmissionPolicy(2, 2))
    service = JobService(repository, objects, JobServicePolicy(10))
    now = datetime.now(UTC)
    job, replayed = service.submit(
        JobRequest(
            owner.id,
            b"# recovery source",
            template_id,
            version_id,
            JobOutput.DOCX,
            (("markweave", "test"),),
            now,
        ),
        "recovery-upload",
    )
    assert not replayed
    source = ObjectKey(ObjectScope.UPLOAD, owner.id, job.source_object_id)
    assert objects.exists(source)
    return data, engine, repository, objects, service, owner.id, job.id, source, now


def _backup_and_restore(data: Path, root: Path) -> Path:
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (root / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    target = root / "restored"
    service.restore(
        RestoreRequest(
            StorageProfile.STANDALONE,
            (root / "backups" / manifest.backup_id).resolve(),
            30,
            "offline-expired-upload-proof",
            data_directory=target.resolve(),
        )
    )
    return target


def test_standalone_restores_backup_after_completed_job_retention_cleanup(
    tmp_path: Path,
) -> None:
    data, engine, repository, objects, service, owner_id, job_id, source, now = (
        _data_with_queued_job(tmp_path)
    )
    service.cancel(job_id, actor_id=owner_id, actor_is_admin=False, now=now)

    class NoopProcessor:
        def process(self, *_args: object, **_kwargs: object) -> JobProcessResult:
            raise AssertionError("cleanup must not process jobs")

    worker = ConversionWorker(
        worker_id="retention",
        runtime=WorkerRuntime(
            repository,
            objects,
            NoopProcessor(),
            lambda: now + timedelta(seconds=11),
        ),
        policy=WorkerPolicy(5, 1, 10, 2),
    )
    assert worker.cleanup(limit=1) == 1
    expired = repository.get(job_id)
    assert expired is not None and expired.state is JobState.EXPIRED
    assert not objects.exists(source)
    with engine.connect() as connection:
        row = connection.execute(
            select(
                ConversionJobRow.state,
                ConversionJobRow.source_ready,
                ConversionJobRow.cleanup_completed,
            ).where(ConversionJobRow.id == str(job_id))
        ).one()
        assert row == ("expired", True, True)
    engine.dispose()

    restored = _backup_and_restore(data, tmp_path)
    restored_engine = create_database_engine(standalone_database_url(restored))
    try:
        with restored_engine.connect() as connection:
            row = connection.execute(
                select(
                    ConversionJobRow.state,
                    ConversionJobRow.source_ready,
                    ConversionJobRow.cleanup_completed,
                ).where(ConversionJobRow.id == str(job_id))
            ).one()
            assert row == ("expired", True, True)
    finally:
        restored_engine.dispose()
    assert not (restored / "objects" / source.as_posix()).exists()


@pytest.mark.parametrize("expired_incomplete", [False, True])
def test_standalone_restore_rejects_missing_required_upload(
    tmp_path: Path, expired_incomplete: bool
) -> None:
    data, engine, repository, objects, service, owner_id, job_id, source, now = (
        _data_with_queued_job(tmp_path)
    )
    if expired_incomplete:
        service.cancel(job_id, actor_id=owner_id, actor_is_admin=False, now=now)
        claimed = repository.expire_terminal(
            "retention", now + timedelta(seconds=11), now + timedelta(seconds=16), 1
        )
        assert len(claimed) == 1
    objects.delete(source)
    engine.dispose()

    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    target = tmp_path / "restored"
    with pytest.raises(RecoveryError, match="stable object references"):
        service.restore(
            RestoreRequest(
                StorageProfile.STANDALONE,
                (tmp_path / "backups" / manifest.backup_id).resolve(),
                30,
                "offline-required-upload-proof",
                data_directory=target.resolve(),
            )
        )
    assert not target.exists()


def test_standalone_backup_is_content_addressed_and_restores_atomically(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    backups = tmp_path / "backups"
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            backups.resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    recovery_set = backups / manifest.backup_id
    assert recovery_set.is_dir()
    assert load_and_verify_manifest(recovery_set) == manifest
    assert manifest.database_identity
    assert manifest.object_identity

    restored = tmp_path / "restored"
    result = service.restore(
        RestoreRequest(
            StorageProfile.STANDALONE,
            recovery_set.resolve(),
            30,
            "offline-change-window-42",
            data_directory=restored.resolve(),
        )
    )
    assert result.backup_id == manifest.backup_id
    assert (restored / "metadata.sqlite3").is_file()
    assert [
        path.read_bytes()
        for path in (restored / "objects").rglob("*")
        if path.is_file()
    ] == [b"stable-object"]


def test_standalone_backup_restore_preserves_idle_session_policy(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    engine = create_database_engine(standalone_database_url(data))
    actor = uuid4()
    audit_id = uuid4()
    created_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    policy = SqlIdleSessionPolicyRepository(engine).update(
        IdleSessionPolicy(300, 60),
        expected_revision=0,
        audit=IdleSessionPolicyAudit(
            audit_id,
            actor,
            IdleSessionPolicyOperation.UPDATE,
            30,
            15,
            300,
            60,
            1,
            created_at,
        ),
    )
    assert policy == IdleSessionPolicy(300, 60, 1)
    engine.dispose()

    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    restored = tmp_path / "restored-policy"
    service.restore(
        RestoreRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups" / manifest.backup_id).resolve(),
            30,
            "offline-policy-proof",
            data_directory=restored.resolve(),
        )
    )
    restored_engine = create_database_engine(standalone_database_url(restored))
    assert SqlIdleSessionPolicyRepository(restored_engine).get() == policy
    with restored_engine.connect() as connection:
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
        assert audit.id == str(audit_id)
        assert audit.actor_id == str(actor)
        assert audit.operation == "idle_session_policy_update"
        assert (
            audit.old_user_idle_minutes,
            audit.old_admin_idle_minutes,
            audit.new_user_idle_minutes,
            audit.new_admin_idle_minutes,
            audit.revision,
        ) == (30, 15, 300, 60, 1)
        assert audit.created_at.replace(tzinfo=UTC) == created_at
    with pytest.raises(SQLAlchemyError), restored_engine.begin() as connection:
        connection.execute(
            update(IdleSessionPolicyAuditRow)
            .where(IdleSessionPolicyAuditRow.id == str(audit_id))
            .values(new_admin_idle_minutes=6)
        )
    restored_engine.dispose()


def test_standalone_restore_rejects_tampering_before_creating_target(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    source = tmp_path / "backups" / manifest.backup_id
    database = source / "database" / "metadata.sqlite3"
    database.write_bytes(database.read_bytes() + b"tampered")
    target = tmp_path / "must-not-exist"
    with pytest.raises(RecoveryError, match="integrity"):
        service.restore(
            RestoreRequest(
                StorageProfile.STANDALONE,
                source.resolve(),
                30,
                "offline-proof",
                data_directory=target.resolve(),
            )
        )
    assert not target.exists()


def test_standalone_backup_rejects_symlinked_objects(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    owner = next((data / "objects" / "uploads").iterdir())
    (owner / str(uuid4())).symlink_to(outside)
    with pytest.raises(RecoveryError, match="unsafe member"):
        RecoveryService().backup(
            BackupRequest(
                StorageProfile.STANDALONE,
                (tmp_path / "backups").resolve(),
                30,
                data_directory=data.resolve(),
            )
        )


def test_standalone_backup_rejects_concurrent_recovery_operation(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    with (
        filesystem_lock(data / ".markweave-recovery.lock"),
        pytest.raises(RecoveryError, match="Another recovery operation"),
    ):
        RecoveryService().backup(
            BackupRequest(
                StorageProfile.STANDALONE,
                (tmp_path / "backups").resolve(),
                30,
                data_directory=data.resolve(),
            )
        )


def test_manifest_rejects_unexpected_files_and_symlinked_members(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    manifest = RecoveryService().backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    source = tmp_path / "backups" / manifest.backup_id
    (source / "unexpected").write_text("no", encoding="utf-8")
    with pytest.raises(RecoveryError, match="unexpected"):
        load_and_verify_manifest(source)


def test_restore_rejects_preexisting_destination_without_removing_it(
    tmp_path: Path,
) -> None:
    data = _standalone_data(tmp_path)
    manifest = RecoveryService().backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (tmp_path / "backups").resolve(),
            30,
            data_directory=data.resolve(),
        )
    )
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "marker"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(RecoveryError, match="absent isolated"):
        RecoveryService().restore(
            RestoreRequest(
                StorageProfile.STANDALONE,
                (tmp_path / "backups" / manifest.backup_id).resolve(),
                30,
                "offline-proof",
                data_directory=target.resolve(),
            )
        )
    assert marker.read_text(encoding="utf-8") == "preserve"
