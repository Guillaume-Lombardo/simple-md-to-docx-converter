"""Claim, heartbeat, execution, and terminal-failure worker services."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event, Lock, Thread
from typing import NotRequired, TypedDict
from uuid import UUID

from markweave.conversion.errors import ConversionError
from markweave.jobs.errors import JobLeaseLostError, JobProcessingCancelled
from markweave.jobs.models import (
    ConversionJob,
    JobFailure,
    JobProcessResult,
    JobState,
    JobStep,
    LeaseHeartbeat,
)
from markweave.jobs.policy import JobExecutionBudget
from markweave.jobs.ports import CancellationProbe, JobProcessor, JobRepository
from markweave.observability import OperationalMetrics, log_event


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A claimed job with the fencing and monotonic execution context."""

    job: ConversionJob
    lease_token: UUID
    budget: JobExecutionBudget | None
    started_monotonic: float


@dataclass(frozen=True, slots=True)
class JobClaimService:
    """Claim one durable job and establish its execution budget."""

    repository: JobRepository
    worker_id: str
    clock: Callable[[], datetime]
    monotonic_clock: Callable[[], float]
    lease_seconds: float
    max_job_duration_seconds: float | None

    def claim(self) -> ClaimedJob | None:
        now = self.clock()
        job = self.repository.claim(
            self.worker_id,
            now,
            now + timedelta(seconds=self.lease_seconds),
        )
        if job is None:
            return None
        if job.lease_token is None:
            raise JobLeaseLostError("Conversion job lease token is missing")
        started = self.monotonic_clock()
        budget = (
            None
            if self.max_job_duration_seconds is None
            else JobExecutionBudget(
                duration_seconds=self.max_job_duration_seconds,
                started_monotonic=started,
                deadline_monotonic=started + self.max_job_duration_seconds,
            )
        )
        return ClaimedJob(job, job.lease_token, budget, started)


@dataclass(slots=True)
class _ProgressState:
    step: JobStep
    percentage: int


@dataclass(frozen=True, slots=True)
class _BudgetCancellationProbe:
    budget: JobExecutionBudget | None
    lease_lost: Event
    duration_exhausted: Event
    shutdown_interrupted: Event
    monotonic_clock: Callable[[], float]
    durable_requested: Callable[[], bool]
    shutdown_requested: Callable[[], bool]

    def __call__(self) -> bool:
        if self.budget is not None and self.budget.exhausted(self.monotonic_clock()):
            self.duration_exhausted.set()
        if self.shutdown_requested():
            self.shutdown_interrupted.set()
        return (
            self.lease_lost.is_set()
            or self.duration_exhausted.is_set()
            or self.shutdown_interrupted.is_set()
            or self.durable_requested()
        )


