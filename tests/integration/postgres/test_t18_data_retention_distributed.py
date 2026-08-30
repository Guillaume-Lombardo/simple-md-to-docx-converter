"""PostgreSQL/RustFS parity for approved data-retention cleanup."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import boto3
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.migrations import upgrade_database
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.schema import (
    RetentionCleanupRunRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.retention import DataRetentionPolicy, RetentionService
from markweave.storage import ObjectKey, ObjectScope, S3ObjectStore


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_retention_matches_standalone_contract() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        region_name=os.environ["MARKWEAVE_TEST_S3_REGION"],
        aws_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
    )
    objects = S3ObjectStore(client, os.environ["MARKWEAVE_TEST_S3_BUCKET"])
    owner_id, template_id = uuid4(), uuid4()
    versions = [uuid4() for _ in range(12)]
    now = datetime.now(UTC)
    try:
        with DatabaseSession(engine) as database, database.begin():
            database.add(
                UserRow(
                    id=str(owner_id),
                    username=f"retention-{owner_id}",
                    normalized_username=f"retention-{owner_id}",
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
                normalized_name=f"retained-{template_id}",
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
        for version_id in versions:
            objects.put(
                ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, version_id), b"x"
            )

        removed = RetentionService(
            SqlRetentionRepository(engine),
            objects,
            DataRetentionPolicy(365 * 86_400, 365 * 86_400, 10, 30),
            clock=lambda: now,
        ).cleanup(limit=1)
        assert removed == 2
        assert objects.exists(
            ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, versions[0])
        )
        assert not objects.exists(
            ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, versions[1])
        )
        with DatabaseSession(engine) as database:
            assert database.get(TemplateVersionRow, str(versions[1])) is None
            assert (
                database.scalar(
                    select(TemplateAuditRow.id).where(
                        TemplateAuditRow.template_id == str(template_id)
                    )
                )
                is None
            )
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
            database.execute(
                delete(RetentionCleanupRunRow).where(
                    RetentionCleanupRunRow.completed_at == now
                )
            )
    finally:
        for version_id in versions:
            objects.delete(
                ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, version_id)
            )
        with DatabaseSession(engine) as database, database.begin():
            database.execute(
                delete(TemplateRow).where(TemplateRow.id == str(template_id))
            )
            database.execute(delete(UserRow).where(UserRow.id == str(owner_id)))
        engine.dispose()
