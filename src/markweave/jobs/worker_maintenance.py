"""Worker lease recovery and bounded retry-safe cleanup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from markweave.jobs.ports import JobRepository
from markweave.observability import OperationalMetrics, log_event
from markweave.storage import ObjectKey, ObjectScope, ObjectStore


class MaintenanceCleaner(Protocol):
    """Additional bounded retention work sharing the periodic worker cadence."""

    def cleanup(self, *, limit: int) -> int: ...


@dataclass(frozen=True, slots=True)
class WorkerMaintenanceService:
    """Recover abandoned claims and clean terminal job objects."""

    repository: JobRepository
    objects: ObjectStore
    worker_id: str
    clock: Callable[[], datetime]
    lease_seconds: float
    result_retention_seconds: float
    incomplete_submission_seconds: float
    maintenance: MaintenanceCleaner | None
    metrics: OperationalMetrics | None

    def recover(self) -> int:
        now = self.clock()
        recovered = self.repository.recover_expired_leases(
            now,
            now + timedelta(seconds=self.result_retention_seconds),
            now - timedelta(seconds=self.incomplete_submission_seconds),
        )
        if self.metrics is not None:
            self.metrics.record_recovery(recovered)
        if recovered:
            log_event("job_recovery_completed", operation="lease_recovery")
        return recovered

    def cleanup(self, *, limit: int) -> int:
        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        now = self.clock()
        expired = self.repository.expire_terminal(
            self.worker_id,
            now,
            now + timedelta(seconds=self.lease_seconds),
            limit,
        )
        for candidate in expired:
            self.objects.delete(
                ObjectKey(
                    ObjectScope.UPLOAD,
                    candidate.owner_id,
                    candidate.source_object_id,
                )
            )
            for object_id in candidate.result_object_ids:
                self.objects.delete(
                    ObjectKey(ObjectScope.RESULT, candidate.owner_id, object_id)
                )
            for object_id in candidate.result_manifest_object_ids:
                self.objects.delete(
                    ObjectKey(
                        ObjectScope.RESULT_MANIFEST, candidate.owner_id, object_id
                    )
                )
            self.repository.complete_cleanup(candidate.job_id, candidate.cleanup_token)
        maintained = (
            self.maintenance.cleanup(limit=limit) if self.maintenance is not None else 0
        )
        if self.metrics is not None:
            self.metrics.record_expiration(len(expired))
        if expired:
            log_event("job_expiration_completed", operation="retention_cleanup")
        return len(expired) + maintained
