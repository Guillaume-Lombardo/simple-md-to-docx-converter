"""Real PostgreSQL reverse queue and mixed-family admission coverage."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text

from markweave.auth.models import Role, User
from markweave.broker.reconciliation_protocol import (
    ReconciliationResponse,
    ReconciliationTombstone,
)
from markweave.jobs.models import JobOutput, JobSubmission
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.sql import SqlUserRepository, create_database_engine
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
)
from markweave.reversion_jobs.models import ReversionJob
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    POLICY_SPECIFICATION,
    PRINCIPAL,
    RETENTION_END,
    exercise_reversion_job_repository_contract,
    proof,
    submission,
)


class _RecoveryRaceRepository(SqlReversionJobRepository):
    def __init__(self, engine: Engine, barrier: Barrier) -> None:
        super().__init__(engine)
        self._barrier = barrier

    def _before_recovery_proof_cas(self) -> None:
        self._barrier.wait()


class _IdempotencyRaceRepository(SqlReversionJobRepository):
    def __init__(self, engine: Engine, barrier: Barrier) -> None:
        super().__init__(engine)
        self._barrier = barrier
        self.collision_count = 0

    def _after_idempotency_miss(self) -> None:
        self._barrier.wait()

    def _after_idempotency_collision(self) -> None:
        self.collision_count += 1


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
def test_postgresql_reconciliation_advances_hwm_and_retains_orphan_before_ack() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    repository = SqlReversionJobRepository(engine)
    token = uuid4()
    repository.begin_reconciliation(
        PRINCIPAL, "postgres-reconciler", token, NOW, LEASE_END
    )
    recovered = proof(uuid4(), uuid4())
    tombstone = ReconciliationTombstone(12, POLICY_SPECIFICATION, recovered)
    repository.record_reconciliation_page(
        PRINCIPAL,
        token,
        ReconciliationResponse(
            uuid4(), PRINCIPAL.principal_id, 0, 15, tombstone, False
        ),
        NOW,
    )
    repository.record_reconciliation_page(
        PRINCIPAL,
        token,
        ReconciliationResponse(uuid4(), PRINCIPAL.principal_id, 12, 14, None, True),
        NOW,
    )
    assert repository.pending_reconciliation_acknowledgements(
        PRINCIPAL, token, NOW
    ) == (tombstone,)
    repository.mark_reconciliation_acknowledged(PRINCIPAL, token, tombstone, NOW)
    repository.record_reconciliation_page(
        PRINCIPAL,
        token,
        ReconciliationResponse(uuid4(), PRINCIPAL.principal_id, 12, 14, None, True),
        NOW,
    )
    repository.complete_reconciliation(PRINCIPAL, token, NOW)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT create_sequence_high_water FROM reversion_broker_principals WHERE principal_id = :principal"
                ),
                {"principal": str(PRINCIPAL.principal_id)},
            ).scalar_one()
            == 15
        )
        assert (
            connection.execute(
                text(
                    "SELECT acknowledged_at FROM reversion_orphan_proofs WHERE principal_id = :principal AND create_sequence = 12"
                ),
                {"principal": str(PRINCIPAL.principal_id)},
            ).scalar_one()
            is not None
        )
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
    assert len(claimed_jobs) == 1
    first = claimed_jobs[0]
    assert first.current_attempt_id is not None and first.lease_token is not None
    first_attempt = repository.get_attempt(first.current_attempt_id)
    assert first_attempt is not None and first_attempt.create_sequence == 1
    assert repository.claim("sequence-blocked", principal, NOW, LEASE_END) is None
    intent = repository.reserve_create_intent(
        first.id,
        first_attempt.attempt_id,
        first.lease_owner or "",
        first.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW,
    )
    assert (
        repository.reserve_create_intent(
            first.id,
            first_attempt.attempt_id,
            first.lease_owner or "",
            first.lease_token,
            "reverse-policy-v1",
            POLICY_SPECIFICATION,
            NOW,
        )
        == intent
    )
    assert repository.claim("sequence-still-blocked", principal, NOW, LEASE_END) is None
    unit_id = uuid4()
    repository.record_broker_unit(
        first.id,
        first_attempt.attempt_id,
        first.lease_owner or "",
        first.lease_token,
        unit_id,
        NOW,
    )
    second = repository.claim("sequence-next", principal, NOW, LEASE_END)
    assert second is not None
    assert second.current_attempt_id is not None and second.lease_token is not None
    second_attempt = repository.get_attempt(second.current_attempt_id)
    assert second_attempt is not None and second_attempt.create_sequence == 2
    repository.record_active_termination_proof(
        first.id,
        first_attempt.attempt_id,
        first.lease_owner or "",
        first.lease_token,
        proof(first_attempt.attempt_id, unit_id, principal),
        NOW,
    )
    repository.finish_cancelled(
        first.id,
        first_attempt.attempt_id,
        first.lease_owner or "",
        first.lease_token,
        NOW,
        RETENTION_END,
    )
    repository.finish_cancelled(
        second.id,
        second_attempt.attempt_id,
        second.lease_owner or "",
        second.lease_token,
        NOW,
        RETENTION_END,
    )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_recovery_proof_is_a_single_exact_cas() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "RecoveryProofRace")
    repository = SqlReversionJobRepository(engine)
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    principal = type(PRINCIPAL)(uuid4())
    claimed = repository.claim("proof-owner", principal, NOW, LEASE_END)
    assert claimed is not None
    assert claimed.current_attempt_id is not None and claimed.lease_token is not None
    repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "proof-owner",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW,
    )
    recovery_now = LEASE_END.replace(microsecond=1)
    recoveries = repository.claim_recovery(
        "proof-recovery", recovery_now, RETENTION_END, 1
    )
    assert len(recoveries) == 1 and recoveries[0].recovery_token is not None
    recovery = recoveries[0]
    recovery_token = recovery.recovery_token
    assert recovery_token is not None
    competing = (
        proof(recovery.attempt_id, uuid4(), principal),
        proof(recovery.attempt_id, uuid4(), principal),
    )
    barrier = Barrier(2)
    race_repository = _RecoveryRaceRepository(engine, barrier)

    def record(candidate_index: int) -> object:
        return race_repository.record_recovery_termination_proof(
            claimed.id,
            recovery.attempt_id,
            recovery_token,
            competing[candidate_index],
            recovery_now,
        )

    successes: list[object] = []
    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(record, index) for index in range(2)]
        for future in futures:
            try:
                successes.append(future.result())
            except Exception as error:
                failures.append(error)
    assert len(successes) == 1
    assert len(failures) == 1 and isinstance(failures[0], ReversionJobConflictError)
    persisted = repository.get_attempt(recovery.attempt_id)
    assert persisted is not None
    assert persisted.termination_proof in competing
    assert persisted.proof_recovery_token == recovery_token
    replayed = repository.record_recovery_termination_proof(
        claimed.id,
        recovery.attempt_id,
        recovery_token,
        persisted.termination_proof,
        recovery_now,
    )
    assert replayed == persisted
    conflicting = next(
        candidate for candidate in competing if candidate != persisted.termination_proof
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_recovery_termination_proof(
            claimed.id,
            recovery.attempt_id,
            recovery_token,
            conflicting,
            recovery_now,
        )
    with pytest.raises(ReversionJobConflictError):
        repository.record_recovery_termination_proof(
            claimed.id,
            recovery.attempt_id,
            uuid4(),
            persisted.termination_proof,
            recovery_now,
        )
    assert repository.recover_expired_leases(recovery_now, RETENTION_END, NOW) == 1
    repository.request_cancel(claimed.id, owner.id, recovery_now, RETENTION_END)
    engine.dispose()


@pytest.mark.integration
@pytest.mark.requires_postgres
def test_postgresql_conflicting_idempotent_submissions_never_replay() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    owner = _user(SqlUserRepository(engine), "IdempotencyRace")
    barrier = Barrier(2)
    repository = _IdempotencyRaceRepository(engine, barrier)
    key = "9" * 64

    def create(request_digest: str) -> object:
        return repository.create(
            submission(
                owner.id,
                idempotency_digest=key,
                request_digest=request_digest,
            )
        )

    successes: list[object] = []
    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create, digest * 64) for digest in ("a", "b")]
        for future in futures:
            try:
                successes.append(future.result())
            except Exception as error:
                failures.append(error)
    assert len(successes) == 1
    assert len(failures) == 1 and isinstance(failures[0], ReversionJobConflictError)
    assert repository.collision_count == 1
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
