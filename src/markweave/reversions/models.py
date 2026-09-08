"""Content-bounded models for one isolated reverse-conversion attempt."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from markweave.conversion.images import ImageLimits
from markweave.reversions.assets import ReverseAssetLimits
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.formats import normalize_extension_hint
from markweave.reversions.package import PackageLimits


class ReverseOutputMode(StrEnum):
    """Deterministic result shapes approved by T69."""

    MARKDOWN = "markdown"
    MARKDOWN_WITH_ASSETS = "markdown_with_assets"
    MARKDOWN_WITH_UNAVAILABLE_ASSETS = "markdown_with_unavailable_assets"


@dataclass(frozen=True, slots=True)
class ReverseContentLimits:
    """Explicit T71-owned input, image, asset, Markdown, and output limits."""

    max_input_bytes: int
    max_output_bytes: int
    max_image_source_bytes: int
    max_image_width_pixels: int
    max_image_height_pixels: int
    max_image_pixels: int
    max_svg_elements: int
    max_svg_depth: int
    max_asset_count: int
    max_total_asset_source_bytes: int
    max_total_asset_output_bytes: int
    max_markdown_bytes: int
    max_package_bytes: int

    def __post_init__(self) -> None:
        for value in (
            self.max_input_bytes,
            self.max_output_bytes,
            self.max_asset_count,
            self.max_total_asset_source_bytes,
            self.max_total_asset_output_bytes,
            self.max_markdown_bytes,
            self.max_package_bytes,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Reverse content limits must be positive integers")
        _ = self.image_limits
        if (
            self.max_markdown_bytes > self.max_output_bytes
            or self.max_package_bytes > self.max_output_bytes
            or self.max_total_asset_output_bytes > self.max_package_bytes
            or self.max_total_asset_output_bytes > self.max_output_bytes
            or self.max_markdown_bytes > self.max_package_bytes
        ):
            raise ValueError("Reverse result limits must fit the output limit")

    @property
    def image_limits(self) -> ImageLimits:
        """Build the existing T08 image-normalization limit model."""

        return ImageLimits(
            self.max_image_source_bytes,
            self.max_image_width_pixels,
            self.max_image_height_pixels,
            self.max_image_pixels,
            self.max_svg_elements,
            self.max_svg_depth,
        )

    @property
    def asset_limits(self) -> ReverseAssetLimits:
        """Build the aggregate reverse-asset limit model."""

        return ReverseAssetLimits(
            self.image_limits,
            self.max_asset_count,
            self.max_total_asset_source_bytes,
            self.max_total_asset_output_bytes,
        )

    @property
    def package_limits(self) -> PackageLimits:
        """Build the deterministic Markdown/package limit model."""

        return PackageLimits(self.max_markdown_bytes, self.max_package_bytes)


@dataclass(frozen=True, slots=True)
class ReverseAttemptRequest:
    """One source-only request sent to the credentialless attempt child."""

    attempt_id: UUID
    extension: str
    limits: ReverseContentLimits
    source: bytes

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not UUID:
            raise ValueError("Reverse-attempt identity must be a UUID")
        object.__setattr__(self, "extension", normalize_extension_hint(self.extension))
        if type(self.limits) is not ReverseContentLimits:
            raise ValueError("Reverse-attempt limits are invalid")
        if type(self.source) is not bytes or not self.source:
            raise ValueError("Reverse-attempt input must not be empty")
        if len(self.source) > self.limits.max_input_bytes:
            raise ValueError("Reverse-attempt input exceeds its configured limit")


@dataclass(frozen=True, slots=True)
class ReverseAttemptSuccess:
    """One unpublished child result returned to the worker-side supervisor."""

    attempt_id: UUID
    mode: ReverseOutputMode
    result: bytes

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not UUID:
            raise ValueError("Reverse-attempt identity must be a UUID")
        if type(self.mode) is not ReverseOutputMode or type(self.result) is not bytes:
            raise ValueError("Reverse-attempt result is invalid")


@dataclass(frozen=True, slots=True)
class ReverseAttemptFailure:
    """One content-free expected failure returned by the attempt child."""

    attempt_id: UUID
    category: ReverseErrorCategory

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not UUID:
            raise ValueError("Reverse-attempt identity must be a UUID")
        if type(self.category) is not ReverseErrorCategory:
            raise ValueError("Reverse-attempt failure category is invalid")


ReverseAttemptResponse = ReverseAttemptSuccess | ReverseAttemptFailure
