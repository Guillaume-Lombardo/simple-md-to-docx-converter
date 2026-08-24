"""Boundary tests for engine-neutral PDF raster comparisons."""

from typing import cast

import pytest

from tests.golden.limits import RasterLimits
from tests.golden.raster import RasterPage, RasterTolerance, compare_pdf_rasters

LIMITS = RasterLimits(10, 1_000, 10_000)


def page(
    pixels: bytes, *, width: int = 2, height: int = 1, dpi: int = 144
) -> RasterPage:
    return RasterPage(width, height, dpi, pixels)


@pytest.mark.unit
def test_one_changed_rgba_pixel_reports_exact_metrics_and_failing_page() -> None:
    expected = page(bytes((10, 20, 30, 255, 40, 50, 60, 255)))
    actual = page(bytes((10, 20, 30, 255, 43, 50, 60, 254)))
    result = compare_pdf_rasters(
        (expected,),
        (actual,),
        RasterTolerance(2, 0.5, 0.5),
        LIMITS,
    )
    assert result.failing_pages == (0,)
    assert result.pages[0].changed_pixels == 1
    assert result.pages[0].changed_pixel_ratio == 0.5
    assert result.pages[0].maximum_channel_delta == 3
    assert result.pages[0].mean_channel_delta == 0.5


@pytest.mark.unit
def test_thresholds_are_inclusive_and_alpha_is_compared() -> None:
    expected = page(bytes((0, 0, 0, 255, 0, 0, 0, 255)))
    actual = page(bytes((0, 0, 0, 255, 0, 0, 0, 251)))
    result = compare_pdf_rasters(
        (expected,),
        (actual,),
        RasterTolerance(4, 0.5, 0.5),
        LIMITS,
    )
    assert result.matches


@pytest.mark.unit
@pytest.mark.parametrize(
    ("actual", "attribute"),
    [
        ((page(bytes(8)), page(bytes(8))), "actual_page_count"),
        ((page(bytes(12), width=3),), "dimension_mismatches"),
        ((page(bytes(8), dpi=72),), "dpi_mismatches"),
    ],
)
def test_page_count_dimensions_and_dpi_are_strict(
    actual: tuple[RasterPage, ...], attribute: str
) -> None:
    result = compare_pdf_rasters(
        (page(bytes(8)),), actual, RasterTolerance(255, 1.0, 255.0), LIMITS
    )
    assert not result.matches
    assert getattr(result, attribute)
    assert result.failing_pages


@pytest.mark.unit
def test_rgba_length_and_tolerance_ranges_are_validated() -> None:
    with pytest.raises(ValueError, match="positive"):
        RasterPage(0, 1, 72, b"")
    with pytest.raises(ValueError, match="RGBA"):
        RasterPage(1, 1, 72, b"\0")
    with pytest.raises(ValueError, match="between 0 and 255"):
        RasterTolerance(256, 0, 0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        RasterTolerance(0, 1.1, 0)
    with pytest.raises(ValueError, match="between 0 and 255"):
        RasterTolerance(0, 0, 256)


@pytest.mark.unit
def test_empty_raster_sequences_are_invalid() -> None:
    with pytest.raises(ValueError, match="at least one page"):
        compare_pdf_rasters((), (), RasterTolerance(0, 0, 0), LIMITS)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (RasterLimits(1, 10, 20), "page count"),
        (RasterLimits(2, 1, 20), "per-page"),
        (RasterLimits(2, 10, 3), "total pixel"),
    ],
)
def test_caller_supplied_raster_limits_are_enforced(
    limits: RasterLimits, message: str
) -> None:
    pages = (page(bytes(8)), page(bytes(8)))
    with pytest.raises(ValueError, match=message):
        compare_pdf_rasters(pages, pages, RasterTolerance(0, 0, 0), limits)


@pytest.mark.unit
def test_raster_limits_require_positive_values() -> None:
    with pytest.raises(ValueError, match="positive"):
        RasterLimits(0, 1, 1)


@pytest.mark.unit
@pytest.mark.parametrize("invalid", [True, 1.0, float("inf")])
@pytest.mark.parametrize("field_index", range(3))
def test_raster_integer_limits_reject_bool_float_and_infinity(
    invalid: object, field_index: int
) -> None:
    values = [1, 1, 1]
    values[field_index] = cast("int", invalid)
    with pytest.raises(ValueError, match="positive integers"):
        RasterLimits(values[0], values[1], values[2])
