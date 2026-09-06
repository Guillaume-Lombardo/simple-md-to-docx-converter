"""Real PostgreSQL reverse queue and mixed-family admission coverage."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import inspect

from markweave.auth.models import Role, User
from markweave.jobs.models import JobOutput, JobSubmission
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.sql import SqlUserRepository, create_database_engine
from markweave.reversion_jobs.models import ReversionJob
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    PRINCIPAL,
    RETENTION_END,
    exercise_reversion_job_repository_contract,
    submission,
)


def _user(repository: SqlUserRepository, name: str) -> User:
    user = User(uuid4(), name, f"{name.casefold()}-{uuid4()}", "hash:user", Role.USER)
    repository.create(user)
    return user


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_reversion_repository_contract() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "ReverseOwner")
    other = _user(users, "ReverseOther")
    exercise_reversion_job_repository_contract(
        SqlReversionJobRepository(engine), owner.id, other.id
    )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_reversion_migration_round_trip() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    assert "reversion_attempts" in inspect(engine).get_table_names()
    try:
        downgrade_database(engine, "20260901_15")
        assert "reversion_attempts" not in inspect(engine).get_table_names()
    finally:
        upgrade_database(engine)
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_claims_allocate_unique_principal_sequences() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    repository = SqlReversionJobRepository(engine)
    for name in ("SequenceOne", "SequenceTwo"):
        owner = _user(users, name)
        job, _ = repository.create(submission(owner.id))
        repository.activate_source(job.id, NOW)
    principal = type(PRINCIPAL)(uuid4())
    barrier = Barrier(2)

    def claim(worker: str) -> ReversionJob | None:
        barrier.wait()
        return repository.claim(worker, principal, NOW, LEASE_END)

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = tuple(executor.map(claim, ("sequence-a", "sequence-b")))
    claimed_jobs = tuple(job for job in jobs if job is not None)
    assert len(claimed_jobs) == 2
    attempt_ids = [
        job.current_attempt_id
        for job in claimed_jobs
        if job.current_attempt_id is not None
    ]
    assert len(attempt_ids) == 2
    attempts = [repository.get_attempt(attempt_id) for attempt_id in attempt_ids]
    assert sorted(attempt.create_sequence for attempt in attempts if attempt) == [1, 2]
    for job in claimed_jobs:
        assert job.current_attempt_id is not None
        assert job.lease_token is not None
        repository.finish_cancelled(
            job.id,
            job.current_attempt_id,
            job.lease_owner or "",
            job.lease_token,
            NOW,
            RETENTION_END,
        )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_mixed_family_global_capacity_is_atomic() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    forward_owner = _user(users, "MixedForward")
    reverse_owner = _user(users, "MixedReverse")
    active_capacity = 1
    forward = SqlJobRepository(engine, JobAdmissionPolicy(10, active_capacity))
    reverse = SqlReversionJobRepository(
        engine, ReversionAdmissionPolicy(10, active_capacity)
    )
    barrier = Barrier(2)

    def create_forward() -> str:
        barrier.wait()
        forward.create(
            JobSubmission(
                uuid4(),
                forward_owner.id,
                uuid4(),
                None,
                None,
                JobOutput.DOCX,
                (("markweave", "0.6.1"),),
                "8" * 64,
                None,
                NOW,
            )
        )
        return "forward"

    def create_reverse() -> str:
        barrier.wait()
        reverse.create(submission(reverse_owner.id))
        return "reverse"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_forward), executor.submit(create_reverse)]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as error:
                errors.append(error)
    assert len(results) == 1 and len(errors) == 1
    engine.dispose()
