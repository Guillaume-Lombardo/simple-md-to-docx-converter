"""Shared durable reverse queue contract for both SQL profiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.broker.reconciliation_protocol import ReconciliationResponse
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobLeaseLostError,
    ReversionProofRequiredError,
)
from markweave.reversion_jobs.models import (
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
    ReversionLeaseRecoveryResult,
    ReversionSubmission,
    ReversionTraceMetadata,
    reversion_result_object_id,
)
from markweave.reversions.formats import FormatFamily, admit_format
from markweave.reversions.models import ReverseOutputMode

NOW = datetime(2026, 9, 6, tzinfo=UTC)
LEASE_END = NOW + timedelta(seconds=30)
RETENTION_END = NOW + timedelta(hours=1)
PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000071"))
POLICY_SPECIFICATION = EvidenceDigest("sha256:" + "a" * 64)


def submission(
    owner_id: UUID,
    *,
    created_at: datetime = NOW,
    idempotency_digest: str | None = None,
    request_digest: str = "2" * 64,
) -> ReversionSubmission:
    return ReversionSubmission(
        id=uuid4(),
        owner_id=owner_id,
        source_object_id=uuid4(),
        source_stem="quarterly-report",
        admission=admit_format(".docx", "docx"),
        source_sha256="1" * 64,
        source_size=128,
        component_versions=(
            ("firecrawl-anydoc", "0.2.4"),
            ("markweave", "0.6.1"),
        ),
        request_digest=request_digest,
        idempotency_digest=idempotency_digest,
        correlation_id=str(uuid4()),
        created_at=created_at,
    )


def proof(
    attempt_id: UUID,
    unit_id: UUID,
    principal: AuthenticatedPrincipal = PRINCIPAL,
) -> TerminationProof:
    return TerminationProof(
        uuid4(),
        attempt_id,
        unit_id,
        principal,
        "reverse-policy-v1",
        EvidenceDigest("sha256:" + "b" * 64),
        EvidenceDigest("sha256:" + "c" * 64),
        EvidenceDigest("sha256:" + "d" * 64),
    )


def trace() -> ReversionTraceMetadata:
    return ReversionTraceMetadata(
        1,
        "firecrawl-anydoc",
        "0.2.4",
        FormatFamily.WORD,
        "docx",
        ReverseOutputMode.MARKDOWN,
        0,
        0,
        0,
    )


def complete_empty_reconciliation(
    repository: SqlReversionJobRepository,
    principal: AuthenticatedPrincipal = PRINCIPAL,
) -> None:
    """Establish explicit fail-closed readiness for repository contract tests."""

    token = uuid4()
    cursor = repository.begin_reconciliation(
        principal, "contract-reconciler", token, NOW, LEASE_END
    )
    for _ in range(2):
        repository.record_reconciliation_page(
            principal,
            token,
            ReconciliationResponse(
                uuid4(), principal.principal_id, cursor, cursor, None, True
            ),
            NOW,
        )
    repository.complete_reconciliation(principal, token, NOW)


def exercise_reversion_job_repository_contract(  # noqa: PLR0915
    repository: SqlReversionJobRepository, owner_id: UUID, other_owner_id: UUID
) -> None:
    complete_empty_reconciliation(repository)
    first_submission = submission(owner_id, idempotency_digest="3" * 64)
    first, replayed = repository.create(first_submission)
    assert not replayed
    assert not first.source_ready
    replay, replayed = repository.create(
        submission(owner_id, idempotency_digest="3" * 64)
    )
    assert replayed and replay.id == first.id
    with pytest.raises(ReversionJobConflictError):
        repository.create(
            submission(
                owner_id,
                idempotency_digest="3" * 64,
                request_digest="4" * 64,
            )
        )
    assert repository.get_owner(first.id, other_owner_id) is None
    assert repository.get_owner(first.id, owner_id) == first
    first = repository.activate_source(first.id, NOW)
    assert first.source_ready

    claimed = repository.claim("reverse-worker", PRINCIPAL, NOW, LEASE_END)
    assert claimed is not None and claimed.id == first.id
    assert claimed.current_attempt_id is not None and claimed.lease_token is not None
    attempts = repository.list_attempts(first.id)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.attempt_id == claimed.current_attempt_id
    first_sequence = attempt.create_sequence
    assert first_sequence > 0
    assert repository.heartbeat(
        ReversionLeaseHeartbeat(
            first.id,
            attempt.attempt_id,
            "reverse-worker",
            claimed.lease_token,
            NOW + timedelta(seconds=1),
            LEASE_END + timedelta(seconds=1),
            ReversionJobStep.CONVERTING,
        )
    )
    with pytest.raises(ReversionProofRequiredError):
        repository.succeed(
            first.id,
            attempt.attempt_id,
            "reverse-worker",
            claimed.lease_token,
            reversion_result_object_id(first.id, 1),
            ReverseOutputMode.MARKDOWN,
            "5" * 64,
            64,
            trace(),
            NOW + timedelta(seconds=2),
            RETENTION_END,
        )

    intent = repository.reserve_create_intent(
        first.id,
        attempt.attempt_id,
        "reverse-worker",
        claimed.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        NOW + timedelta(seconds=2),
    )
    assert intent.create_sequence == first_sequence
    assert (
        repository.reserve_create_intent(
            first.id,
            attempt.attempt_id,
            "reverse-worker",
            claimed.lease_token,
            "reverse-policy-v1",
            POLICY_SPECIFICATION,
            NOW + timedelta(seconds=2),
        )
        == intent
    )
    with pytest.raises(ReversionJobConflictError):
        repository.reserve_create_intent(
            first.id,
            attempt.attempt_id,
            "reverse-worker",
            claimed.lease_token,
            "another-policy",
            POLICY_SPECIFICATION,
            NOW + timedelta(seconds=2),
        )

    expired = LEASE_END + timedelta(seconds=2)
    assert repository.recover_expired_leases(
        expired, RETENTION_END, NOW
    ) == ReversionLeaseRecoveryResult(0, 0, 0)
    blocked = repository.get_internal(first.id)
    assert blocked is not None and blocked.state is ReversionJobState.RUNNING
    recovery = repository.claim_recovery(
        "recovery-worker", expired, expired + timedelta(seconds=30), 10
    )
    assert len(recovery) == 1 and recovery[0].recovery_token is not None
    unit_id = uuid4()
    termination = proof(attempt.attempt_id, unit_id)
    with pytest.raises(ReversionJobLeaseLostError):
        repository.record_recovery_termination_proof(
            first.id, attempt.attempt_id, uuid4(), termination, expired
        )
    recovered_attempt = repository.record_recovery_termination_proof(
        first.id,
        attempt.attempt_id,
        recovery[0].recovery_token,
        termination,
        expired,
    )
    assert recovered_attempt.termination_proof == termination
    assert repository.recover_expired_leases(
        expired, RETENTION_END, NOW
    ) == ReversionLeaseRecoveryResult(1, 0, 0)
    recovered = repository.get_internal(first.id)
    assert recovered is not None and recovered.state is ReversionJobState.QUEUED

    second_claim = repository.claim(
        "reverse-worker-2", PRINCIPAL, expired, expired + timedelta(seconds=30)
    )
    assert second_claim is not None and second_claim.current_attempt_id is not None
    assert second_claim.lease_token is not None
    second_attempt = repository.get_attempt(second_claim.current_attempt_id)
    assert second_attempt is not None
    assert second_attempt.create_sequence == first_sequence + 1
    repository.reserve_create_intent(
        first.id,
        second_attempt.attempt_id,
        "reverse-worker-2",
        second_claim.lease_token,
        "reverse-policy-v1",
        POLICY_SPECIFICATION,
        expired + timedelta(seconds=1),
    )
    second_proof = proof(second_attempt.attempt_id, uuid4())
    recorded = repository.record_active_termination_proof(
        first.id,
        second_attempt.attempt_id,
        "reverse-worker-2",
        second_claim.lease_token,
        second_proof,
        expired + timedelta(seconds=2),
    )
    assert recorded.termination_proof == second_proof
    with pytest.raises(ReversionJobConflictError):
        repository.succeed(
            first.id,
            second_attempt.attempt_id,
            "reverse-worker-2",
            second_claim.lease_token,
            uuid4(),
            ReverseOutputMode.MARKDOWN,
            "5" * 64,
            64,
            trace(),
            expired + timedelta(seconds=3),
            RETENTION_END,
        )
    succeeded = repository.succeed(
        first.id,
        second_attempt.attempt_id,
        "reverse-worker-2",
        second_claim.lease_token,
        reversion_result_object_id(first.id, 2),
        ReverseOutputMode.MARKDOWN,
        "5" * 64,
        64,
        trace(),
        expired + timedelta(seconds=3),
        RETENTION_END,
    )
    assert succeeded.state is ReversionJobState.SUCCEEDED
    assert succeeded.result_sha256 == "5" * 64
    acknowledged = repository.acknowledge_termination_proof(
        second_attempt.attempt_id, second_proof.proof_id, expired + timedelta(seconds=4)
    )
    assert acknowledged.proof_acknowledged_at is not None
    assert (
        repository.acknowledge_termination_proof(
            second_attempt.attempt_id,
            second_proof.proof_id,
            expired + timedelta(seconds=5),
        ).proof_acknowledged_at
        == acknowledged.proof_acknowledged_at
    )
    retained = repository.list_attempts(first.id)
    assert [item.create_sequence for item in retained] == [
        first_sequence,
        first_sequence + 1,
    ]
    assert [item.termination_proof for item in retained] == [termination, second_proof]

    expired_objects = repository.expire_terminal(
        "cleanup-worker",
        RETENTION_END + timedelta(seconds=1),
        RETENTION_END + timedelta(seconds=31),
        10,
    )
    assert len(expired_objects) == 1
    assert expired_objects[0].result_object_ids == (
        reversion_result_object_id(first.id, 1),
        reversion_result_object_id(first.id, 2),
    )
    assert repository.complete_cleanup(first.id, expired_objects[0].cleanup_token)
    assert not repository.complete_cleanup(first.id, expired_objects[0].cleanup_token)

    page = repository.list_owner(owner_id, offset=0, limit=10)
    assert page.total == 1 and page.items[0].state is ReversionJobState.EXPIRED
