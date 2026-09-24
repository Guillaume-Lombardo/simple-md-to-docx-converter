"""Composer generation options follow the conversion service's template contract."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from markweave.http.composer_schemas import ComposerGenerationCreateRequest


@pytest.mark.unit
def test_pptx_reference_template_pair_is_accepted() -> None:
    selected = ComposerGenerationCreateRequest(
        output="pptx",
        template_id=UUID(int=1),
        template_version_id=UUID(int=2),
        presentation_dialect="marp",
        slide_level=3,
    )
    assert selected.template_id == UUID(int=1)
    assert selected.template_version_id == UUID(int=2)
    assert selected.slide_level == 3


@pytest.mark.unit
def test_incomplete_template_pair_and_docx_presentation_options_fail() -> None:
    with pytest.raises(ValidationError):
        ComposerGenerationCreateRequest(output="pptx", template_id=UUID(int=1))
    with pytest.raises(ValidationError):
        ComposerGenerationCreateRequest(output="docx", slide_level=3)
