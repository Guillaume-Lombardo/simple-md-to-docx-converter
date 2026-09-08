"""Content-free reverse-conversion capabilities derived from server policy."""

from __future__ import annotations

from dataclasses import dataclass

from markweave.reversions.formats import (
    REVERSE_ADMISSION_POLICY,
    ReverseAdmissionPolicy,
)
from markweave.reversions.models import ReverseOutputMode


class ReversionCapabilitiesUnavailableError(RuntimeError):
    """Raised when the optional reverse API has no configured upload ceiling."""


@dataclass(frozen=True, slots=True)
class PdfCapabilities:
    """Honest limitations of the pinned PDF conversion path."""

    contract: str = "text extraction only"
    document_model_available: bool = False
    embedded_assets_available: bool = False
    image_preservation: bool = False
    mixed_or_image_only_pages: str = (
        "reject the complete input as needs_ocr when any page yields no text"
    )
    warning: str = (
        "PDF images, layout, and source-position image links are not preserved"
    )


@dataclass(frozen=True, slots=True)
class ExecutionCapabilities:
    """Client-visible locality, OCR, and hosted-fallback guarantees."""

    local: bool = True
    ocr: bool = False
    hosted_fallback: bool = False


@dataclass(frozen=True, slots=True)
class ReversionCapabilities:
    """One immutable runtime capabilities snapshot."""

    admission: ReverseAdmissionPolicy
    maximum_upload_bytes: int
    result_package_modes: tuple[ReverseOutputMode, ...]
    pdf: PdfCapabilities
    execution: ExecutionCapabilities

    @property
    def schema_version(self) -> int:
        return self.admission.schema_version


def build_reversion_capabilities(
    maximum_upload_bytes: int | None,
) -> ReversionCapabilities:
    """Build capabilities or fail safely when reverse admission is not configured."""

    if type(maximum_upload_bytes) is not int or maximum_upload_bytes <= 0:
        raise ReversionCapabilitiesUnavailableError
    return ReversionCapabilities(
        admission=REVERSE_ADMISSION_POLICY,
        maximum_upload_bytes=maximum_upload_bytes,
        result_package_modes=tuple(ReverseOutputMode),
        pdf=PdfCapabilities(),
        execution=ExecutionCapabilities(),
    )
