"""Real SQLite/filesystem coverage for approved template and audit retention."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.schema import (
    RetentionCleanupRunRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.retention import DataRetentionPolicy, RetentionService
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope


@pytest.mark.integration
def test_retention_preserves_current_and_ten_newest_and_traces_audit_cleanup(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner_id = UUID(int=10)
    template_id = UUID(int=20)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    versions = [UUID(int=100 + number) for number in range(1, 13)]
    with DatabaseSession(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner_id),
                username="owner",
                normalized_username="owner",
                password_hash="not-a-" + "credential",
                role="user",
                active=True,
                auth_version=0,
            )
        )
        database.flush()
        template = TemplateRow(
            id=str(template_id),
            owner_id=str(owner_id),
            name="Retained",
            normalized_name="retained",
            description="",
            normalized_description="",
            status="active",
            revision=12,
            current_version_id=None,
            publication_state="published",
        )
        database.add(template)
        database.flush()
        for number, version_id in enumerate(versions, 1):
            database.add(
                TemplateVersionRow(
                    id=str(version_id),
                    template_id=str(template_id),
                    version_number=number,
                    object_owner_id=str(owner_id),
                    sha256="0" * 64,
                    size=1,
                    created_at=now - timedelta(days=400),
                    created_by=str(owner_id),
                    restored_from_version_id=None,
                    declared_fonts="[]",
                    resolved_fonts="[]",
                    validation_trace="[]",
                    publication_state="published",
                    publication_token=None,
                    publication_lease_expires_at=None,
                    retention_token=None,
                    retention_lease_expires_at=None,
                )
            )
        database.flush()
        template.current_version_id = str(versions[-1])
        for audit_number in range(2):
            database.add(
                TemplateAuditRow(
                    id=str(UUID(int=300 + audit_number)),
                    actor_id=str(owner_id),
                    owner_id=str(owner_id),
                    template_id=str(template_id),
                    operation="replace",
                    version_id=None,
                    administrator_intervention=False,
                    created_at=now - timedelta(days=400, seconds=audit_number),
                )
            )

    objects = FilesystemObjectStore(tmp_path)
    for version_id in versions:
        objects.put(ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, version_id), b"x")
    service = RetentionService(
        SqlRetentionRepository(engine),
        objects,
        DataRetentionPolicy(365 * 24 * 60 * 60, 365 * 24 * 60 * 60, 10, 30),
        clock=lambda: now,
    )

    assert service.cleanup(limit=1) == 2
    assert objects.exists(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, versions[0])
    )
    assert not objects.exists(
        ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, versions[1])
    )
    assert all(
        objects.exists(ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, version_id))
        for version_id in versions[2:]
    )
    with DatabaseSession(engine) as database:
        remaining = set(database.scalars(select(TemplateVersionRow.id)))
        assert str(versions[1]) not in remaining
        assert len(tuple(database.scalars(select(TemplateAuditRow.id)))) == 1
        reports = tuple(database.scalars(select(RetentionCleanupRunRow)))
        assert {(report.kind, report.removed_count) for report in reports} == {
            ("template_version", 1),
            ("audit", 1),
        }
    with (
        pytest.raises(IntegrityError),
        DatabaseSession(engine) as database,
        database.begin(),
    ):
        database.execute(update(TemplateAuditRow).values(operation="rewritten"))
    with (
        pytest.raises(IntegrityError),
        DatabaseSession(engine) as database,
        database.begin(),
    ):
        database.execute(update(RetentionCleanupRunRow).values(removed_count=999))
    with (
        pytest.raises(IntegrityError),
        DatabaseSession(engine) as database,
        database.begin(),
    ):
        database.execute(delete(RetentionCleanupRunRow))
    engine.dispose()


@pytest.mark.integration
def test_sqlite_cleanup_evidence_trigger_has_a_real_downgrade_path(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    report_id = str(UUID(int=999))
    now = datetime(2026, 8, 24, tzinfo=UTC)
    with DatabaseSession(engine) as database, database.begin():
        database.add(
            RetentionCleanupRunRow(
                id=report_id,
                kind="audit",
                cutoff_at=now,
                removed_count=0,
                completed_at=now,
            )
        )
    with (
        pytest.raises(IntegrityError),
        DatabaseSession(engine) as database,
        database.begin(),
    ):
        database.execute(delete(RetentionCleanupRunRow))

    downgrade_database(engine, "20260824_08")

    with DatabaseSession(engine) as database, database.begin():
        result = database.execute(delete(RetentionCleanupRunRow))
        assert getattr(result, "rowcount", 0) == 1
    engine.dispose()
