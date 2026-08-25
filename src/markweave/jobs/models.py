"""Storage-neutral conversion job models and invariants."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid5

from markweave.observability import require_correlation_id

SHA256_CHARACTERS = 64
COMPLETE_PROGRESS = 100
RESULT_OBJECT_NAME_PREFIX = "result-attempt:"
RESULT_MANIFEST_OBJECT_NAME_PREFIX = "result-manifest-attempt:"
MAX_SOURCE_FILENAME_CHARACTERS = 255
FIRST_CONTROL_CODEPOINT = 32
DELETE_CODEPOINT = 127


def result_object_id(job_id: UUID, attempt: int) -> UUID:
    """Derive a fenced, retry-cleanable object identifier for one attempt."""

    if attempt <= 0:
        raise ValueError("Result attempts must be positive")
    return uuid5(job_id, f"{RESULT_OBJECT_NAME_PREFIX}{attempt}")


def result_manifest_object_id(job_id: UUID, attempt: int) -> UUID:
    """Derive the fenced traceability sidecar identifier for one attempt."""

    if attempt <= 0:
        raise ValueError("Result attempts must be positive")
    return uuid5(job_id, f"{RESULT_MANIFEST_OBJECT_NAME_PREFIX}{attempt}")


class JobState(StrEnum):
    """Persisted conversion lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class JobStep(StrEnum):
    """Safe current-step vocabulary exposed to clients."""

    QUEUED = "queued"
    VALIDATING = "validating"
    RENDERING = "rendering"
    DOCX = "docx"
    PDF = "pdf"
    PUBLISHING = "publishing"
    COMPLETE = "complete"


class JobOutput(StrEnum):
    """Requested immutable result format."""

    DOCX = "docx"
    PDF = "pdf"
    BOTH = "both"


class SourceKind(StrEnum):
    """Persisted source interpretation selected from the admitted filename."""

    MARKDOWN = "markdown"
    ARCHIVE = "archive"


def source_kind_for_filename(filename: str) -> SourceKind:
    """Validate one private leaf filename and return its immutable source kind."""

    if (
        not filename
        or len(filename) > MAX_SOURCE_FILENAME_CHARACTERS
        or filename in {".", ".."}
        or any(character in filename for character in ("/", "\\", "\0"))
        or any(
            ord(character) < FIRST_CONTROL_CODEPOINT
            or ord(character) == DELETE_CODEPOINT
            for character in filename
        )
    ):
        raise ValueError("Source filename is invalid")
    suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if suffix == "md":
        return SourceKind.MARKDOWN
    if suffix == "zip":
        return SourceKind.ARCHIVE
    raise ValueError("Source filename must end in .md or .zip")


def _validate_sha256(value: str) -> None:
    if len(value) != SHA256_CHARACTERS or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("Job digests must be lowercase SHA-256")


TERMINAL_JOB_STATES = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.EXPIRED}
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Job timestamps must include a timezone")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class JobSubmission:
    """Validated owner-bound input used to create one durable job."""

    id: UUID
    owner_id: UUID
    source_object_id: UUID
    template_id: UUID
    template_version_id: UUID
    output: JobOutput
    component_versions: tuple[tuple[str, str], ...]
    request_digest: str
    idempotency_digest: str | None
    created_at: datetime
    correlation_id: str = ""
    source_filename: str = "source.md"
    source_kind: SourceKind = SourceKind.MARKDOWN
    source_sha256: str = "0" * SHA256_CHARACTERS
    source_size: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _utc(self.created_at))
        correlation_id = self.correlation_id or str(self.id)
        object.__setattr__(
            self, "correlation_id", require_correlation_id(correlation_id)
        )
        _validate_component_versions(self.component_versions)
        if source_kind_for_filename(self.source_filename) is not self.source_kind:
            raise ValueError("Source filename and kind do not match")
        _validate_sha256(self.source_sha256)
        if self.source_size <= 0:
            raise ValueError("Source size must be positive")
        for digest in (self.request_digest, self.idempotency_digest):
            if digest is not None:
                _validate_sha256(digest)