class JobHeartbeatService:
    """Serialize lease renewal, progress, and cancellation observations."""

    def __init__(  # noqa: PLR0913 - explicit lease context dependencies
        self,
        *,
        claimed: ClaimedJob,
        repository: JobRepository,
        worker_id: str,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
        lease_seconds: float,
        heartbeat_seconds: float,
        metrics: OperationalMetrics | None,
        shutdown_requested: Callable[[], bool],
    ) -> None:
        self._claimed = claimed
        self._repository = repository
        self._worker_id = worker_id
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._metrics = metrics
        self._shutdown_requested = shutdown_requested
        self._progress = _ProgressState(claimed.job.step, claimed.job.progress)
        self._step_started = claimed.started_monotonic
        self._progress_lock = Lock()
        self._operation_lock = Lock()
        self._stop = Event()
        self._lease_lost = Event()
        self._duration_exhausted = Event()
        self._shutdown_interrupted = Event()
        self._errors: list[BaseException] = []
        self._thread = Thread(
            target=self._keepalive,
            name=f"{worker_id}-heartbeat",
            daemon=False,
        )
        self.cancellation: CancellationProbe = _BudgetCancellationProbe(
            budget=claimed.budget,
            lease_lost=self._lease_lost,
            duration_exhausted=self._duration_exhausted,
            shutdown_interrupted=self._shutdown_interrupted,
            monotonic_clock=monotonic_clock,
            durable_requested=self.durable_cancellation_requested,
            shutdown_requested=shutdown_requested,
        )

    @property
    def step(self) -> JobStep:
        with self._progress_lock:
            return self._progress.step

    @property
    def duration_exhausted(self) -> bool:
        return self._duration_exhausted.is_set()

    @property
    def shutdown_interrupted(self) -> bool:
        return self._shutdown_interrupted.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        if self._metrics is not None:
            with self._progress_lock:
                final_step = self._progress.step
                step_started = self._step_started
            self._metrics.record_step_duration(
                final_step.value,
                max(0.0, self._monotonic_clock() - step_started),
            )

    def request_stop(self) -> None:
        self._stop.set()

    def durable_cancellation_requested(self) -> bool:
        return self._repository.cancellation_requested(
            self._claimed.job.id,
            self._worker_id,
            self._claimed.lease_token,
        )

    def refresh_limits(self) -> None:
        budget = self._claimed.budget
        if budget is not None and budget.exhausted(self._monotonic_clock()):
            self._duration_exhausted.set()
        if self._shutdown_requested():
            self._shutdown_interrupted.set()

    def progress(self, step: JobStep, percentage: int) -> None:
        with self._progress_lock:
            previous_step = self._progress.step
            if step is not previous_step and self._metrics is not None:
                observed_at = self._monotonic_clock()
                self._metrics.record_step_duration(
                    previous_step.value, max(0.0, observed_at - self._step_started)
                )
                self._step_started = observed_at
            self._progress.step = step
            self._progress.percentage = percentage
        with self._operation_lock:
            self._heartbeat()

    @contextmanager
    def guarded(self) -> Iterator[None]:
        with self._operation_lock:
            if self._errors:
                raise self._errors[0]
            yield

    def _heartbeat(self) -> None:
        heartbeat_at = self._clock()
        with self._progress_lock:
            step = self._progress.step
            percentage = self._progress.percentage
        if not self._repository.heartbeat(
            LeaseHeartbeat(
                job_id=self._claimed.job.id,
                worker_id=self._worker_id,
                lease_token=self._claimed.lease_token,
                now=heartbeat_at,
                lease_expires_at=heartbeat_at + timedelta(seconds=self._lease_seconds),
                step=step,
                progress=percentage,
            )
        ):
            raise JobLeaseLostError("Conversion job lease was lost")

    def _keepalive(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            with self._operation_lock:
                try:
                    self._heartbeat()
                except BaseException as error:
                    self._errors.append(error)
                    self._lease_lost.set()
                    return


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    result: JobProcessResult | None
    error: BaseException | None


@dataclass(frozen=True, slots=True)
class JobExecutionService:
    """Execute a processor while preserving unexpected failures for the owner thread."""

    processor: JobProcessor

    def execute(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> ProcessingOutcome:
        try:
            result = self.processor.process(
                job,
                cancelled=cancelled,
                progress=progress,
            )
            if cancelled():
                raise JobProcessingCancelled
        except BaseException as error:
            return ProcessingOutcome(None, error)
        return ProcessingOutcome(result, None)


@dataclass(frozen=True, slots=True)
class JobFailureService:
    """Resolve cancellation, budgets, and safe conversion failures in priority order."""

    repository: JobRepository
    worker_id: str
    clock: Callable[[], datetime]
    result_retention_seconds: float
    metrics: OperationalMetrics | None

    def resolve(
        self,
        claimed: ClaimedJob,
        outcome: ProcessingOutcome,
        heartbeat: JobHeartbeatService,
    ) -> JobProcessResult | None:
        job = claimed.job
        with heartbeat.guarded():
            if isinstance(outcome.error, JobLeaseLostError):
                raise outcome.error
            heartbeat.refresh_limits()
            if heartbeat.durable_cancellation_requested():
                self._finish_cancelled(job, claimed.lease_token)
                heartbeat.request_stop()
                return None
            if heartbeat.duration_exhausted:
                self._finish_budget_exceeded(job, claimed.lease_token)
                heartbeat.request_stop()
                return None
            if heartbeat.shutdown_interrupted:
                log_event(
                    "job_processing_interrupted",
                    job_id=str(job.id),
                    owner_id=str(job.owner_id),
                    worker_id=self.worker_id,
                    state=job.state.value,
                    step=heartbeat.step.value,
                    **template_log_fields(job),
                )
                heartbeat.request_stop()
                return None
            if isinstance(outcome.error, JobProcessingCancelled):
                self._finish_cancelled(job, claimed.lease_token)
                heartbeat.request_stop()
                return None
            if isinstance(outcome.error, ConversionError):
                self._finish_failed(job, claimed.lease_token, outcome.error)
                heartbeat.request_stop()
                return None
            if outcome.error is not None:
                raise outcome.error
        if outcome.result is None:
            raise RuntimeError("Job processor returned no result")
        return outcome.result

    def _finish_cancelled(self, job: ConversionJob, lease_token: UUID) -> None:
        finished_at = self.clock()
        self.repository.finish_cancelled(
            job.id,
            self.worker_id,
            lease_token,
            finished_at,
            self._expires_at(finished_at),
        )
        log_event(
            "job_processing_completed",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            worker_id=self.worker_id,
            state=JobState.CANCELLED.value,
            step=job.step.value,
            **template_log_fields(job),
        )

    def _finish_failed(
        self, job: ConversionJob, lease_token: UUID, error: ConversionError
    ) -> None:
        finished_at = self.clock()
        self.repository.fail(
            JobFailure(
                job_id=job.id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                code=error.code.value,
                message=str(error),
                now=finished_at,
                expires_at=self._expires_at(finished_at),
            )
        )
        if self.metrics is not None:
            self.metrics.record_failure(error.code.value)
        self._log_failure(job, error.code.value)

    def _finish_budget_exceeded(self, job: ConversionJob, lease_token: UUID) -> None:
        finished_at = self.clock()
        code = "resource_budget_exceeded"
        self.repository.fail(
            JobFailure(
                job_id=job.id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                code=code,
                message="Conversion exceeded its configured duration budget.",
                now=finished_at,
                expires_at=self._expires_at(finished_at),
            )
        )
        if self.metrics is not None:
            self.metrics.record_failure(code)
        self._log_failure(job, code)

    def _log_failure(self, job: ConversionJob, code: str) -> None:
        log_event(
            "job_processing_failed",
            job_id=str(job.id),
            owner_id=str(job.owner_id),
            worker_id=self.worker_id,
            state=JobState.FAILED.value,
            step=job.step.value,
            error_code=code,
            **template_log_fields(job),
        )

    def _expires_at(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.result_retention_seconds)


class TemplateLogFields(TypedDict):
    """Optional immutable-template field accepted by structured logging."""

    version_id: NotRequired[str]


def template_log_fields(job: ConversionJob) -> TemplateLogFields:
    """Return content-free immutable template context for worker logs."""

    if job.template_version_id is None:
        return {}
    return {"version_id": str(job.template_version_id)}
