"""Engine-neutral PDF raster comparison primitives."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain

from tests.golden.limits import RasterLimits


@dataclass(frozen=True)
class RasterPage:
    """One unpremultiplied 8-bit sRGB RGBA page with explicit pixel density."""

    width: int
    height: int
    dpi: int
    pixels_rgba: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.dpi <= 0:
            raise ValueError("Raster dimensions and DPI must be positive")
        if len(self.pixels_rgba) != self.width * self.height * 4:
            raise ValueError("RGBA byte count does not match raster dimensions")


@dataclass(frozen=True)
class RasterTolerance:
    """Explicit golden thresholds; callers must choose every value."""

    max_channel_delta: int
    max_changed_pixel_ratio: float
    max_mean_channel_delta: float

    def __post_init__(self) -> None:
        if not 0 <= self.max_channel_delta <= 255:
            raise ValueError("max_channel_delta must be between 0 and 255")
        if not 0.0 <= self.max_changed_pixel_ratio <= 1.0:
            raise ValueError("max_changed_pixel_ratio must be between 0 and 1")
        if not 0.0 <= self.max_mean_channel_delta <= 255.0:
            raise ValueError("max_mean_channel_delta must be between 0 and 255")


@dataclass(frozen=True)
class RasterPageComparison:
    page_index: int
    changed_pixels: int
    changed_pixel_ratio: float
    maximum_channel_delta: int
    mean_channel_delta: float
    matches: bool


@dataclass(frozen=True)
class RasterComparison:
    expected_page_count: int
    actual_page_count: int
    dimension_mismatches: tuple[int, ...]
    dpi_mismatches: tuple[int, ...]
    pages: tuple[RasterPageComparison, ...]

    @property
    def failing_pages(self) -> tuple[int, ...]:
        unmatched = tuple(
            range(
                min(self.expected_page_count, self.actual_page_count),
                max(self.expected_page_count, self.actual_page_count),
            )
        )
        return tuple(
            sorted(
                set(
                    self.dimension_mismatches
                    + self.dpi_mismatches
                    + unmatched
                    + tuple(page.page_index for page in self.pages if not page.matches)
                )
            )
        )

    @property
    def matches(self) -> bool:
        return (
            self.expected_page_count == self.actual_page_count
            and not self.failing_pages
        )


def compare_pdf_rasters(
    expected: tuple[RasterPage, ...],
    actual: tuple[RasterPage, ...],
    tolerance: RasterTolerance,
    limits: RasterLimits,
) -> RasterComparison:
    """Compare pre-rasterized pages; this helper never selects or invokes a renderer."""

    if not expected or not actual:
        raise ValueError("Raster comparisons require at least one page on each side")
    if max(len(expected), len(actual)) > limits.max_pages:
        raise ValueError("Raster page count exceeds the test-harness limit")
    total_pixels = 0
    for page in chain(expected, actual):
        page_pixels = page.width * page.height
        if page_pixels > limits.max_pixels_per_page:
            raise ValueError("Raster page exceeds the per-page pixel limit")
        total_pixels += page_pixels
        if total_pixels > limits.max_total_pixels:
            raise ValueError("Raster pages exceed the total pixel limit")
    dimensions: list[int] = []
    dpis: list[int] = []
    results: list[RasterPageComparison] = []
    for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
        if (left.width, left.height) != (right.width, right.height):
            dimensions.append(index)
            continue
        if left.dpi != right.dpi:
            dpis.append(index)
            continue
        changed_pixels = 0
        maximum = 0
        delta_sum = 0
        for offset in range(0, len(left.pixels_rgba), 4):
            pixel_changed = False
            for channel in range(4):
                delta = abs(
                    left.pixels_rgba[offset + channel]
                    - right.pixels_rgba[offset + channel]
                )
                delta_sum += delta
                maximum = max(maximum, delta)
                pixel_changed = pixel_changed or delta > 0
            changed_pixels += pixel_changed
        pixel_count = left.width * left.height
        mean = delta_sum / (pixel_count * 4)
        ratio = changed_pixels / pixel_count
        results.append(
            RasterPageComparison(
                index,
                changed_pixels,
                ratio,
                maximum,
                mean,
                maximum <= tolerance.max_channel_delta
                and ratio <= tolerance.max_changed_pixel_ratio
                and mean <= tolerance.max_mean_channel_delta,
            )
        )
    return RasterComparison(
        len(expected), len(actual), tuple(dimensions), tuple(dpis), tuple(results)
    )
