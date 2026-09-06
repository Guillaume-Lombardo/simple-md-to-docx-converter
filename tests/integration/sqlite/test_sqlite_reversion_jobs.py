"""Real SQLite/filesystem reverse queue integration coverage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib import import_module
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import text

from markweave.auth.models import Role, User
from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.fake_runtime import FakeIsolationRuntime
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    BrokerPolicy,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.broker.protocol import AcknowledgeRequest
from markweave.broker.reconciliation_protocol import ReconciliationRequest
from markweave.broker.service import IsolationBrokerService
from markweave.jobs.models import JobOutput, JobSubmission
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import downgrade_database, upgrade_database
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.sql import (
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
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
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
)
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    POLICY_SPECIFICATION,
    PRINCIPAL,
    RETENTION_END,
    complete_empty_reconciliation,
    exercise_reversion_job_repository_contract,
    proof,
    submission,
    trace,
)

REVERSION_MIGRATION = import_module(
    "markweave.persistence.migrations.versions.20260906_16_reversion_queue"
)


class _CrashAfterBrokerAck:
    def __init__(self, dispatcher: BrokerDispatcher) -> None:
        self.dispatcher = dispatcher
        self.fail_once = True

    def reconcile(self, request: ReconciliationRequest):
        return self.dispatcher.dispatch_reconciliation(PRINCIPAL, request)

    def request(self, request: AcknowledgeRequest):
        response = self.dispatcher.dispatch(PRINCIPAL, request)
        if type(request) is AcknowledgeRequest and self.fail_once:
            self.fail_once = False
            raise BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE)
        return response


def _user(repository: SqlUserRepository, name: str) -> User:
    user = User(uuid4(), name, f"{name.casefold()}-{uuid4()}", "hash:user", Role.USER)
    repository.create(user)
    return user


@pytest.mark.integration
def test_reversion_migration_has_the_verified_parent() -> None:
    assert REVERSION_MIGRATION.revision == "20260906_16"
    assert REVERSION_MIGRATION.down_revision == "20260901_15"


@pytest.mark.integration
def test_sqlite_reversion_repository_contract_and_restart(tmp_path: Path) -> None:
    url = standalone_database_url(tmp_path)
    engine = create_database_engine(url)
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "Owner")
    other = _user(users, "Other")
    exercise_reversion_job_repository_contract(
        SqlReversionJobRepository(engine), owner.id, other.id
    )
    engine.dispose()

    reopened = create_database_engine(url)
    assert (
        SqlReversionJobRepository(reopened)
        .list_owner(owner.id, offset=0, limit=10)
        .total
        == 1
    )
    reopened.dispose()


@pytest.mark.integration
def test_sqlite_real_broker_reconciliation_replays_crash_after_ack(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "BrokerReconcileCrash")
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert (
        claimed is not None
        and claimed.current_attempt_id is not None
        and claimed.lease_token is not None
    )

    policy = BrokerPolicy(
        "reverse-policy-v1",
        "sha256:" + "9" * 64,
        RuntimeLimits(1, 1, 1, 1, 1, 1),
        RuntimeChannelLimits(1, 1),
    )
    specification = policy_specification_evidence(policy)
    repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        policy.revision,
        specification,
        NOW,
    )
    broker = IsolationBrokerService(
        SQLiteBrokerInventory(
            tmp_path / "broker-inventory.sqlite3", bytes(range(32)), max_records=8
        ),
        FakeIsolationRuntime(),
        policy,
        max_discovered_units=8,
    )
    broker.start()
    unit = broker.create(ReplayPosition(PRINCIPAL, 1), claimed.current_attempt_id)
    repository.record_broker_unit(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        unit.unit_id,
        NOW,
    )
    broker.terminate(PRINCIPAL, claimed.current_attempt_id, unit.unit_id)
    gateway = _CrashAfterBrokerAck(BrokerDispatcher(broker))
    reconciler = ReversionBrokerReconciler(repository, gateway, ack_batch_limit=2)
    token = uuid4()

    with pytest.raises(BrokerError) as crashed:
        reconciler.reconcile(
            PRINCIPAL, "reconciler", token, NOW, LEASE_END, now_factory=lambda: NOW
        )
    assert crashed.value.category is BrokerErrorCategory.TRANSPORT_FAILURE
    reconciler.reconcile(
        PRINCIPAL, "reconciler", token, NOW, LEASE_END, now_factory=lambda: NOW
    )

    attempt = repository.get_attempt(claimed.current_attempt_id)
    assert attempt is not None and attempt.proof_acknowledged_at is not None
    engine.dispose()


@pytest.mark.integration
def test_sqlite_reverse_owner_quota_and_replay_precede_global_capacity(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "Quota")
    other = _user(users, "Capacity")
    repository = SqlReversionJobRepository(engine, ReversionAdmissionPolicy(1, 1))
    complete_empty_reconciliation(repository)
    original = submission(owner.id, idempotency_digest="a" * 64)
    created, _ = repository.create(original)
    replay, replayed = repository.create(
        submission(owner.id, idempotency_digest="a" * 64)
    )
    assert replayed and replay.id == created.id
    with pytest.raises(ReversionJobUserQuotaExceededError):
        repository.create(submission(owner.id))
    with pytest.raises(ReversionQueueCapacityExceededError):
        repository.create(submission(other.id))
    engine.dispose()


@pytest.mark.integration
def test_sqlite_reverse_cancellation_failure_and_proof_fences(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "Lifecycle")
    other = _user(users, "Outsider")
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)

    queued, _ = repository.create(submission(owner.id))
    assert repository.request_cancel(queued.id, other.id, NOW, RETENTION_END) is None
    cancelled = repository.request_cancel(queued.id, owner.id, NOW, RETENTION_END)
    assert cancelled is not None and cancelled.state is ReversionJobState.CANCELLED
    assert (
        repository.request_cancel(queued.id, owner.id, NOW, RETENTION_END) == cancelled
    )
    with pytest.raises(ReversionJobRepositoryError):
        repository.activate_source(queued.id, NOW)
    assert repository.claim("idle", PRINCIPAL, NOW, LEASE_END) is None

    unproven, _ = repository.create(
        submission(owner.id, created_at=NOW + timedelta(seconds=1))
    )
    repository.activate_source(unproven.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None and claimed.current_attempt_id and claimed.lease_token
    assert not repository.cancellation_requested(
        claimed.id, claimed.current_attempt_id, "wrong", claimed.lease_token
    )
    assert not repository.heartbeat(
        ReversionLeaseHeartbeat(
            claimed.id,
            claimed.current_attempt_id,
            "wrong",
            claimed.lease_token,
            NOW + timedelta(seconds=1),
            LEASE_END,
            claimed.step,
        )
    )
    repository.reserve_create_intent(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW + timedelta(seconds=1),
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
                NOW + timedelta(seconds=2),
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
        NOW + timedelta(seconds=2),
    )
    assert (
        repository.record_broker_unit(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            unit_id,
            NOW + timedelta(seconds=2),
        ).unit_id
        == unit_id
    )
    with pytest.raises(ReversionJobConflictError):
        repository.record_broker_unit(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            uuid4(),
            NOW + timedelta(seconds=2),
        )
    termination = proof(claimed.current_attempt_id, unit_id)
    repository.record_active_termination_proof(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        termination,
        NOW + timedelta(seconds=3),
    )
    assert (
        repository.record_active_termination_proof(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            termination,
            NOW + timedelta(seconds=3),
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
            NOW + timedelta(seconds=3),
        )
    repository.request_cancel(claimed.id, owner.id, NOW, RETENTION_END)
    assert repository.cancellation_requested(
        claimed.id, claimed.current_attempt_id, "worker", claimed.lease_token
    )
    failed = repository.fail(
        ReversionFailure(
            claimed.id,
            claimed.current_attempt_id,
            "worker",
            claimed.lease_token,
            "safe_failure",
            "Reverse conversion failed safely.",
            NOW + timedelta(seconds=4),
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
            NOW + timedelta(seconds=5),
            RETENTION_END,
        )
    with pytest.raises(ReversionJobConflictError):
        repository.acknowledge_termination_proof(
            claimed.current_attempt_id, uuid4(), NOW
        )
    engine.dispose()


@pytest.mark.integration
def test_sqlite_reverse_recovery_without_create_intent_and_incomplete_upload(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "Recovery")
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    ready, _ = repository.create(submission(owner.id))
    repository.activate_source(ready.id, NOW)
    claim = repository.claim("worker", PRINCIPAL, NOW, NOW + timedelta(seconds=1))
    assert claim is not None
    abandoned, _ = repository.create(
        submission(owner.id, created_at=NOW - timedelta(days=1))
    )
    recovered = repository.recover_expired_leases(
        NOW + timedelta(seconds=2), RETENTION_END, NOW - timedelta(hours=1)
    )
    assert recovered == 2
    recovered_job = repository.get_internal(ready.id)
    assert recovered_job is not None and recovered_job.state is ReversionJobState.QUEUED
    incomplete = repository.get_internal(abandoned.id)
    assert incomplete is not None and incomplete.state is ReversionJobState.FAILED
    engine.dispose()


@pytest.mark.integration
def test_sqlite_reverse_repository_sanitizes_database_failures(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    owner_id = uuid4()
    job_id = uuid4()
    attempt_id = uuid4()
    lease_token = uuid4()
    termination = proof(attempt_id, uuid4())
    failure = ReversionFailure(
        job_id,
        attempt_id,
        "worker",
        lease_token,
        "safe",
        "Reverse conversion failed safely.",
        NOW,
        RETENTION_END,
    )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE reversion_attempts"))
        connection.execute(text("DROP TABLE reversion_broker_principals"))
        connection.execute(text("DROP TABLE reversion_jobs"))

    operations = (
        lambda: repository.create(submission(owner_id)),
        lambda: repository.activate_source(job_id, NOW),
        lambda: repository.get_owner(job_id, owner_id),
        lambda: repository.list_owner(owner_id, offset=0, limit=10),
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
        lambda: repository.request_cancel(job_id, owner_id, NOW, RETENTION_END),
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
            job_id,
            attempt_id,
            "worker",
            lease_token,
            termination,
            NOW,
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
    engine.dispose()


@pytest.mark.integration
def test_sqlite_reversion_migration_round_trip(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    downgrade_database(engine, "20260901_15")
    upgrade_database(engine)
    engine.dispose()


@pytest.mark.integration
def test_sqlite_mixed_family_global_capacity_is_atomic(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    forward_owner = _user(users, "Forward")
    reverse_owner = _user(users, "Reverse")
    forward = SqlJobRepository(engine, JobAdmissionPolicy(10, 1))
    reverse = SqlReversionJobRepository(engine, ReversionAdmissionPolicy(10, 1))
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
                "7" * 64,
                None,
                NOW,
            )
        )
        return "forward"

    def create_reverse() -> str:
        barrier.wait()
        reverse.create(submission(reverse_owner.id))
        return "reverse"

    outcomes: list[str] = []
    failures: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_forward), executor.submit(create_reverse)]
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as error:
                failures.append(error)
    assert len(outcomes) == 1
    assert len(failures) == 1
    assert failures[0].__class__.__name__ in {
        "JobQueueCapacityExceededError",
        ReversionQueueCapacityExceededError.__name__,
    }
    engine.dispose()


@pytest.mark.integration
def test_sqlite_claims_allocate_unique_principal_sequences(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    for name in ("SequenceOne", "SequenceTwo"):
        owner = _user(users, name)
        job, _ = repository.create(submission(owner.id))
        repository.activate_source(job.id, NOW)
    principal = type(PRINCIPAL)(uuid4())
    complete_empty_reconciliation(repository, principal)
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
def test_sqlite_begin_reconciliation_and_claim_are_linearized(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "ReconciliationClaimRace")
    repository = SqlReversionJobRepository(engine)
    principal = type(PRINCIPAL)(uuid4())
    complete_empty_reconciliation(repository, principal)
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    barrier = Barrier(2)
    token = uuid4()

    def begin() -> None:
        barrier.wait()
        repository.begin_reconciliation(principal, "racer", token, NOW, LEASE_END)

    def claim() -> ReversionJob | None:
        barrier.wait()
        return repository.claim("racer", principal, NOW, LEASE_END)

    with ThreadPoolExecutor(max_workers=2) as executor:
        begin_future = executor.submit(begin)
        claim_future = executor.submit(claim)
        begin_future.result()
        claimed = claim_future.result()
    assert claimed is None or claimed.current_attempt_id is not None
    assert repository.claim("after-begin", principal, NOW, LEASE_END) is None
    engine.dispose()


@pytest.mark.integration
def test_sqlite_expired_reconciliation_takeover_has_one_winner(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    repository = SqlReversionJobRepository(engine)
    principal = type(PRINCIPAL)(uuid4())
    repository.begin_reconciliation(
        principal, "expired", uuid4(), NOW, NOW + timedelta(seconds=1)
    )
    barrier = Barrier(2)

    def takeover(owner: str) -> bool:
        barrier.wait()
        try:
            repository.begin_reconciliation(
                principal,
                owner,
                uuid4(),
                NOW + timedelta(seconds=2),
                RETENTION_END,
            )
        except ReversionJobLeaseLostError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(takeover, ("takeover-a", "takeover-b"))) == 1
    engine.dispose()


@pytest.mark.integration
def test_sqlite_recovery_proof_is_a_single_exact_cas(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    owner = _user(users, "RecoveryProofRace")
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    principal = type(PRINCIPAL)(uuid4())
    complete_empty_reconciliation(repository, principal)
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

    def record(candidate_index: int) -> object:
        barrier.wait()
        return repository.record_recovery_termination_proof(
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
def test_sqlite_conflicting_idempotent_submissions_never_replay(tmp_path: Path) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    upgrade_database(engine)
    owner = _user(SqlUserRepository(engine), "IdempotencyRace")
    repository = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repository)
    key = "9" * 64
    barrier = Barrier(2)

    def create(request_digest: str) -> object:
        barrier.wait()
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
    engine.dispose()
