"""Persistence and processing ports for durable conversion jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from markweave.jobs.models import (
    ConversionJob,
    ExpiredJobObjects,
    JobFailure,
    JobPage,
    JobProcessResult,
    JobStep,
    JobSubmission,
    LeaseHeartbeat,
)
from markweave.jobs.policy import JobExecutionBudget


class CancellationProbe(Protocol):
    """Callable cancellation boundary carrying the current execution budget."""

    @property
    def budget(self) -> JobExecutionBudget | None: ...

    def __call__(self) -> bool: ...


class JobSubmissionRepository(Protocol):
    """Atomic submission, idempotency, admission, and source activation."""

    def create(self, submission: JobSubmission) -> tuple[ConversionJob, bool]: ...

    def activate_source(self, job_id: UUID, now: datetime) -> ConversionJob: ...


class JobQueryRepository(Protocol):
    """Owner-scoped job identity and pagination queries."""

    def get(self, job_id: UUID) -> ConversionJob | None: ...

    def list_owner(self, owner_id: UUID, *, offset: int, limit: int) -> JobPage: ...


class JobTerminalRepository(Protocol):
    """Owner cancellation and fenced terminal lifecycle transitions."""

    def request_cancel(
        self,
        job_id: UUID,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob | None: ...

    def succeed(  # noqa: PLR0913, PLR0917 - atomic transition contract
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        now: datetime,
        expires_at: datetime,
        result_manifest_object_id: UUID | None = None,
    ) -> ConversionJob: ...

    def fail(self, failure: JobFailure) -> ConversionJob: ...

    def finish_cancelled(
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob: ...


class JobLeaseRepository(Protocol):
    """Exclusive claims, renewable fencing leases, probes, and recovery."""

    def claim(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> ConversionJob | None: ...

    def heartbeat(self, heartbeat: LeaseHeartbeat) -> bool: ...

    def cancellation_requested(
        self, job_id: UUID, worker_id: str, lease_token: UUID
    ) -> bool: ...

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
    ) -> int: ...


class JobCleanupRepository(Protocol):
    """Fenced, retryable object cleanup for expired terminal jobs."""

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredJobObjects, ...]: ...

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool: ...


class JobRepository(
    JobSubmissionRepository,
    JobQueryRepository,
    JobTerminalRepository,
    JobLeaseRepository,
    JobCleanupRepository,
    Protocol,
):
    """Complete provider-neutral job contract shared by both profiles."""


class JobProcessor(Protocol):
    """T15-integrated document processing boundary invoked by a worker."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult: ...
