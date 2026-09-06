"""Storage-neutral durable reverse-job and attempt models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid5

from markweave.broker.models import (
    EvidenceDigest,
    TerminationProof,
)
from markweave.observability import require_correlation_id
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.reversions.models import ReverseOutputMode

SHA256_CHARACTERS = 64
MAX_SOURCE_STEM_CHARACTERS = 255
MAX_COMPONENT_VALUE_CHARACTERS = 255
FIRST_CONTROL_CODEPOINT = 32
DELETE_CODEPOINT = 127
ANYDOC_COMPONENT = ("firecrawl-anydoc", "0.2.4")
RESULT_OBJECT_NAME_PREFIX = "reversion-result-attempt:"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Reverse-job timestamps must include a timezone")
    return value.astimezone(UTC)


def _sha256(value: str, description: str) -> None:
    if len(value) != SHA256_CHARACTERS or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{description} must be lowercase SHA-256")


def _source_stem(value: str) -> None:
    if (
        not value
        or len(value) > MAX_SOURCE_STEM_CHARACTERS
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "\0"))
        or any(
            ord(character) < FIRST_CONTROL_CODEPOINT
            or ord(character) == DELETE_CODEPOINT
            for character in value
        )
    ):
        raise ValueError("Reverse source filename stem is invalid")


def _component_versions(values: tuple[tuple[str, str], ...]) -> None:
    if not values or tuple(sorted(values)) != values or ANYDOC_COMPONENT not in values:
        raise ValueError(
            "Reverse component versions must include firecrawl-anydoc 0.2.4"
        )
    names = [name for name, _version in values]
    if len(set(names)) != len(names) or any(
        not name
        or not version
        or len(name) > MAX_COMPONENT_VALUE_CHARACTERS
        or len(version) > MAX_COMPONENT_VALUE_CHARACTERS
        for name, version in values
    ):
        raise ValueError("Reverse component versions must be unique and bounded")


def reversion_result_object_id(job_id: UUID, attempt_number: int) -> UUID:
    """Derive a retry-cleanable result identifier for one reverse attempt."""

    if type(attempt_number) is not int or attempt_number <= 0:
        raise ValueError("Reverse result attempt must be positive")
    return uuid5(job_id, f"{RESULT_OBJECT_NAME_PREFIX}{attempt_number}")


class ReversionJobState(StrEnum):
    """Persisted reverse-conversion lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReversionJobStep(StrEnum):
    """Closed content-free reverse-job step vocabulary."""

    QUEUED = "queued"
    ISOLATING = "isolating"
    CONVERTING = "converting"
    VALIDATING = "validating"
    PUBLISHING = "publishing"
    COMPLETE = "complete"


TERMINAL_REVERSION_STATES = frozenset(
    {
        ReversionJobState.SUCCEEDED,
        ReversionJobState.FAILED,
        ReversionJobState.CANCELLED,
        ReversionJobState.EXPIRED,
    }
)


@dataclass(frozen=True, slots=True)
class ReversionTraceMetadata:
    """Closed content-free counters copied from the T70 manifest contract."""

    schema_version: int
    engine_name: str
    engine_version: str
    source_family: FormatFamily
    detected_format: str | None
    result_mode: ReverseOutputMode
    asset_count: int
    asset_bytes: int
    unavailable_asset_count: int
    local: bool = True
    ocr: bool = False
    hosted_fallback: bool = False

    def __post_init__(self) -> None:
        if (
            self.schema_version != 1
            or self.engine_name != ANYDOC_COMPONENT[0]
            or self.engine_version != ANYDOC_COMPONENT[1]
            or type(self.source_family) is not FormatFamily
            or (
                self.detected_format is not None
                and type(self.detected_format) is not str
            )
            or type(self.result_mode) is not ReverseOutputMode
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.asset_count,
                    self.asset_bytes,
                    self.unavailable_asset_count,
                )
            )
            or self.local is not True
            or self.ocr is not False
            or self.hosted_fallback is not False
        ):
            raise ValueError("Reverse trace metadata is invalid")
        if self.source_family is FormatFamily.CSV:
            if self.detected_format is not None:
                raise ValueError("CSV reverse trace must remain signature-free")
        elif not self.detected_format:
            raise ValueError("Reverse trace requires a detected format")
        if self.result_mode is ReverseOutputMode.MARKDOWN:
            valid = (
                self.asset_count == 0
                and self.asset_bytes == 0
                and self.unavailable_asset_count == 0
            )
        elif self.result_mode is ReverseOutputMode.MARKDOWN_WITH_ASSETS:
            valid = (
                self.asset_count > 0
                and self.asset_bytes > 0
                and self.unavailable_asset_count == 0
            )
        else:
            valid = (
                self.asset_count == 0
                and self.asset_bytes == 0
                and self.unavailable_asset_count > 0
            )
        if not valid:
            raise ValueError("Reverse trace counters do not match the result mode")


