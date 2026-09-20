"""Reject invalid options before they can reach an engine or durable job."""

from typing import cast

import pytest

from markweave.conversion.pandoc import PandocConfig
from markweave.presentations.models import PresentationDialect, PresentationOptions
from markweave.presentations.pandoc import PandocPptxConverter

pytestmark = pytest.mark.unit


def test_dialect_must_be_an_explicit_enum():
    with pytest.raises(ValueError, match="Invalid presentation dialect"):
        PresentationOptions(dialect=cast(PresentationDialect, "marp"))


@pytest.mark.parametrize("level", [-1, 7, True, 1.5])
def test_invalid_engine_heading_levels_cannot_be_forwarded(mocker, level):
    with pytest.raises(ValueError, match="Invalid slide heading level"):
        PandocPptxConverter(mocker.Mock(spec=PandocConfig), {}, slide_level=level)
