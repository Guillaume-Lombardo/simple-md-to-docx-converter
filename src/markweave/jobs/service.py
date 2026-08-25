"""Owner-bound idempotent job submission and query service."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from markweave.jobs.errors import JobConflictError, JobNotFoundError
from markweave.jobs.models import (
    ConversionJob,
    JobPage,
    JobRequest,
    JobState,
    JobSubmission,
)
from markweave.jobs.ports import JobRepository
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
)

MAX_IDEMPOTENCY_KEY_CHARACTERS = 255
FIRST_VISIBLE_CHARACTER = 33


@dataclass(frozen=True, slots=True)
class JobServicePolicy:
    """Caller-owned terminal retention whose production value belongs to T18."""

    result_retention_seconds: float
    max_job_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.result_retention_seconds, bool)
            or not math.isfinite(self.result_retention_seconds)
            or self.result_retention_seconds <= 0
        ):
            raise ValueError("Job service retention must be positive")
        if self.max_job_duration_seconds is not None and (
            isinstance(self.max_job_duration_seconds, bool)
            or not math.isfinite(self.max_job_duration_seconds)
            or self.max_job_duration_seconds <= 0
        ):
            raise ValueError("Job service duration budget must be positive")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class JobService:
    """Persist sources and jobs before acknowledging owner-scoped submission."""

    def __init__(
        self,
        repository: JobRepository,
        objects: ObjectStore,
        policy: JobServicePolicy,
    ) -> None:
        self._repository = repository
        self._objects = objects
        self._policy = policy

    @property
    def max_job_duration_seconds(self) -> float | None:
        """Expose the configured processing budget to transport composition."""

        return self._policy.max_job_duration_seconds

    def submit(
        self, request: JobRequest, idempotency_key: str | None
    ) -> tuple[ConversionJob, bool]:
        idempotency_digest = self._idempotency_digest(idempotency_key)
        source_sha256 = _digest(request.source)
        source_size = len(request.source)
        request_digest = _digest(
            b"\0".join(
                (
                    source_sha256.encode("ascii"),
                    str(source_size).encode("ascii"),
                    request.source_kind.value.encode("ascii"),
                    str(request.template_id).encode("ascii"),
                    str(request.template_version_id).encode("ascii"),
                    request.output.value.encode("ascii"),
                    repr(request.component_versions).encode("utf-8"),
                )
            )
        )
        job_id = uuid4()
        source_object_id = uuid4()
        job, replayed = self._repository.create(
            JobSubmission(
                id=job_id,
                owner_id=request.owner_id,
                source_object_id=source_object_id,
                template_id=request.template_id,
                template_version_id=request.template_version_id,
                output=request.output,
                component_versions=request.component_versions,
                request_digest=request_digest,
                idempotency_digest=idempotency_digest,
                created_at=request.now,
                correlation_id=request.correlation_id or str(job_id),
                source_filename=request.source_filename,
                source_kind=request.source_kind,
                source_sha256=source_sha256,
                source_size=source_size,
            )
        )
        if replayed:
            if job.request_digest != request_digest:
                raise JobConflictError("Idempotency key conflicts with its request")
            if job.source_ready:
                return job, True
        source_key = ObjectKey(ObjectScope.UPLOAD, job.owner_id, job.source_object_id)
        try:
            self._objects.put(source_key, request.source)
        except Exception:
            self._objects.delete(source_key)
            raise
        return self._repository.activate_source(job.id, request.now), replayed

    def get_visible(
        self, job_id: UUID, *, actor_id: UUID, actor_is_admin: bool
    ) -> ConversionJob:
        job = self._repository.get(job_id)
        if job is None or (job.owner_id != actor_id and not actor_is_admin):
            raise JobNotFoundError("Conversion job was not found")
        return job

    def list_owner(self, owner_id: UUID, *, offset: int, limit: int) -> JobPage:
        if offset < 0 or limit <= 0:
            raise ValueError("Job pagination values are invalid")
        return self._repository.list_owner(owner_id, offset=offset, limit=limit)

    def cancel(
        self,
        job_id: UUID,
        *,
        actor_id: UUID,
        actor_is_admin: bool,
        now: datetime,
    ) -> ConversionJob:
        visible = self.get_visible(
            job_id, actor_id=actor_id, actor_is_admin=actor_is_admin
        )
        job = self._repository.request_cancel(
            job_id,
            visible.owner_id,
            now,
            now + timedelta(seconds=self._policy.result_retention_seconds),
        )
        if job is None:
            raise JobNotFoundError("Conversion job was not found")
        return job

    def download(
        self, job_id: UUID, *, actor_id: UUID, actor_is_admin: bool
    ) -> tuple[ConversionJob, bytes]:
        job = self.get_visible(job_id, actor_id=actor_id, actor_is_admin=actor_is_admin)
        if job.state is not JobState.SUCCEEDED or job.result_object_id is None:
            raise JobConflictError("Conversion result is not available")
        key = ObjectKey(ObjectScope.RESULT, job.owner_id, job.result_object_id)
        try:
            return job, self._objects.get(key)
        except ObjectNotFoundError:
            raise JobConflictError("Conversion result is not available") from None

    def download_manifest(
        self, job_id: UUID, *, actor_id: UUID, actor_is_admin: bool
    ) -> tuple[ConversionJob, bytes]:
        """Return the atomically published PDF traceability sidecar."""

        job = self.get_visible(job_id, actor_id=actor_id, actor_is_admin=actor_is_admin)
        if job.state is not JobState.SUCCEEDED or job.result_manifest_object_id is None:
            raise JobConflictError("Conversion manifest is not available")
        key = ObjectKey(
            ObjectScope.RESULT_MANIFEST,
            job.owner_id,
            job.result_manifest_object_id,
        )
        try:
            return job, self._objects.get(key)
        except ObjectNotFoundError:
            raise JobConflictError("Conversion manifest is not available") from None

    @staticmethod
    def _idempotency_digest(value: str | None) -> str | None:
        if value is None:
            return None
        if (
            not value
            or len(value) > MAX_IDEMPOTENCY_KEY_CHARACTERS
            or any(ord(character) < FIRST_VISIBLE_CHARACTER for character in value)
        ):
            raise ValueError("Idempotency key is invalid")
        return _digest(value.encode("utf-8"))
