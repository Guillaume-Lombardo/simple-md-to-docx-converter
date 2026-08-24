"""Lease-owning worker orchestration with fenced atomic result publication."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event, Lock, Thread
from time import monotonic
from typing import Protocol

from md_converter.conversion.errors import ConversionError
from md_converter.jobs.errors import JobLeaseLostError, JobProcessingCancelled
from md_converter.jobs.models import (
    ConversionJob,
    JobFailure,
    JobProcessResult,
    JobState,
    JobStep,
    LeaseHeartbeat,
    result_object_id,
)
from md_converter.jobs.policy import JobExecutionBudget
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.observability import (
    OperationalMetrics,
    bind_correlation_id,
    log_event,
    require_worker_id,
    reset_correlation_id,
)
from md_converter.storage import ObjectKey, ObjectScope, ObjectStore


class MaintenanceCleaner(Protocol):
    """Additional bounded retention work sharing the periodic worker cadence."""

    def cleanup(self, *, limit: int) -> int: ...


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


@dataclass(slots=True)
class _ProgressState:
    """Thread-safe current progress shared with the keepalive."""

    step: JobStep
    percentage: int


@dataclass(frozen=True, slots=True)
class _BudgetCancellationProbe:
    """Worker-owned cancellation probe visible to the processor boundary."""

    budget: JobExecutionBudget | None
    lease_lost: Event
    duration_exhausted: Event
    monotonic_clock: Callable[[], float]
    durable_requested: Callable[[], bool]

    def __call__(self) -> bool:
        if self.budget is not None and self.budget.exhausted(self.monotonic_clock()):
            self.duration_exhausted.set()
        return (
            self.lease_lost.is_set()
            or self.duration_exhausted.is_set()
            or self.durable_requested()
        )


class ConversionWorker:
    """Claim one job and publish only with its unique fencing token."""

    def __init__(
        self,
        *,
        worker_id: str,
        runtime: WorkerRuntime,
        policy: WorkerPolicy,
    ) -> None:
        self._worker_id = require_worker_id(worker_id)
        self._repository = runtime.repository
        self._objects = runtime.objects
        self._processor = runtime.processor
        self._clock = runtime.clock
        self._monotonic_clock = runtime.monotonic_clock
        self._maintenance = runtime.maintenance
        self._metrics = runtime.metrics
        self._policy = policy

    def run_once(self) -> bool:  # noqa: PLR0912, PLR0915 - lifecycle is explicit
        now = self._clock()
        job = self._repository.claim(
            self._worker_id,
            now,
            now + timedelta(seconds=self._policy.lease_seconds),
        )
        if job is None:
            return False
        if job.lease_token is None:  # guarded by the domain model
            raise JobLeaseLostError("Conversion job lease token is missing")
        lease_token = job.lease_token
        started_monotonic = self._monotonic_clock()
        budget = (
            None
            if self._policy.max_job_duration_seconds is None
            else JobExecutionBudget(
                duration_seconds=self._policy.max_job_duration_seconds,
                started_monotonic=started_monotonic,
                deadline_monotonic=(
                    started_monotonic + self._policy.max_job_duration_seconds
                ),
            )
        )

        progress_state = _ProgressState(job.step, job.progress)
        step_started = started_monotonic
        progress_lock = Lock()
        heartbeat_operation = Lock()
        keepalive_stop = Event()
        lease_lost = Event()
        duration_exhausted = Event()
        keepalive_errors: list[BaseException] = []

        def heartbeat() -> None:
            heartbeat_at = self._clock()
            with progress_lock:
                step = progress_state.step
                percentage = progress_state.percentage
            if not self._repository.heartbeat(
                LeaseHeartbeat(
                    job_id=job.id,
                    worker_id=self._worker_id,
                    lease_token=lease_token,
                    now=heartbeat_at,
                    lease_expires_at=heartbeat_at
                    + timedelta(seconds=self._policy.lease_seconds),
                    step=step,
                    progress=percentage,
                )
            ):
                raise JobLeaseLostError("Conversion job lease was lost")

        def keepalive() -> None:
            while not keepalive_stop.wait(self._policy.heartbeat_seconds):
                with heartbeat_operation:
                    try:
                        heartbeat()
                    except BaseException as error:  # propagated on the owner thread
                        keepalive_errors.append(error)
                        lease_lost.set()
                        return

        cancelled = _BudgetCancellationProbe(
            budget=budget,
            lease_lost=lease_lost,
            duration_exhausted=duration_exhausted,
            monotonic_clock=self._monotonic_clock,
            durable_requested=lambda: self._repository.cancellation_requested(
                job.id, self._worker_id, lease_token
            ),
        )

        def progress(step: JobStep, percentage: int) -> None:
            nonlocal step_started
            with progress_lock:
                previous_step = progress_state.step
                if step is not previous_step and self._metrics is not None:
                    observed_at = self._monotonic_clock()
                    self._metrics.record_step_duration(
                        previous_step.value, max(0.0, observed_at - step_started)
                    )
                    step_started = observed_at
                progress_state.step = step
                progress_state.percentage = percentage
            with heartbeat_operation:
                heartbeat()

        keepalive_thread = Thread(
            target=keepalive,
            name=f"{self._worker_id}-heartbeat",
            daemon=False,
        )
        correlation_token = bind_correlation_id(job.correlation_id)
        log_event(
            "job_processing_started",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            version_id=str(job.template_version_id),
            worker_id=self._worker_id,
            state=job.state.value,
            step=job.step.value,
        )
        keepalive_thread.start()
        try:
            result: JobProcessResult | None = None
            processing_error: BaseException | None = None
            try:
                result = self._processor.process(
                    job,
                    cancelled=cancelled,
                    progress=progress,
                )
                if cancelled():
                    raise JobProcessingCancelled
            except BaseException as error:
                processing_error = error

            with heartbeat_operation:
                if keepalive_errors:
                    raise keepalive_errors[0]
                if isinstance(processing_error, JobLeaseLostError):
                    raise processing_error
                if budget is not None and budget.exhausted(self._monotonic_clock()):
                    duration_exhausted.set()
                durable_cancelled = self._repository.cancellation_requested(
                    job.id, self._worker_id, lease_token
                )
                if durable_cancelled:
                    self._finish_cancelled(job)
                    keepalive_stop.set()
                    return True
                if duration_exhausted.is_set():
                    self._finish_budget_exceeded(job)
                    keepalive_stop.set()
                    return True
                if isinstance(processing_error, JobProcessingCancelled):
                    self._finish_cancelled(job)
                    keepalive_stop.set()
                    return True
                if isinstance(processing_error, ConversionError):
                    self._finish_failed(job, processing_error)
                    keepalive_stop.set()
                    return True
                if processing_error is not None:
                    raise processing_error
            if result is None:  # defensive against a broken processor implementation
                raise RuntimeError("Job processor returned no result")

            publication_id = result_object_id(job.id, job.attempt)
            result_key = ObjectKey(ObjectScope.RESULT, job.owner_id, publication_id)
            self._objects.put(result_key, result.content)
            try:
                with heartbeat_operation:
                    if keepalive_errors:
                        raise keepalive_errors[0]
                    finished_at = self._clock()
                    finished = self._repository.succeed(
                        job.id,
                        self._worker_id,
                        lease_token,
                        publication_id,
                        finished_at,
                        self._expires_at(finished_at),
                    )
                    keepalive_stop.set()
            except BaseException:
                self._objects.delete(result_key)
                raise
            finished_state = (
                finished.state
                if isinstance(finished, ConversionJob)
                else JobState.SUCCEEDED
            )
            finished_step = (
                finished.step
                if isinstance(finished, ConversionJob)
                else JobStep.COMPLETE
            )
            if finished_state is JobState.CANCELLED:
                self._objects.delete(result_key)
            log_event(
                "job_processing_completed",
                job_id=str(job.id),
                owner_id=str(job.owner_id),
                version_id=str(job.template_version_id),
                worker_id=self._worker_id,
                state=finished_state.value,
                step=finished_step.value,
            )
            return True
        finally:
            keepalive_stop.set()
            keepalive_thread.join()
            if self._metrics is not None:
                with progress_lock:
                    final_step = progress_state.step
                self._metrics.record_step_duration(
                    final_step.value,
                    max(0.0, self._monotonic_clock() - step_started),
                )
            reset_correlation_id(correlation_token)

    def recover(self) -> int:
        """Recover expired work and abandoned durable source reservations."""

        now = self._clock()
        recovered = self._repository.recover_expired_leases(
            now,
            self._expires_at(now),
            now - timedelta(seconds=self._policy.incomplete_submission_seconds),
        )
        if self._metrics is not None:
            self._metrics.record_recovery(recovered)
        if recovered:
            log_event("job_recovery_completed", operation="lease_recovery")
        return recovered

    def cleanup(self, *, limit: int) -> int:
        """Claim, delete, and acknowledge a bounded retry-safe cleanup batch."""

        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        now = self._clock()
        expired = self._repository.expire_terminal(
            self._worker_id,
            now,
            now + timedelta(seconds=self._policy.lease_seconds),
            limit,
        )
        for candidate in expired:
            self._objects.delete(
                ObjectKey(
                    ObjectScope.UPLOAD,
                    candidate.owner_id,
                    candidate.source_object_id,
                )
            )
            for object_id in candidate.result_object_ids:
                self._objects.delete(
                    ObjectKey(ObjectScope.RESULT, candidate.owner_id, object_id)
                )
            self._repository.complete_cleanup(candidate.job_id, candidate.cleanup_token)
        maintained = (
            self._maintenance.cleanup(limit=limit)
            if self._maintenance is not None
            else 0
        )
        if self._metrics is not None:
            self._metrics.record_expiration(len(expired))
        if expired:
            log_event("job_expiration_completed", operation="retention_cleanup")
        return len(expired) + maintained

    def _finish_cancelled(self, job: ConversionJob) -> None:
        if job.lease_token is None:
            raise JobLeaseLostError("Conversion job lease token is missing")
        finished_at = self._clock()
        self._repository.finish_cancelled(
            job.id,
            self._worker_id,
            job.lease_token,
            finished_at,
            self._expires_at(finished_at),
        )
        log_event(
            "job_processing_completed",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            version_id=str(job.template_version_id),
            worker_id=self._worker_id,
            state=JobState.CANCELLED.value,
            step=job.step.value,
        )

    def _finish_failed(self, job: ConversionJob, error: ConversionError) -> None:
        if job.lease_token is None:
            raise JobLeaseLostError("Conversion job lease token is missing")
        finished_at = self._clock()
        self._repository.fail(
            JobFailure(
                job_id=job.id,
                worker_id=self._worker_id,
                lease_token=job.lease_token,
                code=error.code.value,
                message=str(error),
                now=finished_at,
                expires_at=self._expires_at(finished_at),
            )
        )
        if self._metrics is not None:
            self._metrics.record_failure(error.code.value)
        log_event(
            "job_processing_failed",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            version_id=str(job.template_version_id),
            worker_id=self._worker_id,
            state=JobState.FAILED.value,
            step=job.step.value,
            error_code=error.code.value,
        )

    def _finish_budget_exceeded(self, job: ConversionJob) -> None:
        if job.lease_token is None:
            raise JobLeaseLostError("Conversion job lease token is missing")
        finished_at = self._clock()
        self._repository.fail(
            JobFailure(
                job_id=job.id,
                worker_id=self._worker_id,
                lease_token=job.lease_token,
                code="resource_budget_exceeded",
                message="Conversion exceeded its configured duration budget.",
                now=finished_at,
                expires_at=self._expires_at(finished_at),
            )
        )
        if self._metrics is not None:
            self._metrics.record_failure("resource_budget_exceeded")
        log_event(
            "job_processing_failed",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            version_id=str(job.template_version_id),
            worker_id=self._worker_id,
            state=JobState.FAILED.value,
            step=job.step.value,
            error_code="resource_budget_exceeded",
        )

    def _expires_at(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._policy.result_retention_seconds)
