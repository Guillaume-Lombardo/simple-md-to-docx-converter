"""Bounded structured author facts and their explicit provenance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

AuthorProvenance = Literal[
    "supplied",
    "cited",
    "model_suggested",
    "human_approved",
    "human_edited",
    "unresolved",
]
_PROVENANCE = frozenset(
    {
        "supplied",
        "cited",
        "model_suggested",
        "human_approved",
        "human_edited",
        "unresolved",
    }
)
_FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")


class AuthorKnowledgeNotFoundError(LookupError):
    """An author entry does not exist or is invisible to the actor."""


class AuthorKnowledgeConflictError(RuntimeError):
    """An author entry changed before the requested mutation or model use."""


@dataclass(frozen=True, slots=True)
class AuthorKnowledgeLimits:
    """Deployment-supplied bounds; no product limit is assumed here."""

    max_fields: int
    max_name_length: int
    max_field_value_length: int
    max_field_name_length: int
    max_citation_length: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.max_fields,
                self.max_name_length,
                self.max_field_value_length,
                self.max_field_name_length,
                self.max_citation_length,
            )
        ):
            raise ValueError("Author knowledge limits must be positive integers")


@dataclass(frozen=True, slots=True)
class AuthorField:
    """A field value remains distinguishable from its evidence and review state."""

    value: str | None
    provenance: AuthorProvenance
    source_reference: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UUID
    owner_id: UUID
    name: str
    fields: dict[str, AuthorField]
    version: int
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


def validate_author(
    name: str, fields: dict[str, AuthorField], limits: AuthorKnowledgeLimits
) -> None:
    """Reject oversized or uncertain facts masquerading as approved data."""

    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > limits.max_name_length
    ):
        raise ValueError("Author name is invalid")
    if not isinstance(fields, dict) or len(fields) > limits.max_fields:
        raise ValueError("Author fields exceed the configured limit")
    for key, field in fields.items():
        if (
            not isinstance(key, str)
            or len(key) > limits.max_field_name_length
            or _FIELD_NAME.fullmatch(key) is None
            or not isinstance(field, AuthorField)
        ):
            raise ValueError("Author field is invalid")
        if field.provenance not in _PROVENANCE:
            raise ValueError("Author provenance is invalid")
        if field.value is not None and (
            not isinstance(field.value, str)
            or not field.value.strip()
            or len(field.value) > limits.max_field_value_length
        ):
            raise ValueError("Author field value is invalid")
        if field.provenance == "unresolved" and field.value is not None:
            raise ValueError("Unresolved author field cannot contain a value")
        if field.provenance != "unresolved" and field.value is None:
            raise ValueError("Resolved author field requires a value")
        if field.source_reference is not None and (
            not isinstance(field.source_reference, str)
            or not field.source_reference.strip()
            or len(field.source_reference) > limits.max_citation_length
        ):
            raise ValueError("Author citation is invalid")
        if field.provenance == "cited" and field.source_reference is None:
            raise ValueError("Cited author field requires a source reference")
