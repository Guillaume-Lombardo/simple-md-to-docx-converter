"""Fair serial scheduling for forward and reverse conversion families."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from markweave.broker.errors import BrokerError
from markweave.jobs.errors import JobLeaseLostError, JobRepositoryError
from markweave.jobs.runner import StopSignal, WorkerSchedule
from markweave.jobs.worker import ConversionWorker
from markweave.observability import OperationalMetrics, log_event
from markweave.persistence.errors import PersistenceError
from markweave.reversion_jobs.errors import ReversionJobError
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.storage import ObjectStoreError


@dataclass(frozen=True, slots=True)
class ReversionSchedule:
    """Reverse-only cleanup and retry schedule with no production defaults."""

    cleanup_interval_seconds: float
    error_backoff_seconds: float

    def __post_init__(self) -> None:
        if any(
            type(value) not in {int, float} or not math.isfinite(value) or value <= 0
            for value in (
                self.cleanup_interval_seconds,
                self.error_backoff_seconds,
            )
        ):
            raise ValueError("Reverse schedule values must be positive")


class StopSignalBridge:
    """Late-bind the lifecycle signal needed by reverse runtime callbacks."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._stop: StopSignal | None = None

    def bind(self, stop: StopSignal) -> None:
        with self._lock:
            if self._stop is not None and self._stop is not stop:
                raise RuntimeError("Worker stop signal is already bound")
            self._stop = stop

    def is_set(self) -> bool:
        with self._lock:
            stop = self._stop
        return stop.is_set() if stop is not None else False

    def wait(self, timeout: float) -> bool:
        with self._lock:
            stop = self._stop
        return stop.wait(timeout) if stop is not None else False


class FairWorkerLoop:
    """Alternate successful claims while isolating one family's expected failures."""

    def __init__(  # noqa: PLR0913 - explicit family composition
        self,
        forward: ConversionWorker,
        reverse: ReversionWorker,
        forward_schedule: WorkerSchedule,
        reverse_schedule: ReversionSchedule,
        *,
        stop_bridge: StopSignalBridge,
        monotonic_clock=monotonic,
        metrics: OperationalMetrics | None = None,
    ) -> None:
        self._forward = forward
        self._reverse = reverse
        self._forward_schedule = forward_schedule
        self._reverse_schedule = reverse_schedule
        self._clock = monotonic_clock
        self._metrics = metrics
        self._stop_bridge = stop_bridge

    def run(self, stop: StopSignal) -> None:
        """Prefer forward first, then alternate successful family claims."""

        self._stop_bridge.bind(stop)
        prefer_forward = True
        now = self._clock()
        next_forward_cleanup = now + self._forward_schedule.cleanup_interval_seconds
        next_reverse_cleanup = now + self._reverse_schedule.cleanup_interval_seconds
        forward_retry_at = now
        reverse_retry_at = now
        while not stop.is_set():
            now = self._clock()
            forward_retry_at = self._recover_forward(now, forward_retry_at)
            reverse_retry_at = self._recover_reverse(now, reverse_retry_at)
            processed = False
            families = (
                ("forward", "reverse") if prefer_forward else ("reverse", "forward")
            )
            for family in families:
                now = self._clock()
                if family == "forward" and now >= forward_retry_at:
                    try:
                        processed = self._forward.run_once(
                            shutdown_requested=stop.is_set
                        )
                    except (
                        JobLeaseLostError,
                        JobRepositoryError,
                        ObjectStoreError,
                        PersistenceError,
                    ):
                        forward_retry_at = (
                            now + self._forward_schedule.error_backoff_seconds
                        )
                        self._retry("forward")
                elif family == "reverse" and now >= reverse_retry_at:
                    try:
                        processed = self._reverse.run_once()
                    except (
                        BrokerError,
                        ReversionJobError,
                        ObjectStoreError,
                        PersistenceError,
                    ):
                        reverse_retry_at = (
                            now + self._reverse_schedule.error_backoff_seconds
                        )
                        self._retry("reverse")
                if processed:
                    prefer_forward = family != "forward"
                    break
            now = self._clock()
            if now >= next_forward_cleanup:
                next_forward_cleanup = (
                    now + self._forward_schedule.cleanup_interval_seconds
                )
                try:
                    self._forward.cleanup(limit=self._forward_schedule.cleanup_limit)
                except JobRepositoryError, ObjectStoreError, PersistenceError:
                    forward_retry_at = (
                        now + self._forward_schedule.error_backoff_seconds
                    )
                    self._retry("forward")
            if now >= next_reverse_cleanup:
                next_reverse_cleanup = (
                    now + self._reverse_schedule.cleanup_interval_seconds
                )
                try:
                    self._reverse.cleanup()
                except (
                    BrokerError,
                    ReversionJobError,
                    ObjectStoreError,
                    PersistenceError,
                ):
                    reverse_retry_at = (
                        now + self._reverse_schedule.error_backoff_seconds
                    )
                    self._retry("reverse")
            if not processed:
                stop.wait(self._forward_schedule.idle_poll_seconds)

    def _recover_forward(self, now: float, retry_at: float) -> float:
        if now < retry_at:
            return retry_at
        try:
            self._forward.recover()
        except (
            JobLeaseLostError,
            JobRepositoryError,
            ObjectStoreError,
            PersistenceError,
        ):
            self._retry("forward")
            return now + self._forward_schedule.error_backoff_seconds
        return retry_at

    def _recover_reverse(self, now: float, retry_at: float) -> float:
        if now < retry_at:
            return retry_at
        try:
            self._reverse.recover()
        except BrokerError, ReversionJobError, ObjectStoreError, PersistenceError:
            self._retry("reverse")
            return now + self._reverse_schedule.error_backoff_seconds
        return retry_at

    def _retry(self, family: str) -> None:
        del family
        if self._metrics is not None:
            self._metrics.record_retry("worker_loop")
        log_event("worker_retry_scheduled", operation="worker_loop")
