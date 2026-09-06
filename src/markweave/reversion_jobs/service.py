"""Owner-only reverse submission, lifecycle, and result service."""

from __future__ import annotations

import hashlib
import math
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobNotFoundError,
    ReversionJobRequestError,
    ReversionJobStorageError,
)
from markweave.reversion_jobs.models import (
    ReversionJob,
    ReversionJobPage,
    ReversionJobState,
    ReversionRequest,
    ReversionSubmission,
)
from markweave.reversion_jobs.ports import ReversionRepository
from markweave.storage import (
    BoundedObjectStore,
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStoreError,
    ObjectTooLargeError,
)

MAX_IDEMPOTENCY_KEY_CHARACTERS = 255
FIRST_VISIBLE_CHARACTER = 33


@dataclass(frozen=True, slots=True)
class ReversionServicePolicy:
    """Caller-owned reverse result retention without a production default."""

    result_retention_seconds: float
    maximum_source_bytes: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.result_retention_seconds, bool)
            or not math.isfinite(self.result_retention_seconds)
            or self.result_retention_seconds <= 0
        ):
            raise ValueError("Reverse result retention must be positive")
        if type(self.maximum_source_bytes) is not int or self.maximum_source_bytes <= 0:
            raise ValueError("Reverse source limit must be a positive integer")


class ReversionService:
    """Persist reverse inputs and expose only exact owner-bound lifecycle access."""

    def __init__(
        self,
        repository: ReversionRepository,
        objects: BoundedObjectStore,
        policy: ReversionServicePolicy,
    ) -> None:
        self._repository = repository
        self._objects = objects
        self._policy = policy

    def submit(
        self, request: ReversionRequest, idempotency_key: str | None
    ) -> tuple[ReversionJob, bool]:
        """Reserve, persist, then atomically activate one owner source."""

        if len(request.source) > self._policy.maximum_source_bytes:
            raise ReversionJobRequestError(
                "Reverse source exceeds its configured limit"
            )
        source_sha256 = _digest(request.source)
        request_digest = _request_digest(request, source_sha256)
        job_id = uuid4()
        job, replayed = self._repository.create(
            ReversionSubmission(
                id=job_id,
                owner_id=request.owner_id,
                source_object_id=uuid4(),
                source_stem=request.source_stem,
                admission=request.admission,
                source_sha256=source_sha256,
                source_size=len(request.source),
                component_versions=request.component_versions,
                request_digest=request_digest,
                idempotency_digest=_idempotency_digest(idempotency_key),
                correlation_id=request.correlation_id or str(job_id),
                created_at=request.now,
            )
        )
        if replayed:
            if job.request_digest != request_digest:
                raise ReversionJobConflictError(
                    "Reverse idempotency key conflicts with its request"
                )
            if job.source_ready:
                return job, True
        key = ObjectKey(
            ObjectScope.REVERSION_UPLOAD, job.owner_id, job.source_object_id
        )
        try:
            self._objects.put(key, request.source)
        except Exception as error:
            with suppress(Exception):
                self._objects.delete(key)
            if isinstance(error, ObjectStoreError):
                raise ReversionJobStorageError from None
            raise
        return self._repository.activate_source(job.id, request.now), replayed

    def get(self, job_id: UUID, owner_id: UUID) -> ReversionJob:
        """Return one reverse job only through its exact owner predicate."""

        job = self._repository.get_owner(job_id, owner_id)
        if job is None:
            raise ReversionJobNotFoundError("Reverse job was not found")
        return job

    def list_owner(
        self, owner_id: UUID, *, offset: int, limit: int
    ) -> ReversionJobPage:
        if offset < 0 or limit <= 0:
            raise ValueError("Reverse job pagination values are invalid")
        return self._repository.list_owner(owner_id, offset=offset, limit=limit)

    def cancel(self, job_id: UUID, owner_id: UUID, *, now: datetime) -> ReversionJob:
        self.get(job_id, owner_id)
        job = self._repository.request_cancel(
            job_id,
            owner_id,
            now,
            now + timedelta(seconds=self._policy.result_retention_seconds),
        )
        if job is None:
            raise ReversionJobNotFoundError("Reverse job was not found")
        return job

    def download(self, job_id: UUID, owner_id: UUID) -> tuple[ReversionJob, bytes]:
        job = self.get(job_id, owner_id)
        if (
            job.state is not ReversionJobState.SUCCEEDED
            or job.result_object_id is None
            or job.result_size is None
            or job.result_sha256 is None
        ):
            raise ReversionJobConflictError("Reverse result is not available")
        key = ObjectKey(
            ObjectScope.REVERSION_RESULT, job.owner_id, job.result_object_id
        )
        try:
            content = self._objects.get_bounded(key, job.result_size)
        except ObjectNotFoundError:
            raise ReversionJobConflictError("Reverse result is not available") from None
        except ObjectTooLargeError:
            raise ReversionJobStorageError from None
        except ObjectStoreError:
            raise ReversionJobStorageError from None
        if len(content) != job.result_size or _digest(content) != job.result_sha256:
            raise ReversionJobStorageError
        return job, content


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _request_digest(request: ReversionRequest, source_sha256: str) -> str:
    fields = (
        source_sha256,
        str(len(request.source)),
        request.source_stem,
        request.admission.family.value,
        request.admission.extension,
        request.admission.detected_format or "",
        request.admission.parser_format,
        repr(request.component_versions),
    )
    return _digest("\0".join(fields).encode())


def _idempotency_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value
        or len(value) > MAX_IDEMPOTENCY_KEY_CHARACTERS
        or any(ord(character) < FIRST_VISIBLE_CHARACTER for character in value)
    ):
        raise ValueError("Idempotency key is invalid")
    return _digest(value.encode())