@dataclass(frozen=True, slots=True)
class ReversionSubmission:
    """Validated owner-bound input reserved before source persistence."""

    id: UUID
    owner_id: UUID
    source_object_id: UUID
    source_stem: str
    admission: FormatAdmission
    source_sha256: str
    source_size: int
    component_versions: tuple[tuple[str, str], ...]
    request_digest: str
    idempotency_digest: str | None
    correlation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        _source_stem(self.source_stem)
        if type(self.admission) is not FormatAdmission:
            raise ValueError("Reverse format admission is invalid")
        _sha256(self.source_sha256, "Reverse source digest")
        if type(self.source_size) is not int or self.source_size <= 0:
            raise ValueError("Reverse source size must be positive")
        _component_versions(self.component_versions)
        _sha256(self.request_digest, "Reverse request digest")
        if self.idempotency_digest is not None:
            _sha256(self.idempotency_digest, "Reverse idempotency digest")
        require_correlation_id(self.correlation_id)
        object.__setattr__(self, "created_at", _utc(self.created_at))


@dataclass(frozen=True, slots=True)
class ReversionAttempt:
    """One retained reverse execution attempt and its full proof history."""

    job_id: UUID
    attempt_number: int
    attempt_id: UUID
    worker_id: str
    lease_token: UUID
    leased_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    principal_id: UUID
    create_sequence: int
    create_intent_at: datetime | None = None
    unit_id: UUID | None = None
    policy_revision: str | None = None
    policy_specification: EvidenceDigest | None = None
    termination_proof: TerminationProof | None = None
    proof_recorded_at: datetime | None = None
    proof_acknowledged_at: datetime | None = None
    proof_recovery_token: UUID | None = None
    recovery_owner: str | None = None
    recovery_token: UUID | None = None
    recovery_expires_at: datetime | None = None

    def __post_init__(self) -> None:  # noqa: PLR0912 - closed bundle validation
        if type(self.attempt_number) is not int or self.attempt_number <= 0:
            raise ValueError("Reverse attempt number must be positive")
        if not self.worker_id:
            raise ValueError("Reverse attempt worker identity must not be blank")
        if type(self.principal_id) is not UUID:
            raise ValueError("Reverse attempt principal identity must be a UUID")
        if type(self.create_sequence) is not int or self.create_sequence <= 0:
            raise ValueError("Reverse create sequence must be positive")
        for field in ("leased_at", "heartbeat_at", "lease_expires_at"):
            object.__setattr__(self, field, _utc(getattr(self, field)))
        for field in (
            "create_intent_at",
            "proof_recorded_at",
            "proof_acknowledged_at",
            "recovery_expires_at",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value))
        if self.create_intent_at is None:
            if any(
                value is not None
                for value in (
                    self.unit_id,
                    self.policy_revision,
                    self.policy_specification,
                )
            ):
                raise ValueError("Reverse broker identity requires a create intent")
        elif (
            not self.policy_revision
            or type(self.policy_specification) is not EvidenceDigest
        ):
            raise ValueError("Reverse create intent identity is incomplete")
        proof = self.termination_proof
        if proof is None:
            if (
                self.proof_recorded_at is not None
                or self.proof_acknowledged_at is not None
                or self.proof_recovery_token is not None
            ):
                raise ValueError("Reverse proof timestamps require a proof")
        else:
            if self.proof_recorded_at is None:
                raise ValueError("Reverse termination proof requires a recorded time")
            if (
                proof.attempt_id != self.attempt_id
                or proof.principal.principal_id != self.principal_id
                or proof.policy_revision != self.policy_revision
                or (self.unit_id is not None and proof.unit_id != self.unit_id)
            ):
                raise ValueError("Reverse termination proof identity does not match")
            if self.unit_id is None:
                object.__setattr__(self, "unit_id", proof.unit_id)
            if (
                self.proof_recovery_token is not None
                and type(self.proof_recovery_token) is not UUID
            ):
                raise ValueError("Reverse proof recovery token must be a UUID")
        if (
            self.proof_acknowledged_at is not None
            and self.proof_recorded_at is not None
            and self.proof_acknowledged_at < self.proof_recorded_at
        ):
            raise ValueError("Reverse proof acknowledgement precedes recording")
        recovery = (self.recovery_owner, self.recovery_token, self.recovery_expires_at)
        if any(value is None for value in recovery) and any(
            value is not None for value in recovery
        ):
            raise ValueError("Reverse recovery lease must be complete")
        if self.recovery_owner == "":
            raise ValueError("Reverse recovery owner must not be blank")

    @property
    def recovery_blocked(self) -> bool:
        return self.create_intent_at is not None and self.termination_proof is None


