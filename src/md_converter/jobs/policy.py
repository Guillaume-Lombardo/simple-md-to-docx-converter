"""Validated production-neutral resource and admission policy."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _positive_integer(name: str, value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _positive_number(name: str, value: float) -> None:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class JobAdmissionPolicy:
    """Atomic owner and global limits applied before reserving a job."""

    active_jobs_per_user: int
    global_queue_capacity: int

    def __post_init__(self) -> None:
        _positive_integer("Active-job limit", self.active_jobs_per_user)
        _positive_integer("Global queue capacity", self.global_queue_capacity)


@dataclass(frozen=True, slots=True)
class ArchiveResourceBudget:
    """Typed archive-validation slice for future processor composition."""

    upload_bytes: int
    decompressed_bytes: int
    file_count: int
    image_count: int

    def __post_init__(self) -> None:
        _positive_integer("Upload budget", self.upload_bytes)
        _positive_integer("Decompressed-content budget", self.decompressed_bytes)
        _positive_integer("File-count budget", self.file_count)
        _positive_integer("Image-count budget", self.image_count)


@dataclass(frozen=True, slots=True)
class DiagramResourceBudget:
    """Typed Mermaid-validation slice for future processor composition."""

    diagram_count: int

    def __post_init__(self) -> None:
        _positive_integer("Diagram-count budget", self.diagram_count)


@dataclass(frozen=True, slots=True)
class DocumentResourceBudget:
    """Structural ceilings projected onto archive and diagram boundaries."""

    archive: ArchiveResourceBudget
    diagrams: DiagramResourceBudget


@dataclass(frozen=True, slots=True)
class JobExecutionBudget:
    """One claimed job's monotonic duration budget."""

    duration_seconds: float
    started_monotonic: float
    deadline_monotonic: float

    def __post_init__(self) -> None:
        _positive_number("Job execution duration", self.duration_seconds)
        if not math.isfinite(self.started_monotonic):
            raise ValueError("Job execution start must be finite")
        if not math.isfinite(self.deadline_monotonic) or not math.isclose(
            self.deadline_monotonic - self.started_monotonic,
            self.duration_seconds,
        ):
            raise ValueError("Job execution deadline must match its duration")

    def remaining_seconds(self, now_monotonic: float) -> float:
        """Return a non-negative remaining duration from a monotonic reading."""

        if not math.isfinite(now_monotonic):
            raise ValueError("Monotonic reading must be finite")
        return max(0.0, self.deadline_monotonic - now_monotonic)

    def exhausted(self, now_monotonic: float) -> bool:
        """Return whether a monotonic reading reached the inclusive deadline."""

        return self.remaining_seconds(now_monotonic) == 0


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """Per-job runtime and deployment-enforced worker resource ceilings."""

    job_duration_seconds: float
    worker_memory_bytes: int
    worker_ephemeral_storage_bytes: int

    def __post_init__(self) -> None:
        _positive_number("Job duration budget", self.job_duration_seconds)
        _positive_integer("Worker memory budget", self.worker_memory_bytes)
        _positive_integer(
            "Worker ephemeral-storage budget", self.worker_ephemeral_storage_bytes
        )


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Explicit retention and recovery windows for private job artifacts."""

    job_artifact_seconds: float
    incomplete_submission_seconds: float
    cleanup_interval_seconds: float
    cleanup_batch_size: int

    def __post_init__(self) -> None:
        _positive_number("Job artifact retention", self.job_artifact_seconds)
        _positive_number(
            "Incomplete-submission recovery window",
            self.incomplete_submission_seconds,
        )
        _positive_number("Cleanup interval", self.cleanup_interval_seconds)
        _positive_integer("Cleanup batch size", self.cleanup_batch_size)
