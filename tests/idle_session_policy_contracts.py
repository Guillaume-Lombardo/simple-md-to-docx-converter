"""Shared cross-profile idle-session policy persistence contract."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError

from markweave.auth.models import (
    IdleSessionPolicy,
    IdleSessionPolicyAudit,
    IdleSessionPolicyOperation,
)
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.schema import IdleSessionPolicyAuditRow
from markweave.persistence.sql import SqlIdleSessionPolicyRepository


def exercise_idle_session_policy_repository_contract(engine: Engine) -> None:
    """Prove defaulting, atomic first write, audit, persistence, and immutability."""
    repository = SqlIdleSessionPolicyRepository(engine)
    assert repository.get() == IdleSessionPolicy()

    actor = uuid4()

    def update_policy(minutes: int) -> IdleSessionPolicy | None:
        return repository.update(
            IdleSessionPolicy(minutes, 5),
            expected_revision=0,
            audit=IdleSessionPolicyAudit(
                uuid4(),
                actor,
                IdleSessionPolicyOperation.UPDATE,
                30,
                15,
                minutes,
                5,
                1,
                datetime.now(UTC),
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(update_policy, (5, 300)))
    assert sum(outcome is not None for outcome in outcomes) == 1
    persisted = SqlIdleSessionPolicyRepository(engine).get()
    assert persisted.revision == 1
    assert persisted.user_idle_minutes in {5, 300}
    assert persisted.admin_idle_minutes == 5

    with engine.connect() as connection:
        audits = tuple(
            connection.execute(
                select(
                    IdleSessionPolicyAuditRow.id,
                    IdleSessionPolicyAuditRow.actor_id,
                    IdleSessionPolicyAuditRow.old_user_idle_minutes,
                    IdleSessionPolicyAuditRow.old_admin_idle_minutes,
                    IdleSessionPolicyAuditRow.revision,
                )
            )
        )
    assert len(audits) == 1
    audit = audits[0]
    assert audit.actor_id == str(actor)
    assert (audit.old_user_idle_minutes, audit.old_admin_idle_minutes) == (30, 15)
    assert audit.revision == 1

    with pytest.raises(SQLAlchemyError), engine.begin() as connection:
        connection.execute(
            update(IdleSessionPolicyAuditRow)
            .where(IdleSessionPolicyAuditRow.id == audit.id)
            .values(new_admin_idle_minutes=6)
        )


def exercise_idle_session_policy_audit_retention_contract(engine: Engine) -> None:
    """Prove policy audits participate in guarded retention on either SQL profile."""
    repository = SqlIdleSessionPolicyRepository(engine)
    current = repository.get()
    audit_id = uuid4()
    created_at = datetime(2020, 1, 1, tzinfo=UTC)
    proposed_user = 5 if current.user_idle_minutes != 5 else 6
    assert (
        repository.update(
            IdleSessionPolicy(proposed_user, 5),
            expected_revision=current.revision,
            audit=IdleSessionPolicyAudit(
                audit_id,
                uuid4(),
                IdleSessionPolicyOperation.UPDATE,
                current.user_idle_minutes,
                current.admin_idle_minutes,
                proposed_user,
                5,
                current.revision + 1,
                created_at,
            ),
        )
        is not None
    )
    removed = SqlRetentionRepository(engine).cleanup_audits(
        cutoff_at=created_at + timedelta(days=1),
        completed_at=created_at + timedelta(days=2),
        limit=10_000,
    )
    assert removed >= 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(IdleSessionPolicyAuditRow.id).where(
                    IdleSessionPolicyAuditRow.id == str(audit_id)
                )
            )
            is None
        )
