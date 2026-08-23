"""Storage-neutral template identity and search models."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


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

    def __post_init__(self) -> None:
        if not self.normalized_name:
            raise ValueError("Template name must not be blank")

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
