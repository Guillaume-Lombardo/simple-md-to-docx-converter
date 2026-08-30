"""Small composition root for durable conversion-worker orchestration."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from markweave.jobs.models import JobProcessResult
from markweave.jobs.ports import JobProcessor, JobRepository
from markweave.jobs.worker_execution import (
    JobClaimService,
    JobExecutionService,
    JobFailureService,
    JobHeartbeatService,
    template_log_fields,
)
from markweave.jobs.worker_maintenance import (
    MaintenanceCleaner,
    WorkerMaintenanceService,
)
from markweave.jobs.worker_publication import JobPublicationService
from markweave.observability import (
    OperationalMetrics,
    bind_correlation_id,
    log_event,
    require_worker_id,
    reset_correlation_id,
)
from markweave.storage import ObjectStore


@dataclass(frozen=True, slots=True)
class WorkerPolicy:
    """Caller-owned timings whose production values remain assigned to T18."""

    lease_seconds: float
    heartbeat_seconds: float
    result_retention_seconds: float
    incomplete_submission_seconds: float
    max_job_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        values = (
            self.lease_seconds,
            self.heartbeat_seconds,
            self.result_retention_seconds,
            self.incomplete_submission_seconds,
        )
        if any(
            isinstance(value, bool) or not math.isfinite(value) or value <= 0
            for value in values
        ):
            raise ValueError("Worker policy durations must be positive")
        if self.max_job_duration_seconds is not None and (
            isinstance(self.max_job_duration_seconds, bool)
            or not math.isfinite(self.max_job_duration_seconds)
            or self.max_job_duration_seconds <= 0
        ):
            raise ValueError("Worker job duration budget must be positive")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("Worker heartbeat must be shorter than its lease")


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Worker ports grouped independently of caller-owned policy."""

    repository: JobRepository
    objects: ObjectStore
    processor: JobProcessor
    clock: Callable[[], datetime]
    monotonic_clock: Callable[[], float] = monotonic
    maintenance: MaintenanceCleaner | None = None
    metrics: OperationalMetrics | None = None


class ConversionWorker:
    """Orchestrate one fenced claim through execution and publication."""

    def __init__(
        self,
        *,
        worker_id: str,
        runtime: WorkerRuntime,
        policy: WorkerPolicy,
    ) -> None:
        self._worker_id = require_worker_id(worker_id)
        self._repository = runtime.repository
        self._clock = runtime.clock
        self._monotonic_clock = runtime.monotonic_clock
        self._metrics = runtime.metrics
        self._policy = policy
        self._claims = JobClaimService(
            runtime.repository,
            self._worker_id,
            runtime.clock,
            runtime.monotonic_clock,
            policy.lease_seconds,
            policy.max_job_duration_seconds,
        )
        self._execution = JobExecutionService(runtime.processor)
        self._failures = JobFailureService(
            runtime.repository,
            self._worker_id,
            runtime.clock,
            policy.result_retention_seconds,
            runtime.metrics,
        )
        self._publication = JobPublicationService(
            runtime.repository,
            runtime.objects,
            self._worker_id,
            runtime.clock,
            policy.result_retention_seconds,
        )
        self._maintenance = WorkerMaintenanceService(
            runtime.repository,
            runtime.objects,
            self._worker_id,
            runtime.clock,
            policy.lease_seconds,
            policy.result_retention_seconds,
            policy.incomplete_submission_seconds,
            runtime.maintenance,
            runtime.metrics,
        )

    def run_once(self, *, shutdown_requested: Callable[[], bool] | None = None) -> bool:
        """Run one explicit claim, execution, resolution, and publication flow."""

        claimed = self._claims.claim()
        if claimed is None:
            return False
        job = claimed.job
        heartbeat = JobHeartbeatService(
            claimed=claimed,
            repository=self._repository,
            worker_id=self._worker_id,
            clock=self._clock,
            monotonic_clock=self._monotonic_clock,
            lease_seconds=self._policy.lease_seconds,
            heartbeat_seconds=self._policy.heartbeat_seconds,
            metrics=self._metrics,
            shutdown_requested=shutdown_requested or (lambda: False),
        )
        correlation_token = bind_correlation_id(job.correlation_id)
        log_event(
            "job_processing_started",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            worker_id=self._worker_id,
            state=job.state.value,
            step=job.step.value,
            **template_log_fields(job),
        )
        heartbeat.start()
        try:
            outcome = self._execution.execute(
                job,
                cancelled=heartbeat.cancellation,
                progress=heartbeat.progress,
            )
            result: JobProcessResult | None = self._failures.resolve(
                claimed, outcome, heartbeat
            )
            if result is None:
                return True
            published = self._publication.publish(
                job, claimed.lease_token, result, heartbeat
            )
            log_event(
                "job_processing_completed",
                job_id=str(job.id),
                owner_id=str(job.owner_id),
                worker_id=self._worker_id,
                state=published.state.value,
                step=published.step.value,
                **template_log_fields(job),
            )
            return True
        finally:
            heartbeat.stop()
            reset_correlation_id(correlation_token)

    def recover(self) -> int:
        """Recover expired work and abandoned durable source reservations."""

        return self._maintenance.recover()

    def cleanup(self, *, limit: int) -> int:
        """Claim, delete, and acknowledge a bounded retry-safe cleanup batch."""

        return self._maintenance.cleanup(limit=limit)
