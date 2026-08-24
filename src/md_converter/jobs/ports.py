"""Persistence and processing ports for durable conversion jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from md_converter.jobs.models import (
    ConversionJob,
    ExpiredJobObjects,
    JobFailure,
    JobPage,
    JobProcessResult,
    JobStep,
    JobSubmission,
    LeaseHeartbeat,
)
from md_converter.jobs.policy import JobExecutionBudget


class CancellationProbe(Protocol):
    """Callable cancellation boundary carrying the current execution budget."""

    @property
    def budget(self) -> JobExecutionBudget | None: ...

    def __call__(self) -> bool: ...


class JobRepository(Protocol):
    """Atomic queue contract shared by SQLite and PostgreSQL."""

    def create(self, submission: JobSubmission) -> tuple[ConversionJob, bool]: ...

    def activate_source(self, job_id: UUID, now: datetime) -> ConversionJob: ...

    def get(self, job_id: UUID) -> ConversionJob | None: ...

    def list_owner(self, owner_id: UUID, *, offset: int, limit: int) -> JobPage: ...

    def request_cancel(
        self,
        job_id: UUID,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob | None: ...

    def claim(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> ConversionJob | None: ...

    def heartbeat(self, heartbeat: LeaseHeartbeat) -> bool: ...

    def cancellation_requested(
        self, job_id: UUID, worker_id: str, lease_token: UUID
    ) -> bool: ...

    def succeed(  # noqa: PLR0913, PLR0917 - atomic transition contract
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        now: datetime,
        expires_at: datetime,
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

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
    ) -> int: ...

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredJobObjects, ...]: ...

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool: ...


class JobProcessor(Protocol):
    """T15-integrated document processing boundary invoked by a worker."""

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult: ...
