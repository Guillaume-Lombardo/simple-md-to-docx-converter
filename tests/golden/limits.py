"""Explicit immutable resource bounds shared by golden-test helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real


@dataclass(frozen=True)
class ArchiveLimits:
    """Test-harness ZIP bounds; these are not T18 production limits."""

    max_entries: int
    max_member_uncompressed_bytes: int
    max_total_uncompressed_bytes: int
    max_compression_ratio: float

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_entries,
            self.max_member_uncompressed_bytes,
            self.max_total_uncompressed_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in integer_limits):
            raise ValueError("Archive integer limits must be positive integers")
        if (
            isinstance(self.max_compression_ratio, bool)
            or not isinstance(self.max_compression_ratio, Real)
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio < 1.0
        ):
            raise ValueError(
                "max_compression_ratio must be a finite non-boolean number at least 1"
            )


@dataclass(frozen=True)
class RasterLimits:
    """Test-harness raster bounds; callers must choose every value."""

    max_pages: int
    max_pixels_per_page: int
    max_total_pixels: int

    def __post_init__(self) -> None:
        values = (self.max_pages, self.max_pixels_per_page, self.max_total_pixels)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Raster limits must be positive integers")
