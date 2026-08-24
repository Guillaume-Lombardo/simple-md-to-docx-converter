"""Bounded retention orchestration tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session as DatabaseSession

from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.retention import SqlRetentionRepository
from md_converter.persistence.schema import (
    RetentionCleanupRunRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from md_converter.persistence.sql import create_database_engine
from md_converter.retention import (
    DataRetentionPolicy,
    RetentionClaim,
    RetentionRepository,
    RetentionService,
)
from md_converter.storage import ObjectStore, ObjectStoreError
from md_converter.templates.models import TemplateVersion


@pytest.mark.unit
def test_object_failure_leaves_fenced_template_claim_retryable(
    mocker: MockerFixture,
) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    version = TemplateVersion(
        UUID(int=1), UUID(int=2), 1, UUID(int=3), "0" * 64, 1, now, UUID(int=3)
    )
    repository = mocker.Mock(spec=RetentionRepository)
    repository.claim_template_versions.return_value = (
        RetentionClaim(version, UUID(int=4)),
    )
    objects = mocker.Mock(spec=ObjectStore)
    objects.delete.side_effect = ObjectStoreError
    service = RetentionService(
        repository,
        objects,
        DataRetentionPolicy(1, 1, 10, 1),
        clock=lambda: now,
    )
    with pytest.raises(ObjectStoreError):
        service.cleanup(limit=1)
    repository.complete_template_version.assert_not_called()
    repository.cleanup_audits.assert_not_called()
    with pytest.raises(ValueError, match="Cleanup limit"):
        service.cleanup(limit=0)
    for values in ((0, 1, 10, 1), (1, 0, 10, 1), (1, 1, 9, 1), (1, 1, 10, 0)):
        with pytest.raises(ValueError, match="policy"):
            DataRetentionPolicy(*values)


@pytest.mark.unit
def test_inprocess_sql_retention_claims_and_completes_bounded_work(
    mocker: MockerFixture,
) -> None:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    now = datetime(2026, 8, 24, tzinfo=UTC)
    owner_id, template_id = UUID(int=10), UUID(int=20)
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
        database.add(
            TemplateAuditRow(
                id=str(uuid4()),
                actor_id=str(owner_id),
                owner_id=str(owner_id),
                template_id=str(template_id),
                operation="replace",
                version_id=None,
                administrator_intervention=False,
                created_at=now - timedelta(days=400),
            )
        )
    objects = mocker.Mock(spec=ObjectStore)
    service = RetentionService(
        SqlRetentionRepository(engine),
        objects,
        DataRetentionPolicy(365 * 86_400, 365 * 86_400, 10, 30),
        clock=lambda: now,
    )
    assert service.cleanup(limit=1) == 2
    objects.delete.assert_called_once()
    with DatabaseSession(engine) as database:
        assert database.get(TemplateVersionRow, str(versions[1])) is None
        assert database.get(TemplateVersionRow, str(versions[0])) is not None
        assert database.scalar(select(TemplateAuditRow.id)) is None
        assert len(tuple(database.scalars(select(RetentionCleanupRunRow.id)))) == 2

    repository = SqlRetentionRepository(engine)
    with pytest.raises(ValueError, match="claim limits"):
        repository.claim_template_versions(
            cutoff_at=now,
            now=now,
            lease_expires_at=now,
            minimum_versions=9,
            limit=0,
        )
    with pytest.raises(ValueError, match="Cleanup limit"):
        repository.cleanup_audits(cutoff_at=now, completed_at=now, limit=0)
    stale = RetentionClaim(
        TemplateVersion(
            versions[0],
            template_id,
            1,
            owner_id,
            "0" * 64,
            1,
            now - timedelta(days=400),
            owner_id,
        ),
        uuid4(),
    )
    assert not repository.complete_template_version(stale, completed_at=now)
    engine.dispose()