@dataclass(frozen=True, slots=True)
class ReversionJob:
    """Complete durable reverse-job snapshot."""

    id: UUID
    owner_id: UUID
    source_object_id: UUID
    source_stem: str
    admission: FormatAdmission
    source_sha256: str
    source_size: int
    component_versions: tuple[tuple[str, str], ...]
    request_digest: str
    idempotency_digest: str | None
    correlation_id: str
    state: ReversionJobState
    step: ReversionJobStep
    created_at: datetime
    updated_at: datetime
    attempt: int = 0
    source_ready: bool = False
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    current_attempt_id: UUID | None = None
    cancel_requested: bool = False
    result_mode: ReverseOutputMode | None = None
    result_object_id: UUID | None = None
    result_sha256: str | None = None
    result_size: int | None = None
    trace: ReversionTraceMetadata | None = None
    error_code: str | None = None
    error_message: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:  # noqa: PLR0912 - closed bundle validation
        _source_stem(self.source_stem)
        if type(self.admission) is not FormatAdmission:
            raise ValueError("Reverse format admission is invalid")
        _sha256(self.source_sha256, "Reverse source digest")
        if type(self.source_size) is not int or self.source_size <= 0:
            raise ValueError("Reverse source size must be positive")
        _component_versions(self.component_versions)
        _sha256(self.request_digest, "Reverse request digest")
        if self.idempotency_digest is not None:
            _sha256(self.idempotency_digest, "Reverse idempotency digest")
        require_correlation_id(self.correlation_id)
        for field in ("created_at", "updated_at"):
            object.__setattr__(self, field, _utc(getattr(self, field)))
        for field in ("lease_expires_at", "heartbeat_at", "expires_at"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _utc(value))
        if self.attempt < 0:
            raise ValueError("Reverse attempt counter must not be negative")
        lease_values = (
            self.lease_owner,
            self.lease_token,
            self.lease_expires_at,
            self.heartbeat_at,
            self.current_attempt_id,
        )
        if self.state is ReversionJobState.RUNNING:
            if any(value is None for value in lease_values) or self.attempt <= 0:
                raise ValueError("Running reverse jobs require an exact active attempt")
        elif any(value is not None for value in lease_values):
            raise ValueError("Only running reverse jobs may carry lease state")
        result_values = (
            self.result_mode,
            self.result_object_id,
            self.result_sha256,
            self.result_size,
            self.trace,
        )
        if self.state is ReversionJobState.SUCCEEDED:
            if any(value is None for value in result_values):
                raise ValueError("Succeeded reverse jobs require a complete result")
            if self.result_sha256 is not None:
                _sha256(self.result_sha256, "Reverse result digest")
            if type(self.result_size) is not int or self.result_size <= 0:
                raise ValueError("Reverse result size must be positive")
            if (
                self.trace is not None
                and self.trace.result_mode is not self.result_mode
            ):
                raise ValueError("Reverse trace and result mode differ")
        elif any(value is not None for value in result_values):
            raise ValueError("Only succeeded reverse jobs may expose a result")
        if self.state is ReversionJobState.FAILED:
            if not self.error_code or not self.error_message:
                raise ValueError("Failed reverse jobs require a safe error")
        elif self.error_code is not None or self.error_message is not None:
            raise ValueError("Only failed reverse jobs may expose an error")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_REVERSION_STATES


@dataclass(frozen=True, slots=True)
class ReversionJobPage:
    """Deterministic owner-visible reverse-job page."""

    items: tuple[ReversionJob, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class ReversionLeaseHeartbeat:
    """Exact attempt lease extension and safe progress step."""

    job_id: UUID
    attempt_id: UUID
    worker_id: str
    lease_token: UUID
    now: datetime
    lease_expires_at: datetime
    step: ReversionJobStep


@dataclass(frozen=True, slots=True)
class ReversionFailure:
    """Safe proof-gated failure transition."""

    job_id: UUID
    attempt_id: UUID
    worker_id: str
    lease_token: UUID
    code: str
    message: str
    now: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("Reverse failure worker identity must not be blank")
        if not self.code.strip() or not self.message.strip():
            raise ValueError("Reverse failure details must not be blank")
        object.__setattr__(self, "now", _utc(self.now))
        object.__setattr__(self, "expires_at", _utc(self.expires_at))


@dataclass(frozen=True, slots=True)
class ExpiredReversionObjects:
    """Stable identifiers returned by a fenced expiration claim."""

    job_id: UUID
    cleanup_token: UUID
    owner_id: UUID
    source_object_id: UUID
    result_object_ids: tuple[UUID, ...]
