"""Fast in-process branch coverage for T18 transactional admission."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from md_converter.auth.models import Role, User
from md_converter.jobs.errors import (
    JobQueueCapacityExceededError,
    JobUserQuotaExceededError,
)
from md_converter.jobs.policy import JobAdmissionPolicy
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import SqlUserRepository, create_database_engine
from tests.job_repository_contracts import LEASE_END, NOW, submission
from tests.template_records import publish_template_pair

pytestmark = pytest.mark.unit


def _repository(
    policy: JobAdmissionPolicy,
) -> tuple[Engine, SqlJobRepository, UUID, UUID]:
    engine = create_database_engine("sqlite+pysqlite://")
    upgrade_database(engine)
    owner_id = uuid4()
    other_id = uuid4()
    users = SqlUserRepository(engine)
    users.create(User(owner_id, "Owner", f"owner-{owner_id}", "hash", Role.USER))
    users.create(User(other_id, "Other", f"other-{other_id}", "hash", Role.USER))
    publish_template_pair(
        engine,
        owner_id,
        submission(owner_id).template_id,
        submission(owner_id).template_version_id,
    )
    return engine, SqlJobRepository(engine, policy), owner_id, other_id


def test_owner_quota_allows_exact_idempotent_replay() -> None:
    engine, repository, owner_id, _other_id = _repository(JobAdmissionPolicy(1, 10))
    first, replayed = repository.create(
        submission(owner_id, idempotency_digest="a" * 64)
    )
    assert not replayed
    replay, replayed = repository.create(
        submission(owner_id, idempotency_digest="a" * 64)
    )
    assert replayed and replay.id == first.id
    with pytest.raises(JobUserQuotaExceededError):
        repository.create(submission(owner_id))
    engine.dispose()


def test_global_capacity_rejects_another_owner() -> None:
    engine, repository, owner_id, other_id = _repository(JobAdmissionPolicy(2, 1))
    queued, _ = repository.create(submission(owner_id))
    repository.activate_source(queued.id, NOW)
    assert repository.claim("active-capacity", NOW, LEASE_END) is not None
    with pytest.raises(JobQueueCapacityExceededError):
        repository.create(submission(other_id))
    engine.dispose()
