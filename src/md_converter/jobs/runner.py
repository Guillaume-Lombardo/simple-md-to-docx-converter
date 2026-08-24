"""Bounded worker loop usable by embedded threads and external processes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Protocol

from md_converter.jobs.errors import JobLeaseLostError, JobRepositoryError
from md_converter.jobs.worker import ConversionWorker
from md_converter.storage import ObjectStoreError


class StopSignal(Protocol):
    """Minimal stoppable wait boundary supported by threading and process adapters."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float) -> bool: ...


@dataclass(frozen=True, slots=True)
class WorkerSchedule:
    """Caller-owned polling and cleanup schedule whose values belong to T18."""

    idle_poll_seconds: float
    cleanup_every_iterations: int
    cleanup_limit: int
    error_backoff_seconds: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.idle_poll_seconds, bool)
            or self.idle_poll_seconds <= 0
            or isinstance(self.cleanup_every_iterations, bool)
            or self.cleanup_every_iterations <= 0
            or isinstance(self.cleanup_limit, bool)
            or self.cleanup_limit <= 0
            or isinstance(self.error_backoff_seconds, bool)
            or self.error_backoff_seconds <= 0
        ):
            raise ValueError("Worker schedule values must be positive")


class WorkerLoop:
    """Recover continuously, process serially, and run bounded periodic cleanup."""

    def __init__(self, worker: ConversionWorker, schedule: WorkerSchedule) -> None:
        self._worker = worker
        self._schedule = schedule

    def run(self, stop: StopSignal) -> None:
        iteration = 0
        while not stop.is_set():
            try:
                self._worker.recover()
                processed = self._worker.run_once()
                iteration += 1
                if iteration % self._schedule.cleanup_every_iterations == 0:
                    self._worker.cleanup(limit=self._schedule.cleanup_limit)
                if not processed:
                    stop.wait(self._schedule.idle_poll_seconds)
            except JobLeaseLostError, JobRepositoryError, ObjectStoreError:
                stop.wait(self._schedule.error_backoff_seconds)


class EmbeddedWorker:
    """Single lifecycle-owned worker thread for the standalone application profile."""

    def __init__(self, loop: WorkerLoop, *, thread_name: str) -> None:
        if not thread_name:
            raise ValueError("Embedded worker thread name is required")
        self._loop = loop
        self._thread_name = thread_name
        self._stop = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._failure: BaseException | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Embedded worker is already running")
            self._stop.clear()
            self._failure = None
            self._thread = Thread(
                target=self._run,
                name=self._thread_name,
                daemon=False,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float) -> None:
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("Embedded worker stop timeout must be positive")
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise RuntimeError("Embedded worker did not stop in time")
        with self._lock:
            if self._thread is thread:
                self._thread = None

    @property
    def failure(self) -> BaseException | None:
        """Expose an unexpected terminal loop failure to readiness supervision."""

        with self._lock:
            return self._failure

    def _run(self) -> None:
        try:
            self._loop.run(self._stop)
        except BaseException as error:  # inspected by the lifecycle owner
            with self._lock:
                self._failure = error
