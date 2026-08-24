"""Engine-neutral PDF raster comparison primitives."""

from __future__ import annotations

from dataclasses import dataclass


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
) -> RasterComparison:
    """Compare pre-rasterized pages; this helper never selects or invokes a renderer."""

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
        channel_deltas = tuple(
            abs(a - b) for a, b in zip(left.pixels_rgba, right.pixels_rgba, strict=True)
        )
        changed_pixels = sum(
            any(channel_deltas[offset + channel] > 0 for channel in range(4))
            for offset in range(0, len(channel_deltas), 4)
        )
        pixel_count = left.width * left.height
        maximum = max(channel_deltas, default=0)
        mean = sum(channel_deltas) / len(channel_deltas)
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
