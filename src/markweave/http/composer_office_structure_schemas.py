"""Public contracts for bounded structural DOCX and PPTX edits on exact revisions."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAXIMUM_STRUCTURE_OPERATIONS = 100


class ComposerParagraphTargetResponse(BaseModel):
    target: str
    paragraph: int
    in_table: bool
    style: str | None
    text: str


class ComposerParagraphStyleResponse(BaseModel):
    style_id: str
    name: str


class ComposerSlideTargetResponse(BaseModel):
    slide_id: int
    position: int
    title: str


class ComposerImageTargetResponse(BaseModel):
    target: str
    media_type: str
    sha256: str
    size: int
    used_by: tuple[str, ...]


class ComposerOfficeStructureResponse(BaseModel):
    format: Literal["docx", "pptx"]
    revision_id: UUID
    download_sha256: str
    engine_version: int
    paragraphs: tuple[ComposerParagraphTargetResponse, ...]
    paragraph_styles: tuple[ComposerParagraphStyleResponse, ...]
    slides: tuple[ComposerSlideTargetResponse, ...]
    images: tuple[ComposerImageTargetResponse, ...]


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ComposerSetParagraphStyleOperation(_Operation):
    op: Literal["set_paragraph_style"]
    target: str = Field(min_length=1, max_length=64)
    expected_style: str | None = Field(max_length=253)
    style: str = Field(min_length=1, max_length=253)


class ComposerMoveSlideOperation(_Operation):
    op: Literal["move_slide"]
    slide_id: int = Field(ge=0)
    expected_position: int = Field(ge=1)
    position: int = Field(ge=1)


class ComposerReplaceImageOperation(_Operation):
    op: Literal["replace_image"]
    target: str = Field(min_length=1, max_length=64)
    expected_sha256: str = Field(min_length=64, max_length=64)
    filename: str = Field(min_length=5, max_length=255)
    content_base64: str = Field(min_length=4)


ComposerStructureOperation = Annotated[
    ComposerSetParagraphStyleOperation
    | ComposerMoveSlideOperation
    | ComposerReplaceImageOperation,
    Field(discriminator="op"),
]


class ComposerOfficeStructureEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    operations: tuple[ComposerStructureOperation, ...] = Field(
        min_length=1, max_length=MAXIMUM_STRUCTURE_OPERATIONS
    )
