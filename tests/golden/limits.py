"""Explicit immutable resource bounds shared by golden-test helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveLimits:
    """Test-harness ZIP bounds; these are not T18 production limits."""

    max_entries: int
    max_member_uncompressed_bytes: int
    max_total_uncompressed_bytes: int
    max_compression_ratio: float

    def __post_init__(self) -> None:
        if (
            min(
                self.max_entries,
                self.max_member_uncompressed_bytes,
                self.max_total_uncompressed_bytes,
            )
            <= 0
        ):
            raise ValueError("Archive limits must be positive")
        if (
            not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio < 1.0
        ):
            raise ValueError("max_compression_ratio must be finite and at least 1")


@dataclass(frozen=True)
class RasterLimits:
    """Test-harness raster bounds; callers must choose every value."""

    max_pages: int
    max_pixels_per_page: int
    max_total_pixels: int

    def __post_init__(self) -> None:
        if min(self.max_pages, self.max_pixels_per_page, self.max_total_pixels) <= 0:
            raise ValueError("Raster limits must be positive")
