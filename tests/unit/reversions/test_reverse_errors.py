"""Unit coverage for stable reverse-conversion failures."""

import pytest

from markweave.reversions.errors import (
    ReverseConversionError,
    ReverseErrorCategory,
    reject,
)

pytestmark = pytest.mark.unit


def test_every_error_category_has_a_fixed_content_free_message() -> None:
    failures = [ReverseConversionError(category) for category in ReverseErrorCategory]

    assert {failure.category for failure in failures} == set(ReverseErrorCategory)
    assert all(failure.message == str(failure) for failure in failures)
    assert all("secret.docx" not in str(failure) for failure in failures)
    assert len({failure.message for failure in failures}) == len(ReverseErrorCategory)


def test_reject_raises_a_fresh_safe_error() -> None:
    with pytest.raises(ReverseConversionError) as captured:
        reject(ReverseErrorCategory.NEEDS_OCR)

    assert captured.value.category is ReverseErrorCategory.NEEDS_OCR
    assert str(captured.value) == "The document requires OCR, which is not available."
