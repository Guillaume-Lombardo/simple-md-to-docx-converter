"""Public contracts for bounded DOCX and PPTX text edits on exact Composer revisions."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ComposerDocxTextTargetResponse(BaseModel):
    target: str
    paragraph: int
    in_table: bool
    text: str


class ComposerDocxTextTargetListResponse(BaseModel):
    revision_id: UUID
    download_sha256: str
    engine_version: int
    targets: tuple[ComposerDocxTextTargetResponse, ...]
    total: int
    limit: int
    offset: int


class ComposerDocxReplaceTextOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    op: Literal["replace_text"]
    target: str = Field(min_length=1, max_length=64)
    expected_text: str
    text: str


class ComposerDocxEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    operations: tuple[ComposerDocxReplaceTextOperation, ...] = Field(min_length=1)


class ComposerPptxTextTargetResponse(BaseModel):
    target: str
    slide: int
    notes: bool
    paragraph: int
    in_table: bool
    text: str


class ComposerPptxTextTargetListResponse(BaseModel):
    revision_id: UUID
    download_sha256: str
    engine_version: int
    targets: tuple[ComposerPptxTextTargetResponse, ...]
    total: int
    limit: int
    offset: int


class ComposerPptxEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    operations: tuple[ComposerDocxReplaceTextOperation, ...] = Field(min_length=1)
