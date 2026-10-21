"""Public contracts for the permissioned, cited Composer reference library."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LibraryDocumentResponse(BaseModel):
    id: UUID
    owner_id: UUID
    title: str
    version: int
    etag: str
    current_version_id: UUID
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime


class LibraryDocumentListResponse(BaseModel):
    documents: tuple[LibraryDocumentResponse, ...]
    limit: int
    offset: int


class LibraryVersionResponse(BaseModel):
    id: UUID
    document_id: UUID
    number: int
    sha256: str
    size: int
    media_type: str
    chunk_count: int
    created_at: datetime


class LibraryVersionListResponse(BaseModel):
    versions: tuple[LibraryVersionResponse, ...]
    limit: int
    offset: int


class LibrarySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class LibrarySearchHitResponse(BaseModel):
    document_id: UUID
    title: str
    version_id: UUID
    version_number: int
    chunk: int
    score: int
    snippet: str


class LibrarySearchResponse(BaseModel):
    results: tuple[LibrarySearchHitResponse, ...]


class LibraryCitationReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: UUID
    chunk: int = Field(ge=0)


class LibraryExcerptResponse(BaseModel):
    document_id: UUID
    title: str
    version_id: UUID
    version_number: int
    version_sha256: str
    chunk: int
    text: str


class LibraryExcerptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    references: tuple[LibraryCitationReference, ...] = Field(
        min_length=1, max_length=20
    )


class LibraryExcerptListResponse(BaseModel):
    excerpts: tuple[LibraryExcerptResponse, ...]
