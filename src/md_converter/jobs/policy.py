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
class DocumentResourceBudget:
    """Structural ceilings applied by the document-validation pipeline."""

    upload_bytes: int
    decompressed_bytes: int
    file_count: int
    image_count: int
    diagram_count: int

    def __post_init__(self) -> None:
        _positive_integer("Upload budget", self.upload_bytes)
        _positive_integer("Decompressed-content budget", self.decompressed_bytes)
        _positive_integer("File-count budget", self.file_count)
        _positive_integer("Image-count budget", self.image_count)
        _positive_integer("Diagram-count budget", self.diagram_count)
        if self.decompressed_bytes < self.upload_bytes:
            raise ValueError(
                "Decompressed-content budget cannot be below upload budget"
            )


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
