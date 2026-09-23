"""Immutable Composer source, artifact, and revision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

_SHA256_HEX_LENGTH = 64
_SOURCE_MEDIA_TYPES = frozenset(
    {
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)
_ARTIFACT_MEDIA_TYPES = _SOURCE_MEDIA_TYPES | {"text/html", "text/plain"}


class ComposerNotFoundError(LookupError):
    """A Composer resource is absent or outside the caller's ownership."""


class ComposerConflictError(RuntimeError):
    """An optimistic version, proposal, or publication precondition failed."""


class ComposerArtifactError(RuntimeError):
    """A revision artifact is missing, corrupt, or could not be published."""


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Immutable scanned source identity accepted by an upstream admission boundary."""

    kind: str
    object_id: UUID
    owner_id: UUID
    sha256: str
    scan_receipt: str
    media_type: str
    origin_job_id: UUID | None = None
    origin_result_object_id: UUID | None = None
    origin_result_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"upload", "conversion_result", "composer_revision"}:
            raise ValueError("Unsupported Composer source kind")
        if len(self.sha256) != _SHA256_HEX_LENGTH or any(
            c not in "0123456789abcdef" for c in self.sha256
        ):
            raise ValueError("Source digest must be lowercase SHA-256")
        if not self.scan_receipt.strip():
            raise ValueError("Source scan proof is required")
        if self.media_type not in _SOURCE_MEDIA_TYPES:
            raise ValueError("Unsupported Composer source media type")
        origin = (
            self.origin_job_id,
            self.origin_result_object_id,
            self.origin_result_sha256,
        )
        if any(value is not None for value in origin) and not all(
            value is not None for value in origin
        ):
            raise ValueError("Source origin reference is incomplete")
        if self.kind == "conversion_result" and self.origin_job_id is None:
            raise ValueError("Conversion source origin is required")
        if self.kind == "upload" and self.origin_job_id is not None:
            raise ValueError("Upload source cannot claim a conversion origin")
        if (
            self.origin_result_sha256 is not None
            and self.origin_result_sha256 != self.sha256
        ):
            raise ValueError("Source bytes differ from the immutable origin")


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    """One complete, validated output supplied for atomic revision publication."""

    kind: str
    media_type: str
    content: bytes

    def __post_init__(self) -> None:
        if self.kind not in {"source", "download", "preview"}:
            raise ValueError("Unsupported Composer artifact kind")
        if self.media_type not in _ARTIFACT_MEDIA_TYPES:
            raise ValueError("Unsupported Composer artifact media type")
        if not self.content:
            raise ValueError("Composer artifact content is required")


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    """Immutable object identity and byte integrity metadata."""

    id: UUID
    kind: str
    sha256: str
    size: int
    media_type: str


@dataclass(frozen=True, slots=True)
class RevisionSnapshot:
    """Values frozen when a reviewed change is generated or restored."""

    source: SourceReference
    template_reference: str | None
    approved_values: str
    render_options: str
    model_identity: str | None
    provenance: str
    operation: str

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("Revision operation is required")


@dataclass(frozen=True, slots=True)
class ComposerRevision:
    """Published revision and matching artifact references."""

    id: UUID
    draft_id: UUID
    number: int
    actor_id: UUID
    snapshot: RevisionSnapshot
    artifacts: tuple[ArtifactReference, ...]
    restored_from_revision_id: UUID | None
    created_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.number}"'
