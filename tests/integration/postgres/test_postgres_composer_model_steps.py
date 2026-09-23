"""PostgreSQL/S3 model-step admission across independent repository instances."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.revisions import ComposerConflictError
from markweave.persistence.composer import (
    ComposerCapacityError,
    SqlComposerAuditRepository,
    SqlComposerModelStepRepository,
    SqlComposerRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import SqlAuditReader
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerContentAuditRow,
    ComposerCredentialRow,
    ComposerProposalRow,
    RetentionCleanupRunRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from tests.integration.postgres.test_postgres_composer_foundations import _store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def test_postgres_global_admission_and_cancel_fence_with_s3_drafts(  # noqa: PLR0915 - full PG/S3 boundary lifecycle
) -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    objects = _store()
    owner, first_connection, second_connection = uuid4(), uuid4(), uuid4()
    try:
        with Session(engine) as database, database.begin():
            database.add(
                UserRow(
                    id=str(owner),
                    username=str(owner),
                    normalized_username=str(owner),
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role="user",
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
            database.flush()
            for connection_id in (first_connection, second_connection):
                database.add(
                    ComposerConnectionRow(
                        id=str(connection_id),
                        scope="instance",
                        owner_id=None,
                        identity_mode="shared",
                        name="Test",
                        endpoint="https://model.internal/v1",
                        selected_model="approved-model",
                        permitted_models='["approved-model"]',
                        enabled=True,
                        version=1,
                        generation=1,
                        outage=False,
                    )
                )
            database.flush()
            for connection_id in (first_connection, second_connection):
                database.add(
                    ComposerConnectionGrantRow(
                        connection_id=str(connection_id), user_id=str(owner)
                    )
                )
                database.add(
                    ComposerCredentialRow(
                        id=str(uuid4()),
                        connection_id=str(connection_id),
                        user_id=None,
                        api_key=b"encrypted-key",
                        client_certificate=None,
                        client_private_key=None,
                        ca_bundle=None,
                        outage=False,
                    )
                )
        drafts = SqlComposerRepository(engine, objects)
        first = drafts.create_draft_with_source(
            owner,
            b"first",
            "scanner-approved",
            title="First",
            content="",
            media_type="text/markdown",
        )
        second = drafts.create_draft_with_source(
            owner,
            b"second",
            "scanner-approved",
            title="Second",
            content="",
            media_type="text/markdown",
        )

        def admit(connection_id, draft, key):
            repository = SqlComposerModelStepRepository(engine)
            try:
                step, created = repository.start_model_step(
                    owner,
                    draft.id,
                    connection_id=connection_id,
                    approved_endpoint="https://model.internal/v1",
                    approved_model="approved-model",
                    if_match=draft.etag,
                    idempotency_key=key,
                    payload_digest=hashlib.sha256(key.encode()).hexdigest(),
                    max_active=1,
                    lease=timedelta(minutes=5),
                )
                assert created
                return step
            except ComposerCapacityError, ComposerConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                pool.map(
                    lambda args: admit(*args),
                    (
                        (first_connection, first, "first"),
                        (second_connection, second, "second"),
                    ),
                )
            )
        assert sum(step is not None for step in results) == 1
        admitted = results[0] or results[1]
        assert admitted is not None
        restarted = SqlComposerModelStepRepository(engine)
        assert (
            restarted.cancel_model_step(owner, admitted.draft_id, admitted.id).state
            == "cancelled"
        )
        step_audit = tuple(
            record
            for record in SqlAuditReader(engine).list_recent(offset=0, limit=100)
            if record.target_id == admitted.id
        )
        assert {record.operation for record in step_audit} == {
            "model_step_start",
            "model_step_cancel",
        }
        assert all(record.owner_id == owner for record in step_audit)
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                text("DELETE FROM composer_content_audit WHERE target_id = :id"),
                {"id": str(admitted.id)},
            )
        audit_repository = SqlComposerAuditRepository(engine)
        assert (
            audit_repository.cleanup_content_audit(
                cutoff_at=datetime.now(UTC) + timedelta(days=1), limit=1
            )
            == 1
        )
        with Session(engine) as database:
            assert (
                database.scalar(
                    select(RetentionCleanupRunRow.id).where(
                        RetentionCleanupRunRow.kind == "composer_content_audit"
                    )
                )
                is not None
            )
            assert database.scalar(select(ComposerContentAuditRow.id)) is not None
        with pytest.raises(ComposerConflictError):
            restarted.finish_model_step(
                owner,
                admitted.draft_id,
                admitted.id,
                proposed_value="late",
                provenance="model",
            )

        abandoned_draft = second if admitted.draft_id == first.id else first
        now = [datetime.now(UTC)]
        expirations: list[int] = []
        recoveries: list[int] = []
        periodic = SqlComposerModelStepRepository(
            engine,
            clock=lambda: now[0],
            on_expiration=expirations.append,
            on_recovery=recoveries.append,
        )
        abandoned, created = periodic.start_model_step(
            owner,
            abandoned_draft.id,
            connection_id=second_connection,
            approved_endpoint="https://model.internal/v1",
            approved_model="approved-model",
            if_match=abandoned_draft.etag,
            idempotency_key="abandoned-after-restart",
            payload_digest=hashlib.sha256(b"abandoned-after-restart").hexdigest(),
            max_active=1,
            lease=timedelta(minutes=1),
        )
        assert created
        assert periodic.recover_stale_model_steps(stale_before=now[0], limit=1) == 0
        now[0] += timedelta(minutes=2)
        assert periodic.recover_stale_model_steps(stale_before=now[0], limit=1) == 1
        assert periodic.recover_stale_model_steps(stale_before=now[0], limit=1) == 0
        assert expirations == recoveries == [1]
        assert (
            periodic.get_model_step(owner, abandoned_draft.id, abandoned.id).state
            == "failed"
        )
        with pytest.raises(ComposerConflictError, match="no longer active"):
            periodic.finish_model_step(
                owner,
                abandoned_draft.id,
                abandoned.id,
                proposed_value="late",
                provenance="model",
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        objects.close()
        engine.dispose()
