"""Configuration-to-domain assembly kept independent of the HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass

from md_converter.config import Settings
from md_converter.jobs.policy import (
    ArchiveResourceBudget,
    DiagramResourceBudget,
    DocumentResourceBudget,
    JobAdmissionPolicy,
    ResourceBudget,
    RetentionPolicy,
)
from md_converter.jobs.runner import WorkerSchedule
from md_converter.jobs.service import JobServicePolicy
from md_converter.jobs.worker import WorkerPolicy


@dataclass(frozen=True, slots=True)
class JobPolicies:
    """All validated job policies needed by API and worker composition."""

    admission: JobAdmissionPolicy
    documents: DocumentResourceBudget
    resources: ResourceBudget
    retention: RetentionPolicy
    service: JobServicePolicy
    worker: WorkerPolicy
    schedule: WorkerSchedule


def build_job_policies(settings: Settings) -> JobPolicies:
    """Translate required environment settings without inventing defaults."""

    retention = RetentionPolicy(
        job_artifact_seconds=settings.job_result_retention_seconds,
        incomplete_submission_seconds=settings.worker_incomplete_submission_seconds,
        cleanup_interval_seconds=settings.worker_cleanup_interval_seconds,
        cleanup_batch_size=settings.worker_cleanup_batch_size,
    )
    resources = ResourceBudget(
        job_duration_seconds=settings.job_max_duration_seconds,
        worker_memory_bytes=settings.worker_memory_budget_bytes,
        worker_ephemeral_storage_bytes=settings.worker_ephemeral_storage_budget_bytes,
    )
    return JobPolicies(
        admission=JobAdmissionPolicy(
            active_jobs_per_user=settings.job_active_limit_per_user,
            global_queue_capacity=settings.job_global_queue_capacity,
        ),
        documents=DocumentResourceBudget(
            archive=ArchiveResourceBudget(
                upload_bytes=settings.conversion_upload_max_bytes,
                decompressed_bytes=settings.conversion_max_decompressed_bytes,
                file_count=settings.conversion_max_files,
                image_count=settings.conversion_max_images,
            ),
            diagrams=DiagramResourceBudget(
                diagram_count=settings.conversion_max_diagrams
            ),
        ),
        resources=resources,
        retention=retention,
        service=JobServicePolicy(
            retention.job_artifact_seconds,
            max_job_duration_seconds=resources.job_duration_seconds,
        ),
        worker=WorkerPolicy(
            lease_seconds=settings.worker_lease_seconds,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            result_retention_seconds=retention.job_artifact_seconds,
            incomplete_submission_seconds=retention.incomplete_submission_seconds,
            max_job_duration_seconds=resources.job_duration_seconds,
        ),
        schedule=WorkerSchedule(
            idle_poll_seconds=settings.worker_idle_poll_seconds,
            cleanup_interval_seconds=retention.cleanup_interval_seconds,
            cleanup_limit=retention.cleanup_batch_size,
            error_backoff_seconds=settings.worker_error_backoff_seconds,
        ),
    )