@dataclass(frozen=True, slots=True)
class ConversionJob:
    """Complete persistent job snapshot."""

    id: UUID
    owner_id: UUID
    source_object_id: UUID
    template_id: UUID
    template_version_id: UUID
    output: JobOutput
    component_versions: tuple[tuple[str, str], ...]
    state: JobState
    step: JobStep
    progress: int
    request_digest: str
    idempotency_digest: str | None
    created_at: datetime
    updated_at: datetime
    correlation_id: str = ""
    attempt: int = 0
    source_ready: bool = True
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool = False
    result_object_id: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    expires_at: datetime | None = None
    source_filename: str | None = None
    source_kind: SourceKind | None = None
    source_sha256: str | None = None
    source_size: int | None = None
    result_manifest_object_id: UUID | None = None

    def __post_init__(self) -> None:
        correlation_id = self.correlation_id or str(self.id)
        object.__setattr__(
            self, "correlation_id", require_correlation_id(correlation_id)
        )
        self._normalize_timestamps()
        self._validate_progress()
        _validate_component_versions(self.component_versions)
        self._validate_source()
        self._validate_lease()
        self._validate_result()
        self._validate_error()

    def _validate_source(self) -> None:
        filename = self.source_filename
        kind = self.source_kind
        sha256 = self.source_sha256
        size = self.source_size
        metadata = (filename, kind, sha256, size)
        if all(value is None for value in metadata):
            return
        if filename is None or kind is None or sha256 is None or size is None:
            raise ValueError("Source integrity metadata must be complete")
        if source_kind_for_filename(filename) is not kind:
            raise ValueError("Source filename and kind do not match")
        _validate_sha256(sha256)
        if size <= 0:
            raise ValueError("Source size must be positive")

    def _normalize_timestamps(self) -> None:
        object.__setattr__(self, "created_at", _utc(self.created_at))
        object.__setattr__(self, "updated_at", _utc(self.updated_at))
        for field_name in ("lease_expires_at", "heartbeat_at", "expires_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _utc(value))

    def _validate_progress(self) -> None:
        if not 0 <= self.progress <= COMPLETE_PROGRESS:
            raise ValueError("Job progress must be between zero and one hundred")
        if self.attempt < 0:
            raise ValueError("Job attempt must not be negative")

    def _validate_lease(self) -> None:
        if self.state is JobState.RUNNING:
            if (
                self.lease_owner is None
                or self.lease_token is None
                or self.lease_expires_at is None
            ):
                raise ValueError("Running jobs require an active lease")
        elif any(
            value is not None
            for value in (
                self.lease_owner,
                self.lease_token,
                self.lease_expires_at,
                self.heartbeat_at,
            )
        ):
            raise ValueError("Only running jobs may carry lease state")

    def _validate_result(self) -> None:
        if self.state is JobState.SUCCEEDED:
            if self.result_object_id is None or self.progress != COMPLETE_PROGRESS:
                raise ValueError("Succeeded jobs require a complete result")
        elif (
            self.result_object_id is not None
            or self.result_manifest_object_id is not None
        ):
            raise ValueError("Only succeeded jobs may expose a result")

    def _validate_error(self) -> None:
        if self.state is JobState.FAILED:
            if self.error_code is None or self.error_message is None:
                raise ValueError("Failed jobs require a safe error")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("Only failed jobs may expose an error")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_JOB_STATES


@dataclass(frozen=True, slots=True)
class JobPage:
    """Deterministic owner-visible page."""

    items: tuple[ConversionJob, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class JobProcessResult:
    """Immutable worker output awaiting atomic publication."""

    content: bytes
    progress_manifest: bytes | None = None


@dataclass(frozen=True, slots=True)
class JobRequest:
    """Application input before stable job and source object identifiers exist."""

    owner_id: UUID
    source: bytes
    template_id: UUID
    template_version_id: UUID
    output: JobOutput
    component_versions: tuple[tuple[str, str], ...]
    now: datetime
    correlation_id: str = ""
    source_filename: str = "source.md"
    source_kind: SourceKind = SourceKind.MARKDOWN

    def __post_init__(self) -> None:
        if self.correlation_id:
            require_correlation_id(self.correlation_id)
        if source_kind_for_filename(self.source_filename) is not self.source_kind:
            raise ValueError("Source filename and kind do not match")


@dataclass(frozen=True, slots=True)
class LeaseHeartbeat:
    """Atomic lease extension and safe progress update."""

    job_id: UUID
    worker_id: str
    lease_token: UUID
    now: datetime
    lease_expires_at: datetime
    step: JobStep
    progress: int


@dataclass(frozen=True, slots=True)
class JobFailure:
    """Safe terminal failure transition owned by one worker."""

    job_id: UUID
    worker_id: str
    lease_token: UUID
    code: str
    message: str
    now: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ExpiredJobObjects:
    """Stable object identifiers returned by an atomic expiration transition."""

    job_id: UUID
    cleanup_token: UUID
    owner_id: UUID
    source_object_id: UUID
    result_object_ids: tuple[UUID, ...]
    result_manifest_object_ids: tuple[UUID, ...] = ()


def _validate_component_versions(values: tuple[tuple[str, str], ...]) -> None:
    if not values or tuple(sorted(values)) != values:
        raise ValueError("Component versions must be non-empty and sorted")
    names = [name for name, _version in values]
    if len(set(names)) != len(names) or any(
        not name or not version for name, version in values
    ):
        raise ValueError("Component versions must be unique and non-empty")
