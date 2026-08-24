"""Lease-owning worker orchestration with fenced atomic result publication."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event, Lock, Thread

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
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.storage import ObjectKey, ObjectScope, ObjectStore

MAX_WORKER_ID_CHARACTERS = 255


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


@dataclass(slots=True)
class _ProgressState:
    """Thread-safe current progress shared with the keepalive."""

    step: JobStep
    percentage: int


class ConversionWorker:
    """Claim one job and publish only with its unique fencing token."""

    def __init__(
        self,
        *,
        worker_id: str,
        runtime: WorkerRuntime,
        policy: WorkerPolicy,
    ) -> None:
        if not worker_id or len(worker_id) > MAX_WORKER_ID_CHARACTERS:
            raise ValueError("Worker identifier is invalid")
        self._worker_id = worker_id
        self._repository = runtime.repository
        self._objects = runtime.objects
        self._processor = runtime.processor
        self._clock = runtime.clock
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
        deadline = (
            None
            if self._policy.max_job_duration_seconds is None
            else now + timedelta(seconds=self._policy.max_job_duration_seconds)
        )

        progress_state = _ProgressState(job.step, job.progress)
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

        def cancelled() -> bool:
            if deadline is not None and self._clock() >= deadline:
                duration_exhausted.set()
            return (
                lease_lost.is_set()
                or duration_exhausted.is_set()
                or self._repository.cancellation_requested(
                    job.id, self._worker_id, lease_token
                )
            )

        def progress(step: JobStep, percentage: int) -> None:
            with progress_lock:
                progress_state.step = step
                progress_state.percentage = percentage
            with heartbeat_operation:
                heartbeat()

        keepalive_thread = Thread(
            target=keepalive,
            name=f"{self._worker_id}-heartbeat",
            daemon=False,
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
                if isinstance(processing_error, JobProcessingCancelled):
                    if duration_exhausted.is_set() and not (
                        self._repository.cancellation_requested(
                            job.id, self._worker_id, lease_token
                        )
                    ):
                        self._finish_budget_exceeded(job)
                    else:
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
            if finished.state is JobState.CANCELLED:
                self._objects.delete(result_key)
            return True
        finally:
            keepalive_stop.set()
            keepalive_thread.join()

    def recover(self) -> int:
        """Recover expired work and abandoned durable source reservations."""

        now = self._clock()
        return self._repository.recover_expired_leases(
            now,
            self._expires_at(now),
            now - timedelta(seconds=self._policy.incomplete_submission_seconds),
        )

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
        return len(expired)

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

    def _expires_at(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self._policy.result_retention_seconds)
