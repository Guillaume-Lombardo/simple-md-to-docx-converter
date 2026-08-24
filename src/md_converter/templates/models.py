"""Storage-neutral template identity and search models."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

SHA256_CHARACTERS = 64


class TemplateStatus(StrEnum):
    """Visibility lifecycle available before T15 adds version mutations."""

    ACTIVE = "active"
    ARCHIVED = "archived"


def normalize_template_text(value: str) -> str:
    """Normalize searchable text consistently across SQLite and PostgreSQL."""
    return unicodedata.normalize("NFKC", value).strip().casefold()


@dataclass(frozen=True, slots=True)
class TemplateIdentity:
    """A stable template identity whose owner cannot be reassigned."""

    id: UUID
    owner_id: UUID
    name: str
    description: str
    status: TemplateStatus
    revision: int = 1
    current_version_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.normalized_name:
            raise ValueError("Template name must not be blank")
        if self.revision <= 0:
            raise ValueError("Template revision must be positive")

    @property
    def normalized_name(self) -> str:
        return normalize_template_text(self.name)

    @property
    def normalized_description(self) -> str:
        return normalize_template_text(self.description)


@dataclass(frozen=True, slots=True)
class TemplateCreate:
    """Actor-owned template input that deliberately carries no owner identifier."""

    id: UUID
    name: str
    description: str
    status: TemplateStatus = TemplateStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class TemplateSearch:
    """Deterministic paginated template filters."""

    name: str | None = None
    description: str | None = None
    owner_id: UUID | None = None
    status: TemplateStatus | None = None
    offset: int = 0
    limit: int = 20

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("Template search offset must not be negative")
        if self.limit <= 0:
            raise ValueError("Template search limit must be positive")


@dataclass(frozen=True, slots=True)
class TemplatePage:
    """One deterministic page plus the complete visible match count."""

    items: tuple[TemplateIdentity, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    """Immutable validated content snapshot."""

    id: UUID
    template_id: UUID
    number: int
    object_owner_id: UUID
    sha256: str
    size: int
    created_at: datetime
    created_by: UUID
    restored_from_version_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.number <= 0 or self.size <= 0:
            raise ValueError("Template version number and size must be positive")
        if len(self.sha256) != SHA256_CHARACTERS or any(
            c not in "0123456789abcdef" for c in self.sha256
        ):
            raise ValueError("Template version digest must be lowercase SHA-256")
        if self.created_at.tzinfo is None:
            raise ValueError("Template version timestamp must include a timezone")
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class TemplateAuditRecord:
    """Content-free record of a sensitive template mutation."""

    id: UUID
    actor_id: UUID
    owner_id: UUID
    template_id: UUID
    operation: str
    version_id: UUID | None
    administrator_intervention: bool
    created_at: datetime
