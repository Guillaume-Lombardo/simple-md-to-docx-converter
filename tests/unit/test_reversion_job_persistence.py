"""In-process persistence coverage for the durable reverse queue."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

from markweave.auth.models import Role, User
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.reversion_jobs.common import _trace, _versions
from markweave.persistence.schema import Base
from markweave.persistence.sql import SqlUserRepository
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobLeaseLostError,
    ReversionJobRepositoryError,
    ReversionJobUserQuotaExceededError,
    ReversionProofRequiredError,
    ReversionQueueCapacityExceededError,
)
from markweave.reversion_jobs.models import (
    ReversionFailure,
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
    reversion_result_object_id,
)
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
    trace,
)

LOCAL_NOW = NOW.astimezone(timezone(timedelta(hours=2)))
LOCAL_RETENTION_END = RETENTION_END.astimezone(timezone(timedelta(hours=-3)))


def _user(repository: SqlUserRepository, name: str) -> User:
    user = User(uuid4(), name, f"{name.casefold()}-{uuid4()}", "hash:user", Role.USER)
    repository.create(user)
    return user


@pytest.fixture
def reverse_repository() -> Iterator[
    tuple[SqlReversionJobRepository, User, User, Engine]
]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "ReverseOwner")
    other = _user(users, "ReverseOther")
    try:
        yield SqlReversionJobRepository(engine), owner, other, engine
    finally:
        engine.dispose()


@pytest.mark.unit
def test_in_process_reverse_repository_contract(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, other, _engine = reverse_repository
    exercise_reversion_job_repository_contract(repository, owner.id, other.id)


@pytest.mark.unit
def test_in_process_reverse_repository_fences_failures_and_cancellation(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, other, _engine = reverse_repository
    queued, _ = repository.create(submission(owner.id))
    assert repository.request_cancel(queued.id, other.id, NOW, RETENTION_END) is None
    cancelled = repository.request_cancel(queued.id, owner.id, NOW, RETENTION_END)
    assert cancelled is not None
    assert (
        repository.request_cancel(queued.id, owner.id, NOW, RETENTION_END) == cancelled
    )
    with pytest.raises(ReversionJobRepositoryError):
        repository.activate_source(queued.id, NOW)
    assert repository.claim("idle", PRINCIPAL, NOW, LEASE_END) is None

    running, _ = repository.create(
        submission(owner.id, created_at=NOW + timedelta(seconds=1))
    )
    activated = repository.activate_source(running.id, NOW)
    assert repository.activate_source(running.id, NOW) == activated
    waiting, _ = repository.create(
        submission(owner.id, created_at=NOW + timedelta(seconds=2))
    )
    repository.activate_source(waiting.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None
    assert claimed.current_attempt_id is not None and claimed.lease_token is not None
    assert repository.claim("blocked", PRINCIPAL, NOW, LEASE_END) is None
    assert not repository.heartbeat(
        ReversionLeaseHeartbeat(
            claimed.id,
            claimed.current_attempt_id,
            "wrong-worker",
            claimed.lease_token,
            NOW,
            LEASE_END,
            ReversionJobStep.CONVERTING,
        )
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_broker_unit(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            uuid4(),
            NOW,
        )
    repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW,
    )
    with pytest.raises(ReversionProofRequiredError):
        repository.fail(
            ReversionFailure(
                claimed.id,
                claimed.current_attempt_id,
                "worker",
                claimed.lease_token,
                "safe_failure",
                "Reverse conversion failed safely.",
                NOW,
                RETENTION_END,
            )
        )
    unit_id = uuid4()
    repository.record_broker_unit(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        unit_id,
        NOW,
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_broker_unit(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            uuid4(),
            NOW,
        )
    termination = proof(claimed.current_attempt_id, unit_id)
    repository.record_active_termination_proof(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        termination,
        NOW,
    )
    assert (
        repository.record_active_termination_proof(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            termination,
            NOW,
        ).termination_proof
        == termination
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_active_termination_proof(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            proof(claimed.current_attempt_id, unit_id),
            NOW,
        )
    with pytest.raises(ReversionJobConflictError, match="trace does not match"):
        repository.succeed(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            reversion_result_object_id(claimed.id, 1),
            trace().result_mode,
            "a" * 64,
            1,
            replace(trace(), detected_format="pptx"),
            NOW,
            RETENTION_END,
        )
    repository.request_cancel(claimed.id, owner.id, NOW, RETENTION_END)
    failed = repository.fail(
        ReversionFailure(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            "safe_failure",
            "Reverse conversion failed safely.",
            NOW,
            RETENTION_END,
        )
    )
    assert failed.state is ReversionJobState.CANCELLED
    with pytest.raises(ReversionJobLeaseLostError):
        repository.finish_cancelled(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            NOW,
            RETENTION_END,
        )
    with pytest.raises(ReversionJobConflictError):
        repository.acknowledge_termination_proof(
            claimed.current_attempt_id, uuid4(), NOW
        )


@pytest.mark.unit
def test_in_process_reverse_repository_recovers_pre_intent_and_incomplete_jobs(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    ready, _ = repository.create(submission(owner.id))
    repository.activate_source(ready.id, NOW)
    assert (
        repository.claim("worker", PRINCIPAL, NOW, NOW + timedelta(seconds=1))
        is not None
    )
    incomplete, _ = repository.create(
        submission(owner.id, created_at=NOW - timedelta(days=1))
    )
    assert (
        repository.recover_expired_leases(
            NOW + timedelta(seconds=2), RETENTION_END, NOW - timedelta(hours=1)
        )
        == 2
    )
    recovered = repository.get_internal(ready.id)
    abandoned = repository.get_internal(incomplete.id)
    assert recovered is not None and recovered.state is ReversionJobState.QUEUED
    assert abandoned is not None and abandoned.state is ReversionJobState.FAILED


@pytest.mark.unit
def test_in_process_reverse_admission_replays_before_capacity(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    _repository, owner, other, engine = reverse_repository
    repository = SqlReversionJobRepository(engine, ReversionAdmissionPolicy(1, 1))
    original = submission(owner.id, idempotency_digest="a" * 64)
    created, _ = repository.create(original)
    replayed, is_replay = repository.create(
        submission(owner.id, idempotency_digest="a" * 64)
    )
    assert is_replay and replayed.id == created.id
    with pytest.raises(ReversionJobUserQuotaExceededError):
        repository.create(submission(owner.id))
    with pytest.raises(ReversionQueueCapacityExceededError):
        repository.create(submission(other.id))


@pytest.mark.unit
def test_in_process_idempotency_collision_paths_are_fail_closed(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
    mocker: MockerFixture,
) -> None:
    repository, owner, _other, _engine = reverse_repository
    winner, _ = repository.create(submission(owner.id, request_digest="d" * 64))
    collision = IntegrityError("INSERT", {}, RuntimeError("unique collision"))
    mocker.patch.object(repository, "_after_idempotency_miss", side_effect=collision)

    with pytest.raises(ReversionJobRepositoryError):
        repository.create(submission(owner.id))

    candidate = submission(owner.id, idempotency_digest="b" * 64)
    get_winner = mocker.patch.object(repository, "_get_idempotent", return_value=None)
    with pytest.raises(ReversionJobRepositoryError):
        repository.create(candidate)
    get_winner.return_value = winner
    with pytest.raises(ReversionJobConflictError):
        repository.create(candidate)

    compatible = submission(
        owner.id,
        idempotency_digest="c" * 64,
        request_digest=winner.request_digest,
    )
    replayed, is_replay = repository.create(compatible)
    assert is_replay and replayed == winner


@pytest.mark.unit
def test_in_process_recovery_proof_retry_is_exact_and_idempotent(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None
    assert claimed.current_attempt_id is not None and claimed.lease_token is not None
    repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW,
    )
    recovery_now = LEASE_END + timedelta(microseconds=1)
    recoveries = repository.claim_recovery("recovery", recovery_now, RETENTION_END, 1)
    assert len(recoveries) == 1 and recoveries[0].recovery_token is not None
    recovery = recoveries[0]
    recovery_token = recovery.recovery_token
    assert recovery_token is not None
    termination = proof(recovery.attempt_id, uuid4())
    persisted = repository.record_recovery_termination_proof(
        job.id,
        recovery.attempt_id,
        recovery_token,
        termination,
        recovery_now,
    )
    assert (
        repository.record_recovery_termination_proof(
            job.id,
            recovery.attempt_id,
            recovery_token,
            termination,
            recovery_now,
        )
        == persisted
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_recovery_termination_proof(
            job.id,
            recovery.attempt_id,
            recovery_token,
            proof(recovery.attempt_id, termination.unit_id),
            recovery_now,
        )
    with pytest.raises(ReversionJobConflictError):
        repository.record_recovery_termination_proof(
            job.id,
            recovery.attempt_id,
            uuid4(),
            termination,
            recovery_now,
        )


@pytest.mark.unit
def test_in_process_reverse_repository_rejects_invalid_result_metadata(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    with pytest.raises(ReversionJobConflictError, match="metadata is invalid"):
        repository.succeed(
            job.id,
            uuid4(),
            "worker",
            uuid4(),
            uuid4(),
            trace().result_mode,
            "not-a-digest",
            1,
            trace(),
            NOW,
            RETENTION_END,
        )


@pytest.mark.unit
def test_in_process_reverse_repository_sanitizes_database_failures(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, engine = reverse_repository
    job_id = uuid4()
    attempt_id = uuid4()
    lease_token = uuid4()
    termination = proof(attempt_id, uuid4())
    failure = ReversionFailure(
        job_id,
        attempt_id,
        "worker",
        lease_token,
        "safe_failure",
        "Reverse conversion failed safely.",
        NOW,
        RETENTION_END,
    )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE reversion_attempts"))
        connection.execute(text("DROP TABLE reversion_broker_principals"))
        connection.execute(text("DROP TABLE reversion_jobs"))

    operations = (
        lambda: repository.create(submission(owner.id)),
        lambda: repository.activate_source(job_id, NOW),
        lambda: repository.get_owner(job_id, owner.id),
        lambda: repository.list_owner(owner.id, offset=0, limit=10),
        lambda: repository.get_internal(job_id),
        lambda: repository.get_attempt(attempt_id),
        lambda: repository.list_attempts(job_id),
        lambda: repository.claim("worker", PRINCIPAL, NOW, LEASE_END),
        lambda: repository.heartbeat(
            ReversionLeaseHeartbeat(
                job_id,
                attempt_id,
                "worker",
                lease_token,
                NOW,
                LEASE_END,
                ReversionJobStep.CONVERTING,
            )
        ),
        lambda: repository.cancellation_requested(
            job_id, attempt_id, "worker", lease_token
        ),
        lambda: repository.request_cancel(job_id, owner.id, NOW, RETENTION_END),
        lambda: repository.reserve_create_intent(
            job_id,
            attempt_id,
            "worker",
            lease_token,
            "reverse-policy-v1",
            POLICY_SPECIFICATION,
            NOW,
        ),
        lambda: repository.record_broker_unit(
            job_id, attempt_id, "worker", lease_token, uuid4(), NOW
        ),
        lambda: repository.record_active_termination_proof(
            job_id, attempt_id, "worker", lease_token, termination, NOW
        ),
        lambda: repository.claim_recovery("recovery", NOW, LEASE_END, 1),
        lambda: repository.record_recovery_termination_proof(
            job_id, attempt_id, uuid4(), termination, NOW
        ),
        lambda: repository.acknowledge_termination_proof(
            attempt_id, termination.proof_id, NOW
        ),
        lambda: repository.recover_expired_leases(NOW, RETENTION_END, NOW),
        lambda: repository.succeed(
            job_id,
            attempt_id,
            "worker",
            lease_token,
            uuid4(),
            trace().result_mode,
            "a" * 64,
            1,
            trace(),
            NOW,
            RETENTION_END,
        ),
        lambda: repository.fail(failure),
        lambda: repository.finish_cancelled(
            job_id, attempt_id, "worker", lease_token, NOW, RETENTION_END
        ),
        lambda: repository.expire_terminal("cleanup", NOW, LEASE_END, 1),
        lambda: repository.complete_cleanup(job_id, uuid4()),
    )
    for operation in operations:
        with pytest.raises(ReversionJobRepositoryError):
            operation()


@pytest.mark.unit
def test_reverse_row_decoders_fail_closed() -> None:
    assert _trace(None) is None
    for invalid in ('{"unexpected":true}', "[]", "not-json"):
        with pytest.raises(ReversionJobRepositoryError):
            _trace(invalid)
    with pytest.raises(ReversionJobRepositoryError):
        _versions("not-json")


@pytest.mark.unit
def test_reversion_failure_rejects_unsafe_text_and_normalizes_timestamps() -> None:
    def failure(
        *,
        worker_id: str = "worker",
        code: str = "safe_failure",
        message: str = "Reverse conversion failed safely.",
        now: datetime = LOCAL_NOW,
        expires_at: datetime = LOCAL_RETENTION_END,
    ) -> ReversionFailure:
        return ReversionFailure(
            uuid4(), uuid4(), worker_id, uuid4(), code, message, now, expires_at
        )

    normalized = failure()
    assert normalized.now == NOW
    assert normalized.expires_at == RETENTION_END

    with pytest.raises(ValueError, match="worker identity must not be blank"):
        failure(worker_id=" \t")
    with pytest.raises(ValueError, match="details must not be blank"):
        failure(code=" \t")
    with pytest.raises(ValueError, match="details must not be blank"):
        failure(message=" \t")
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        failure(now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        failure(expires_at=NOW.replace(tzinfo=None))
