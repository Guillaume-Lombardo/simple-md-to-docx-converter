"""Boundary tests for engine-neutral PDF raster comparisons."""

import pytest

from tests.golden.raster import RasterPage, RasterTolerance, compare_pdf_rasters


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
        (page(bytes(8)),), actual, RasterTolerance(255, 1.0, 255.0)
    )
    assert not result.matches
    assert getattr(result, attribute)
    assert result.failing_pages


@pytest.mark.unit
def test_rgba_length_and_tolerance_ranges_are_validated() -> None:
    with pytest.raises(ValueError, match="RGBA"):
        RasterPage(1, 1, 72, b"\0")
    with pytest.raises(ValueError, match="between 0 and 255"):
        RasterTolerance(256, 0, 0)
