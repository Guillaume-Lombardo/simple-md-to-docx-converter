"""Public contracts for private author knowledge and typed DOCX filling."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ValueProvenance(StrEnum):
    SUPPLIED = "supplied"
    CITED = "cited"
    MODEL_SUGGESTED = "model_suggested"
    HUMAN_APPROVED = "human_approved"
    HUMAN_EDITED = "human_edited"
    UNRESOLVED = "unresolved"


class FillProvenanceKind(StrEnum):
    SUPPLIED = "supplied"
    CITED = "cited"
    MODEL_SUGGESTED = "model_suggested"
    HUMAN_APPROVED = "human_approved"
    HUMAN_EDITED = "human_edited"
    UNRESOLVED = "unresolved"
    TEMPLATE_DEFAULT = "template_default"


class AuthorFieldValue(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    value: str | None = None
    provenance: ValueProvenance
    source_reference: str | None = None


class AuthorWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1)
    fields: dict[str, AuthorFieldValue]


class AuthorResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    fields: dict[str, AuthorFieldValue]
    version: int
    etag: str
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime


class AuthorListResponse(BaseModel):
    authors: tuple[AuthorResponse, ...]
    limit: int
    offset: int


class TypedTemplateResponse(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    version: int
    etag: str
    active_version_id: UUID
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime


class TypedTemplateListResponse(BaseModel):
    templates: tuple[TypedTemplateResponse, ...]
    limit: int
    offset: int


class TypedTemplateVersionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    template_id: UUID
    number: int
    schema_version: int
    template_schema: dict[str, Any] = Field(alias="schema")
    schema_sha256: str
    docx_sha256: str
    size: int
    created_at: datetime


class TypedTemplateVersionListResponse(BaseModel):
    versions: tuple[TypedTemplateVersionResponse, ...]
    limit: int
    offset: int


class FillValueProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    kind: FillProvenanceKind
    source_reference: str | None = None


class FillQuestion(BaseModel):
    path: str
    text: str
    reason: str


class FillPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    source_revision_id: UUID
    template_id: UUID
    template_version_id: UUID
    author_ids: tuple[UUID, ...] = ()
    values: dict[str, Any] = Field(default_factory=dict)


class FillPlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    values: dict[str, Any]
    provenance: dict[str, FillValueProvenance]


class FillPlanResponse(BaseModel):
    id: UUID
    draft_id: UUID
    source_revision_id: UUID
    template_id: UUID
    template_version_id: UUID
    author_refs: tuple[dict[str, Any], ...]
    values: dict[str, Any]
    provenance: dict[str, FillValueProvenance]
    questions: tuple[FillQuestion, ...]
    state: str
    version: int
    etag: str
    result_revision_id: UUID | None
    created_at: datetime
    updated_at: datetime


class FillPlanListResponse(BaseModel):
    plans: tuple[FillPlanResponse, ...]
    limit: int
    offset: int


class AuthorPromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    connection_id: UUID
    approved_endpoint: str = Field(min_length=1)
    approved_model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    author_ids: tuple[UUID, ...]
    max_output_tokens: int = Field(gt=0)


class AuthorReference(BaseModel):
    id: UUID
    version: int = Field(gt=0)


class AuthorPromptPreviewResponse(BaseModel):
    transmitted_content: str
    author_refs: tuple[AuthorReference, ...]
    preview_digest: str
