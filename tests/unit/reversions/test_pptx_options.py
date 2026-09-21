"""Canonical structured extraction options and reverse-specific limits."""

from dataclasses import replace

import pytest

from markweave.reversions.options import ReverseExtraction, ReversionOptions
from markweave.reversions.pptx_archive import PptxReadLimits
from tests.pptx_fixtures import LIMITS

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("extraction", list(ReverseExtraction))
def test_options_round_trip_and_defaults(extraction: ReverseExtraction) -> None:
    options = ReversionOptions(extraction)
    assert options.include_notes and options.include_images
    assert ReversionOptions.from_dict(options.to_dict()) == options


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        None,
        {"extraction": "slides"},
        {"extraction": "slides", "include_notes": True, "include_images": 1},
        {"extraction": "slides", "include_notes": 1, "include_images": True},
        {"extraction": "bad", "include_notes": True, "include_images": True},
        {"extraction": 1, "include_notes": True, "include_images": True},
        {"extraction": "anydoc", "include_notes": False, "include_images": True},
        {"extraction": "anydoc", "include_notes": True, "include_images": False},
        {
            "extraction": "slides",
            "include_notes": True,
            "include_images": True,
            "extra": 1,
        },
    ],
)
def test_invalid_options_are_not_coerced(value: object) -> None:
    with pytest.raises(ValueError):
        ReversionOptions.from_dict(value)


def test_reader_defaults_derive_from_reverse_bytes_and_explicit_overrides_win() -> None:
    derived = PptxReadLimits.from_content_limits(LIMITS)
    assert derived.member_bytes == LIMITS.max_input_bytes
    assert (
        derived.total_bytes
        == LIMITS.max_input_bytes
        + LIMITS.max_total_asset_source_bytes
        + LIMITS.max_markdown_bytes
    )
    assert derived.xml_depth == 64
    configured = replace(
        LIMITS,
        max_pptx_archive_entries=100,
        max_pptx_member_bytes=200,
        max_pptx_uncompressed_bytes=300,
        max_pptx_xml_elements=400,
        max_pptx_xml_depth=50,
        max_pptx_xml_attributes=600,
    )
    assert PptxReadLimits.from_content_limits(configured) == PptxReadLimits(
        100, 200, 300, 400, 50, 600
    )


@pytest.mark.parametrize("value", [0, -1, True])
@pytest.mark.parametrize(
    "field",
    [
        "max_pptx_archive_entries",
        "max_pptx_member_bytes",
        "max_pptx_uncompressed_bytes",
        "max_pptx_xml_elements",
        "max_pptx_xml_depth",
        "max_pptx_xml_attributes",
    ],
)
def test_optional_reader_budgets_fail_closed(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        replace(LIMITS, **{field: value})


def test_reader_depth_override_cannot_exceed_safety_ceiling() -> None:
    with pytest.raises(ValueError, match="safety ceiling"):
        replace(LIMITS, max_pptx_xml_depth=65)
