"""In-process persistence coverage for the durable reverse queue."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, create_engine, text, update
from sqlalchemy.exc import IntegrityError

from markweave.auth.models import Role, User
from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    TerminationProof,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationResponse,
    ReconciliationTombstone,
)
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.reversion_jobs.common import _trace, _trace_json, _versions
from markweave.persistence.schema import (
    Base,
    ReversionAttemptRow,
    ReversionJobRow,
    ReversionOrphanProofRow,
)
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
    ReversionTraceMetadata,
    reversion_result_object_id,
)
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from markweave.reversions.formats import FormatFamily, admit_format
from markweave.reversions.models import ReverseOutputMode
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

LOCAL_NOW = NOW.astimezone(timezone(timedelta(hours=2)))
LOCAL_RETENTION_END = RETENTION_END.astimezone(timezone(timedelta(hours=-3)))


@pytest.mark.unit
def test_reconciliation_is_exclusive_monotone_and_retains_orphan_before_ack(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    token = uuid4()
    queued, _ = repository.create(submission(owner.id))
    repository.activate_source(queued.id, NOW)
    assert repository.claim("not-reconciled", principal, NOW, LEASE_END) is None
    cursor = repository.begin_reconciliation(
        principal, "reconciler", token, NOW, LEASE_END
    )
    assert cursor == 0
    assert repository.claim("blocked", principal, NOW, LEASE_END) is None

    orphan_proof = proof(uuid4(), uuid4(), principal=principal)
    tombstone = ReconciliationTombstone(7, POLICY_SPECIFICATION, orphan_proof)
    page = ReconciliationResponse(
        uuid4(), principal.principal_id, 0, 9, tombstone, False
    )
    assert (
        repository.record_reconciliation_page(principal, token, page, NOW) == tombstone
    )
    assert repository.pending_reconciliation_acknowledgements(
        principal, token, NOW, 8
    ) == (tombstone,)
    substituted = replace(
        tombstone,
        proof=replace(tombstone.proof, principal=AuthenticatedPrincipal(uuid4())),
    )
    with pytest.raises(ReversionJobConflictError):
        repository.mark_reconciliation_acknowledged(principal, token, substituted, NOW)
    repository.record_reconciliation_page(
        principal,
        token,
        ReconciliationResponse(uuid4(), principal.principal_id, 7, 8, None, True),
        NOW,
    )
    repository.record_reconciliation_page(
        principal,
        token,
        ReconciliationResponse(uuid4(), principal.principal_id, 7, 8, None, True),
        NOW,
    )
    with pytest.raises(ReversionJobConflictError):
        repository.complete_reconciliation(principal, token, NOW)
    repository.mark_reconciliation_acknowledged(principal, token, tombstone, NOW)
    assert (
        repository.pending_reconciliation_acknowledgements(principal, token, NOW, 8)
        == ()
    )
    repository.complete_reconciliation(principal, token, NOW)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT create_sequence_high_water, reconciliation_complete FROM reversion_broker_principals WHERE principal_id = :principal"
            ),
            {"principal": str(principal.principal_id)},
        ).one()
        orphan = connection.execute(
            text(
                "SELECT acknowledged_at FROM reversion_orphan_proofs WHERE principal_id = :principal AND create_sequence = 7"
            ),
            {"principal": str(principal.principal_id)},
        ).one()
    assert row == (9, True)
    assert orphan.acknowledged_at is not None


@pytest.mark.unit
def test_reconciliation_takeover_and_unproven_restore_fail_closed(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    token = uuid4()
    repository.begin_reconciliation(principal, "first", token, NOW, LEASE_END)
    with pytest.raises(ReversionJobLeaseLostError):
        repository.begin_reconciliation(principal, "second", uuid4(), NOW, LEASE_END)
    takeover = uuid4()
    repository.begin_reconciliation(
        principal, "second", takeover, LEASE_END + timedelta(seconds=1), RETENTION_END
    )
    for _ in range(2):
        repository.record_reconciliation_page(
            principal,
            takeover,
            ReconciliationResponse(uuid4(), principal.principal_id, 0, 0, None, True),
            LEASE_END + timedelta(seconds=1),
        )
    queued, _ = repository.create(submission(owner.id))
    repository.activate_source(queued.id, NOW)
    repository.complete_reconciliation(
        principal, takeover, LEASE_END + timedelta(seconds=1)
    )
    claimed = repository.claim("worker", principal, NOW, LEASE_END)
    assert (
        claimed is not None
        and claimed.current_attempt_id is not None
        and claimed.lease_token is not None
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
    retry = uuid4()
    repository.begin_reconciliation(principal, "restore", retry, NOW, LEASE_END)
    with pytest.raises(ReversionProofRequiredError):
        repository.complete_reconciliation(principal, retry, NOW)


@pytest.mark.unit
def test_reconciliation_rejects_invalid_inputs_and_stale_tokens(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, _owner, _other, engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    token = uuid4()
    with pytest.raises(ValueError):
        repository.begin_reconciliation(principal, "", token, NOW, LEASE_END)
    repository.begin_reconciliation(principal, "reconciler", token, NOW, LEASE_END)
    with engine.connect() as connection:
        durable_lease = connection.execute(
            text(
                "SELECT reconciliation_token, reconciliation_cursor, reconciliation_fixed_point FROM reversion_broker_principals WHERE principal_id = :principal"
            ),
            {"principal": str(principal.principal_id)},
        ).one()
    page = ReconciliationResponse(uuid4(), principal.principal_id, 0, 0, None, True)
    with pytest.raises(ReversionJobConflictError):
        repository.record_reconciliation_page(
            AuthenticatedPrincipal(uuid4()), token, page, NOW
        )
    with pytest.raises(ReversionJobLeaseLostError):
        repository.record_reconciliation_page(principal, uuid4(), page, NOW)
    with pytest.raises(ReversionJobLeaseLostError):
        repository.mark_reconciliation_acknowledged(
            principal,
            uuid4(),
            ReconciliationTombstone(
                1, POLICY_SPECIFICATION, proof(uuid4(), uuid4(), principal)
            ),
            NOW,
        )
    with pytest.raises(ValueError):
        repository.pending_reconciliation_acknowledgements(principal, token, NOW, 0)
    with pytest.raises(ReversionJobLeaseLostError):
        repository.pending_reconciliation_acknowledgements(principal, uuid4(), NOW, 1)
    with pytest.raises(ReversionJobLeaseLostError):
        repository.complete_reconciliation(principal, uuid4(), NOW)
    with pytest.raises(ReversionJobConflictError):
        repository.complete_reconciliation(principal, token, NOW)
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT reconciliation_token, reconciliation_cursor, reconciliation_fixed_point FROM reversion_broker_principals WHERE principal_id = :principal"
                ),
                {"principal": str(principal.principal_id)},
            ).one()
            == durable_lease
        )


@pytest.mark.unit
def test_reconciliation_replays_and_rejects_conflicting_orphan_receipts(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, _owner, _other, engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    retained = proof(uuid4(), uuid4(), principal)
    tombstone = ReconciliationTombstone(1, POLICY_SPECIFICATION, retained)
    moments = iter(NOW + timedelta(minutes=value) for value in range(4))

    def snapshot() -> tuple[object, ...]:
        with engine.connect() as connection:
            receipt = connection.execute(
                text(
                    "SELECT attempt_id, unit_id, proof_id, policy_revision, policy_specification, exit_evidence, empty_evidence, removal_evidence, recorded_at, acknowledged_at FROM reversion_orphan_proofs WHERE principal_id = :principal AND create_sequence = 1"
                ),
                {"principal": str(principal.principal_id)},
            ).one_or_none()
            state = connection.execute(
                text(
                    "SELECT create_sequence_high_water, reconciliation_cursor FROM reversion_broker_principals WHERE principal_id = :principal"
                ),
                {"principal": str(principal.principal_id)},
            ).one()
            sequence_two = connection.execute(
                text(
                    "SELECT COUNT(*) FROM reversion_orphan_proofs WHERE principal_id = :principal AND create_sequence = 2"
                ),
                {"principal": str(principal.principal_id)},
            ).scalar_one()
        return receipt, state, sequence_two

    def record(candidate: ReconciliationTombstone, *, conflict: bool = False) -> None:
        token = uuid4()
        moment = next(moments)
        repository.begin_reconciliation(
            principal, "reconciler", token, moment, moment + timedelta(seconds=30)
        )
        before = snapshot()

        def persist() -> None:
            repository.record_reconciliation_page(
                principal,
                token,
                ReconciliationResponse(
                    uuid4(), principal.principal_id, 0, 2, candidate, False
                ),
                moment,
            )

        if conflict:
            with pytest.raises(ReversionJobConflictError):
                persist()
            assert snapshot() == before
        else:
            persist()

    record(tombstone)
    durable = snapshot()
    record(tombstone)
    assert snapshot() == durable
    record(
        ReconciliationTombstone(
            1, POLICY_SPECIFICATION, proof(uuid4(), uuid4(), principal)
        ),
        conflict=True,
    )
    assert snapshot()[0] == durable[0]
    record(ReconciliationTombstone(2, POLICY_SPECIFICATION, retained), conflict=True)
    assert snapshot()[0] == durable[0]


@pytest.mark.unit
def test_reverse_claim_rejects_exhausted_principal_sequence(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE reversion_broker_principals SET create_sequence_high_water = :maximum WHERE principal_id = :principal"
            ),
            {"maximum": MAX_SEQUENCE, "principal": str(PRINCIPAL.principal_id)},
        )
    with pytest.raises(ReversionJobRepositoryError):
        repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    persisted = repository.get_internal(job.id)
    assert persisted is not None and persisted.state is ReversionJobState.QUEUED
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT create_sequence_high_water FROM reversion_broker_principals WHERE principal_id = :principal"
                ),
                {"principal": str(PRINCIPAL.principal_id)},
            ).scalar_one()
            == MAX_SEQUENCE
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM reversion_attempts")
            ).scalar_one()
            == 0
        )


@pytest.mark.unit
def test_reconciliation_drains_preexisting_unacknowledged_attempt_proof(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert (
        claimed is not None
        and claimed.current_attempt_id is not None
        and claimed.lease_token is not None
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
    unit_id = uuid4()
    repository.record_broker_unit(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        unit_id,
        NOW,
    )
    retained = proof(claimed.current_attempt_id, unit_id)
    repository.record_active_termination_proof(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        retained,
        NOW,
    )
    token = uuid4()
    repository.begin_reconciliation(PRINCIPAL, "restart", token, NOW, LEASE_END)
    tombstone = ReconciliationTombstone(1, POLICY_SPECIFICATION, retained)
    with pytest.raises(ReversionJobConflictError):
        repository.mark_reconciliation_acknowledged(PRINCIPAL, token, tombstone, NOW)
    assert repository.pending_reconciliation_acknowledgements(
        PRINCIPAL, token, NOW, 8
    ) == (tombstone,)
    substituted = replace(
        tombstone,
        proof=replace(retained, principal=AuthenticatedPrincipal(uuid4())),
    )
    with pytest.raises(ReversionJobConflictError):
        repository.mark_reconciliation_acknowledged(PRINCIPAL, token, substituted, NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("unit_id", str(uuid4())),
        ("proof_unit_id", str(uuid4())),
        ("proof_id", str(uuid4())),
        ("proof_principal_id", str(uuid4())),
        ("policy_revision", "mutated-policy"),
        ("policy_specification", "sha256:" + "9" * 64),
        ("proof_policy_revision", "mutated-proof-policy"),
        ("exit_evidence", "sha256:" + "8" * 64),
        ("empty_evidence", "sha256:" + "7" * 64),
        ("removal_evidence", "sha256:" + "6" * 64),
        ("reconciliation_ack_intent_at", None),
    ],
)
def test_reconciliation_ack_rejects_mutated_attempt_proof_identity(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
    column: str,
    value: object,
) -> None:
    repository, owner, _other, engine = reverse_repository
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
    unit_id = uuid4()
    repository.record_broker_unit(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        unit_id,
        NOW,
    )
    retained = proof(claimed.current_attempt_id, unit_id)
    repository.record_active_termination_proof(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        retained,
        NOW,
    )
    token = uuid4()
    repository.begin_reconciliation(PRINCIPAL, "restart", token, NOW, LEASE_END)
    tombstone = ReconciliationTombstone(1, POLICY_SPECIFICATION, retained)
    assert repository.pending_reconciliation_acknowledgements(
        PRINCIPAL, token, NOW, 8
    ) == (tombstone,)
    with engine.begin() as connection:
        connection.execute(
            update(ReversionAttemptRow)
            .where(ReversionAttemptRow.attempt_id == str(claimed.current_attempt_id))
            .values({column: value})
        )
    with pytest.raises(ReversionJobConflictError):
        repository.mark_reconciliation_acknowledged(PRINCIPAL, token, tombstone, NOW)
    with engine.connect() as connection:
        acknowledged_at = connection.execute(
            text(
                "SELECT proof_acknowledged_at FROM reversion_attempts WHERE attempt_id = :attempt"
            ),
            {"attempt": str(claimed.current_attempt_id)},
        ).scalar_one()
    assert acknowledged_at is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("attempt_id", str(uuid4())),
        ("unit_id", str(uuid4())),
        ("proof_id", str(uuid4())),
        ("policy_revision", "mutated-policy"),
        ("policy_specification", "sha256:" + "9" * 64),
        ("exit_evidence", "sha256:" + "8" * 64),
        ("empty_evidence", "sha256:" + "7" * 64),
        ("removal_evidence", "sha256:" + "6" * 64),
    ],
)
def test_reconciliation_ack_rejects_mutated_orphan_proof_bundle(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
    column: str,
    value: object,
) -> None:
    repository, _owner, _other, engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    token = uuid4()
    repository.begin_reconciliation(principal, "reconciler", token, NOW, LEASE_END)

    retained = proof(uuid4(), uuid4(), principal)
    tombstone = ReconciliationTombstone(1, POLICY_SPECIFICATION, retained)
    repository.record_reconciliation_page(
        principal,
        token,
        ReconciliationResponse(uuid4(), principal.principal_id, 0, 1, tombstone, False),
        NOW,
    )
    assert repository.pending_reconciliation_acknowledgements(
        principal, token, NOW, 8
    ) == (tombstone,)
    with engine.begin() as connection:
        connection.execute(
            update(ReversionOrphanProofRow)
            .where(
                ReversionOrphanProofRow.principal_id == str(principal.principal_id),
                ReversionOrphanProofRow.create_sequence == 1,
            )
            .values({column: value})
        )
    with pytest.raises(ReversionJobConflictError):
        repository.mark_reconciliation_acknowledged(principal, token, tombstone, NOW)
    with engine.connect() as connection:
        acknowledged_at = connection.execute(
            text(
                "SELECT acknowledged_at FROM reversion_orphan_proofs WHERE principal_id = :principal AND create_sequence = 1"
            ),
            {"principal": str(principal.principal_id)},
        ).scalar_one()
    assert acknowledged_at is None


@pytest.mark.unit
def test_reconciliation_hydrates_exact_pre_intent_attempt_from_broker_proof(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None and claimed.current_attempt_id is not None
    unit_id = uuid4()
    retained = proof(claimed.current_attempt_id, unit_id)
    tombstone = ReconciliationTombstone(1, POLICY_SPECIFICATION, retained)
    token = uuid4()
    repository.begin_reconciliation(PRINCIPAL, "restore", token, NOW, LEASE_END)

    repository.record_reconciliation_page(
        PRINCIPAL,
        token,
        ReconciliationResponse(uuid4(), PRINCIPAL.principal_id, 0, 1, tombstone, False),
        NOW,
    )

    attempt = repository.get_attempt(claimed.current_attempt_id)
    assert attempt is not None
    assert attempt.unit_id == unit_id
    assert attempt.policy_revision == retained.policy_revision
    assert attempt.policy_specification == POLICY_SPECIFICATION
    assert attempt.termination_proof == retained


@pytest.mark.unit
def test_reconciliation_rejects_known_attempt_identity_policy_and_proof_conflicts(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, engine = reverse_repository
    principal = AuthenticatedPrincipal(uuid4())
    complete_empty_reconciliation(repository, principal)
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", principal, NOW, LEASE_END)
    assert claimed is not None
    assert claimed.current_attempt_id is not None and claimed.lease_token is not None
    attempt = repository.get_attempt(claimed.current_attempt_id)
    assert attempt is not None
    unit_id = uuid4()
    retained = proof(attempt.attempt_id, unit_id, principal)
    token = uuid4()
    repository.begin_reconciliation(principal, "reconciler", token, NOW, LEASE_END)

    def snapshot() -> tuple[object, object]:
        with engine.connect() as connection:
            durable_attempt = connection.execute(
                text(
                    "SELECT create_intent_at, policy_revision, policy_specification, unit_id, proof_id, proof_unit_id, proof_principal_id, proof_policy_revision, exit_evidence, empty_evidence, removal_evidence, proof_recorded_at, reconciliation_ack_intent_at, proof_acknowledged_at FROM reversion_attempts WHERE attempt_id = :attempt"
                ),
                {"attempt": str(attempt.attempt_id)},
            ).one()
            durable_principal = connection.execute(
                text(
                    "SELECT create_sequence_high_water, reconciliation_cursor FROM reversion_broker_principals WHERE principal_id = :principal"
                ),
                {"principal": str(principal.principal_id)},
            ).one()
        return durable_attempt, durable_principal

    def record(candidate: TerminationProof) -> None:
        tombstone = ReconciliationTombstone(
            attempt.create_sequence, POLICY_SPECIFICATION, candidate
        )
        repository.record_reconciliation_page(
            principal,
            token,
            ReconciliationResponse(
                uuid4(),
                principal.principal_id,
                0,
                attempt.create_sequence,
                tombstone,
                False,
            ),
            NOW,
        )

    def reject(candidate: TerminationProof) -> None:
        before = snapshot()
        with pytest.raises(ReversionJobConflictError):
            record(candidate)
        assert snapshot() == before

    reject(replace(retained, attempt_id=uuid4()))
    repository.reserve_create_intent(
        claimed.id,
        attempt.attempt_id,
        "worker",
        claimed.lease_token,
        retained.policy_revision,
        POLICY_SPECIFICATION,
        NOW,
    )
    reject(replace(retained, policy_revision="other-policy"))
    repository.record_broker_unit(
        claimed.id,
        attempt.attempt_id,
        "worker",
        claimed.lease_token,
        unit_id,
        NOW,
    )
    repository.record_active_termination_proof(
        claimed.id,
        attempt.attempt_id,
        "worker",
        claimed.lease_token,
        retained,
        NOW,
    )
    reject(replace(retained, proof_id=uuid4()))


@pytest.mark.unit
def test_reconciliation_rejects_cross_ledger_attempt_identity_collision(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
) -> None:
    repository, owner, _other, _engine = reverse_repository
    job, _ = repository.create(submission(owner.id))
    repository.activate_source(job.id, NOW)
    claimed = repository.claim("worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None and claimed.current_attempt_id is not None
    token = uuid4()
    repository.begin_reconciliation(PRINCIPAL, "restore", token, NOW, LEASE_END)
    conflicting = proof(claimed.current_attempt_id, uuid4())

    with pytest.raises(ReversionJobConflictError):
        repository.record_reconciliation_page(
            PRINCIPAL,
            token,
            ReconciliationResponse(
                uuid4(),
                PRINCIPAL.principal_id,
                0,
                2,
                ReconciliationTombstone(2, POLICY_SPECIFICATION, conflicting),
                False,
            ),
            NOW,
        )


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
        repository = SqlReversionJobRepository(engine)
        complete_empty_reconciliation(repository)
        yield repository, owner, other, engine
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
@pytest.mark.parametrize(
    ("admission", "result_trace"),
    (
        (
            admit_format(".docx", "docx"),
            ReversionTraceMetadata(
                1,
                "firecrawl-anydoc",
                "0.2.4",
                FormatFamily.WORD,
                "docx",
                ReverseOutputMode.MARKDOWN_WITH_ASSETS,
                2,
                256,
                1,
            ),
        ),
        (
            admit_format(".csv", None, csv_text_validated=True),
            ReversionTraceMetadata(
                1,
                "firecrawl-anydoc",
                "0.2.4",
                FormatFamily.CSV,
                "csv",
                ReverseOutputMode.MARKDOWN,
                0,
                0,
                0,
            ),
        ),
    ),
)
def test_in_process_publication_persists_mixed_assets_and_csv_parser_trace(
    reverse_repository: tuple[SqlReversionJobRepository, User, User, Engine],
    admission,
    result_trace: ReversionTraceMetadata,
) -> None:
    repository, owner, _other, _engine = reverse_repository
    source = replace(submission(owner.id), admission=admission)
    job, _ = repository.create(source)
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
    termination = proof(claimed.current_attempt_id, uuid4())
    repository.record_active_termination_proof(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        termination,
        NOW,
    )
    succeeded = repository.succeed(
        claimed.id,
        claimed.current_attempt_id,
        "worker",
        claimed.lease_token,
        reversion_result_object_id(claimed.id, 1),
        result_trace.result_mode,
        "a" * 64,
        1,
        result_trace,
        NOW,
        RETENTION_END,
    )
    assert succeeded.trace == result_trace
    persisted = repository.get_internal(claimed.id)
    assert persisted is not None and persisted.trace == result_trace
    if result_trace.source_family is FormatFamily.WORD:
        tampered = replace(result_trace, detected_format="pptx")
        with _engine.begin() as database:
            database.execute(
                update(ReversionJobRow)
                .where(ReversionJobRow.id == str(claimed.id))
                .values(trace_metadata=_trace_json(tampered))
            )
        with pytest.raises(ReversionJobRepositoryError):
            repository.get_internal(claimed.id)


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
    assert len(failure(worker_id="w" * 255).worker_id) == 255
    assert len(failure(code="c" * 128).code) == 128
    assert len(failure(message="m" * 1024).message) == 1024

    with pytest.raises(ValueError, match="worker identity is invalid"):
        failure(worker_id=" \t")
    with pytest.raises(ValueError, match="worker identity is invalid"):
        failure(worker_id="w" * 256)
    with pytest.raises(ValueError, match="worker identity is invalid"):
        replace(normalized, worker_id=1)
    with pytest.raises(ValueError, match="details are invalid"):
        failure(code=" \t")
    with pytest.raises(ValueError, match="details are invalid"):
        failure(code="c" * 129)
    with pytest.raises(ValueError, match="details are invalid"):
        replace(normalized, code=1)
    with pytest.raises(ValueError, match="details are invalid"):
        failure(message=" \t")
    with pytest.raises(ValueError, match="details are invalid"):
        failure(message="m" * 1025)
    with pytest.raises(ValueError, match="details are invalid"):
        replace(normalized, message=1)
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        failure(now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        failure(expires_at=NOW.replace(tzinfo=None))
