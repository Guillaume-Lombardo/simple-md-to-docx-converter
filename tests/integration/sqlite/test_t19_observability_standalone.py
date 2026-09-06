"""Real SQLite observability and immutable-audit query coverage."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError

from markweave.auth.models import Role, User
from markweave.broker.models import AuthenticatedPrincipal
from markweave.jobs.errors import JobRepositoryError
from markweave.jobs.models import JobOutput, JobRequest
from markweave.jobs.service import JobService, JobServicePolicy
from markweave.persistence.errors import PersistenceError
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import (
    SqlAuditReader,
    SqlOperationalObserver,
)
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.schema import AuthenticationAuditRow, TemplateAuditRow
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.storage import FilesystemObjectStore
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    POLICY_SPECIFICATION,
    PRINCIPAL,
    complete_empty_reconciliation,
    proof,
    submission,
)
from tests.template_records import publish_template_pair

pytestmark = pytest.mark.integration


def test_sqlite_queue_metrics_correlation_and_audit_are_content_free(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    template_id, version_id = uuid4(), uuid4()
    publish_template_pair(engine, owner.id, template_id, version_id)
    repository = SqlJobRepository(engine)
    service = JobService(
        repository, FilesystemObjectStore(tmp_path), JobServicePolicy(3_600)
    )
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)

    first, _ = service.submit(
        JobRequest(
            owner.id,
            b"# private markdown",
            template_id,
            version_id,
            JobOutput.DOCX,
            (("md-converter", "0.1.0"),),
            now,
            "request-standalone",
        ),
        None,
    )
    service.submit(
        JobRequest(
            owner.id,
            b"# other private markdown",
            template_id,
            version_id,
            JobOutput.PDF,
            (("md-converter", "0.1.0"),),
            now + timedelta(seconds=2),
            "request-second",
        ),
        None,
    )
    snapshot = SqlOperationalObserver(engine).observe_queue(now + timedelta(seconds=10))
    assert (snapshot.depth, snapshot.oldest_age_seconds, snapshot.active_jobs) == (
        2,
        10.0,
        0,
    )
    claimed = repository.claim(
        "observer-worker", now + timedelta(seconds=11), now + timedelta(seconds=41)
    )
    assert claimed is not None and claimed.id == first.id
    assert claimed.correlation_id == "request-standalone"
    running = SqlOperationalObserver(engine).observe_queue(now + timedelta(seconds=12))
    assert (running.depth, running.active_jobs) == (1, 1)

    audit = SqlAuditReader(engine).list_recent(offset=0, limit=10)
    assert len(audit) == 0
    with engine.begin() as connection:
        connection.execute(
            insert(TemplateAuditRow).values(
                id=str(uuid4()),
                actor_id=str(owner.id),
                owner_id=str(owner.id),
                template_id=str(template_id),
                operation="replace",
                version_id=str(version_id),
                administrator_intervention=False,
                created_at=now,
            )
        )
    record = SqlAuditReader(engine).list_recent(offset=0, limit=1)[0]
    assert record.operation == "replace"
    assert record.target_id == template_id
    assert record.version_id == version_id
    assert not hasattr(record, "content")
    assert not hasattr(record, "filename")
    engine.dispose()


def test_sqlite_observation_failure_is_sanitized() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(JobRepositoryError):
        SqlOperationalObserver(engine).observe_queue(datetime.now(UTC))
    with pytest.raises(PersistenceError):
        SqlAuditReader(engine).list_recent(offset=0, limit=10)
    with pytest.raises(ValueError, match="pagination"):
        SqlAuditReader(engine).list_recent(offset=-1, limit=10)
    engine.dispose()


def test_sqlite_reverse_queue_and_proof_backlogs_are_content_free(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:owner", Role.USER)
    SqlUserRepository(engine).create(owner)
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    queued, _ = repository.create(submission(owner.id))
    repository.activate_source(queued.id, NOW)

    observed = SqlOperationalObserver(engine).observe_queue(NOW + timedelta(seconds=5))
    assert observed.reversion_depth == 1
    assert observed.reversion_oldest_age_seconds == 5
    assert observed.reversion_active_jobs == 0
    assert observed.shared_capacity_used == 1
    assert observed.reversion_reconciliation_pending == 0

    claimed = repository.claim("reverse-observer", PRINCIPAL, NOW, LEASE_END)
    assert (
        claimed is not None
        and claimed.current_attempt_id is not None
        and claimed.lease_token is not None
    )
    attempt = repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "reverse-observer",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW,
    )
    expired = SqlOperationalObserver(engine).observe_queue(
        LEASE_END + timedelta(microseconds=1)
    )
    assert expired.reversion_depth == 0
    assert expired.reversion_active_jobs == 1
    assert expired.reversion_proof_blocked_attempts == 1

    unit_id = uuid4()
    repository.record_broker_unit(
        claimed.id,
        attempt.attempt_id,
        "reverse-observer",
        claimed.lease_token,
        unit_id,
        NOW,
    )
    repository.record_active_termination_proof(
        claimed.id,
        attempt.attempt_id,
        "reverse-observer",
        claimed.lease_token,
        proof(attempt.attempt_id, unit_id),
        NOW,
    )
    proof_pending = SqlOperationalObserver(engine).observe_queue(LEASE_END)
    assert proof_pending.reversion_proof_blocked_attempts == 0
    assert proof_pending.reversion_proof_ack_backlog == 1

    other_principal = AuthenticatedPrincipal(uuid4())
    repository.begin_reconciliation(
        other_principal, "pending-reconciler", uuid4(), NOW, LEASE_END
    )
    reconciling = SqlOperationalObserver(engine).observe_queue(NOW)
    assert reconciling.reversion_reconciliation_pending == 1
    engine.dispose()


def test_sqlite_authentication_audit_shares_bounded_retention_order(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    now = datetime(2026, 8, 24, 20, tzinfo=UTC)
    stable_id = uuid4()
    old_authentication_id = uuid4()
    template_audit_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            insert(AuthenticationAuditRow),
            [
                {
                    "id": str(old_authentication_id),
                    "actor_id": str(stable_id),
                    "owner_id": str(stable_id),
                    "operation": "user_create",
                    "target_id": str(stable_id),
                    "auth_version": 0,
                    "administrator_intervention": True,
                    "created_at": now - timedelta(days=3),
                },
                {
                    "id": str(uuid4()),
                    "actor_id": str(stable_id),
                    "owner_id": str(stable_id),
                    "operation": "user_password_reset",
                    "target_id": str(stable_id),
                    "auth_version": 1,
                    "administrator_intervention": True,
                    "created_at": now,
                },
            ],
        )
        connection.execute(
            insert(TemplateAuditRow).values(
                id=str(template_audit_id),
                actor_id=str(stable_id),
                owner_id=str(stable_id),
                template_id=str(uuid4()),
                operation="replace",
                version_id=None,
                administrator_intervention=False,
                created_at=now - timedelta(days=2),
            )
        )

    for row_type, identifier in (
        (AuthenticationAuditRow, old_authentication_id),
        (TemplateAuditRow, template_audit_id),
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(delete(row_type).where(row_type.id == str(identifier)))

    retention = SqlRetentionRepository(engine)
    assert (
        retention.cleanup_audits(
            cutoff_at=now - timedelta(days=1), completed_at=now, limit=1
        )
        == 1
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(AuthenticationAuditRow))
            == 1
        )
        assert (
            connection.scalar(select(func.count()).select_from(TemplateAuditRow)) == 1
        )
    assert (
        retention.cleanup_audits(
            cutoff_at=now - timedelta(days=1), completed_at=now, limit=10
        )
        == 1
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(AuthenticationAuditRow))
            == 1
        )
        assert (
            connection.scalar(select(func.count()).select_from(TemplateAuditRow)) == 0
        )
    engine.dispose()
