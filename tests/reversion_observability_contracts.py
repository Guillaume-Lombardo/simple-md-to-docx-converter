"""Shared SQLite/PostgreSQL contract for reverse operational gauges."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.reconciliation_protocol import (
    ReconciliationResponse,
    ReconciliationTombstone,
)
from markweave.observability import QueueObserver
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    POLICY_SPECIFICATION,
    PRINCIPAL,
    complete_empty_reconciliation,
    proof,
    submission,
)


def exercise_reverse_observability_contract(
    repository: SqlReversionJobRepository,
    observer: QueueObserver,
    owner_id: UUID,
) -> None:
    """Prove every reverse safety gauge transition against a real database."""

    baseline = observer.observe_queue(NOW)
    complete_empty_reconciliation(repository)
    queued, _ = repository.create(submission(owner_id))
    repository.activate_source(queued.id, NOW)
    observed = observer.observe_queue(NOW + timedelta(seconds=5))
    assert observed.reversion_depth == 1
    assert observed.reversion_oldest_age_seconds == 5
    assert observed.reversion_active_jobs == 0
    assert observed.shared_capacity_used == baseline.shared_capacity_used + 1
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
    expired = observer.observe_queue(LEASE_END + timedelta(microseconds=1))
    assert expired.reversion_depth == 0
    assert expired.reversion_active_jobs == 1
    assert expired.reversion_proof_blocked_attempts == 1

    recovery = repository.claim_recovery(
        "reverse-observer-recovery",
        LEASE_END + timedelta(seconds=1),
        LEASE_END + timedelta(seconds=31),
        1,
    )
    assert len(recovery) == 1 and recovery[0].recovery_token is not None
    recovered_proof = proof(attempt.attempt_id, uuid4())
    recorded = repository.record_recovery_termination_proof(
        claimed.id,
        attempt.attempt_id,
        recovery[0].recovery_token,
        recovered_proof,
        LEASE_END + timedelta(seconds=2),
    )
    proof_pending = observer.observe_queue(LEASE_END + timedelta(seconds=2))
    assert proof_pending.reversion_proof_blocked_attempts == 0
    assert proof_pending.reversion_proof_ack_backlog == 1
    repository.acknowledge_termination_proof(
        recorded.attempt_id,
        recovered_proof.proof_id,
        LEASE_END + timedelta(seconds=3),
    )
    assert (
        observer.observe_queue(
            LEASE_END + timedelta(seconds=3)
        ).reversion_proof_ack_backlog
        == 0
    )

    _exercise_orphan_ack_and_reconciliation(repository, observer)
    _exercise_incomplete_and_expired_reconciliation(repository, observer)


def _exercise_orphan_ack_and_reconciliation(
    repository: SqlReversionJobRepository, observer: QueueObserver
) -> None:
    principal = AuthenticatedPrincipal(uuid4())
    token = uuid4()
    repository.begin_reconciliation(
        principal, "orphan-reconciler", token, NOW, LEASE_END
    )
    tombstone = ReconciliationTombstone(
        7,
        POLICY_SPECIFICATION,
        proof(uuid4(), uuid4(), principal=principal),
    )
    repository.record_reconciliation_page(
        principal,
        token,
        ReconciliationResponse(uuid4(), principal.principal_id, 0, 7, tombstone, False),
        NOW,
    )
    pending = observer.observe_queue(NOW)
    assert pending.reversion_proof_ack_backlog == 1
    assert pending.reversion_reconciliation_pending == 1
    repository.mark_reconciliation_acknowledged(principal, token, tombstone, NOW)
    assert observer.observe_queue(NOW).reversion_proof_ack_backlog == 0
    _complete_reconciliation(repository, principal, token, cursor=7)
    assert observer.observe_queue(NOW).reversion_reconciliation_pending == 0


def _exercise_incomplete_and_expired_reconciliation(
    repository: SqlReversionJobRepository, observer: QueueObserver
) -> None:
    incomplete = AuthenticatedPrincipal(uuid4())
    assert repository.claim("incomplete-observer", incomplete, NOW, LEASE_END) is None
    assert observer.observe_queue(NOW).reversion_reconciliation_pending == 1
    complete_empty_reconciliation(repository, incomplete)
    assert observer.observe_queue(NOW).reversion_reconciliation_pending == 0

    expired = AuthenticatedPrincipal(uuid4())
    old_token = uuid4()
    repository.begin_reconciliation(
        expired,
        "expired-reconciler",
        old_token,
        NOW,
        NOW + timedelta(seconds=1),
    )
    after_expiry = NOW + timedelta(seconds=2)
    assert observer.observe_queue(after_expiry).reversion_reconciliation_pending == 1
    replacement = uuid4()
    repository.begin_reconciliation(
        expired,
        "replacement-reconciler",
        replacement,
        after_expiry,
        after_expiry + timedelta(seconds=30),
    )
    _complete_reconciliation(
        repository, expired, replacement, cursor=0, now=after_expiry
    )
    assert observer.observe_queue(after_expiry).reversion_reconciliation_pending == 0


def _complete_reconciliation(
    repository: SqlReversionJobRepository,
    principal: AuthenticatedPrincipal,
    token: UUID,
    *,
    cursor: int,
    now: datetime = NOW,
) -> None:
    for _ in range(2):
        repository.record_reconciliation_page(
            principal,
            token,
            ReconciliationResponse(
                uuid4(), principal.principal_id, cursor, cursor, None, True
            ),
            now,
        )
    repository.complete_reconciliation(principal, token, now)
