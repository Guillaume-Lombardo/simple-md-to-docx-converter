"""Public-owner and internal-worker ports for durable reverse jobs."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.reversion_jobs.models import (
    ExpiredReversionObjects,
    ReversionAttempt,
    ReversionFailure,
    ReversionJob,
    ReversionJobPage,
    ReversionLeaseHeartbeat,
    ReversionLeaseRecoveryResult,
    ReversionSubmission,
    ReversionTraceMetadata,
)
from markweave.reversions.models import ReverseOutputMode


class OwnerReversionRepository(Protocol):  # pragma: no cover - structural port
    """Only owner-bound reads and lifecycle mutations exposed to public services."""

    def get_owner(self, job_id: UUID, owner_id: UUID) -> ReversionJob | None: ...

    def list_owner(
        self, owner_id: UUID, *, offset: int, limit: int
    ) -> ReversionJobPage: ...

    def request_cancel(
        self, job_id: UUID, owner_id: UUID, now: datetime, expires_at: datetime
    ) -> ReversionJob | None: ...


class ReversionSubmissionRepository(Protocol):  # pragma: no cover - structural port
    def create(self, submission: ReversionSubmission) -> tuple[ReversionJob, bool]: ...

    def activate_source(self, job_id: UUID, now: datetime) -> ReversionJob: ...


class ReversionRepository(  # pragma: no cover - structural port
    OwnerReversionRepository, ReversionSubmissionRepository, Protocol
):
    """Public submission and owner-lifecycle repository boundary."""


class ReversionWorkerRepository(Protocol):  # pragma: no cover - structural port
    """Internal-only unscoped lookup and exact attempt lifecycle."""

    def get_internal(self, job_id: UUID) -> ReversionJob | None: ...

    def get_attempt(self, attempt_id: UUID) -> ReversionAttempt | None: ...

    def list_attempts(self, job_id: UUID) -> tuple[ReversionAttempt, ...]: ...

    def claim(
        self,
        worker_id: str,
        principal: AuthenticatedPrincipal,
        now: datetime,
        lease_expires_at: datetime,
        running_limit: int,
    ) -> ReversionJob | None: ...

    def heartbeat(self, heartbeat: ReversionLeaseHeartbeat) -> bool: ...

    def cancellation_requested(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, lease_token: UUID
    ) -> bool: ...

    def reserve_create_intent(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        policy_revision: str,
        policy_specification: EvidenceDigest,
        now: datetime,
    ) -> ReversionAttempt: ...

    def record_broker_unit(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        unit_id: UUID,
        now: datetime,
    ) -> ReversionAttempt: ...

    def record_active_termination_proof(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        proof: TerminationProof,
        now: datetime,
    ) -> ReversionAttempt: ...

    def claim_recovery(
        self,
        recovery_owner: str,
        now: datetime,
        recovery_expires_at: datetime,
        limit: int,
    ) -> tuple[ReversionAttempt, ...]: ...

    def record_recovery_termination_proof(
        self,
        job_id: UUID,
        attempt_id: UUID,
        recovery_token: UUID,
        proof: TerminationProof,
        now: datetime,
    ) -> ReversionAttempt: ...

    def acknowledge_termination_proof(
        self, attempt_id: UUID, proof_id: UUID, now: datetime
    ) -> ReversionAttempt: ...

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
        limit: int,
    ) -> ReversionLeaseRecoveryResult: ...

    def succeed(  # noqa: PLR0913, PLR0917 - atomic publication contract
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        result_mode: ReverseOutputMode,
        result_sha256: str,
        result_size: int,
        trace: ReversionTraceMetadata,
        now: datetime,
        expires_at: datetime,
    ) -> ReversionJob: ...

    def fail(self, failure: ReversionFailure) -> ReversionJob: ...

    def finish_cancelled(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ReversionJob: ...

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredReversionObjects, ...]: ...

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool: ...
