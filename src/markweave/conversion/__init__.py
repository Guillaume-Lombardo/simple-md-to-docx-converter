"""Markdown-to-document conversion contracts with lazy optional-engine imports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from markweave.conversion.archive import (
        ApprovedDocument,
        ApprovedResource,
        ArchiveLimits,
    )
    from markweave.conversion.errors import ConversionError, ConversionErrorCode
    from markweave.conversion.images import ImageLimits
    from markweave.conversion.libreoffice import (
        LibreOfficeConfig,
        LibreOfficePdfConverter,
        PdfArtifact,
        PdfLimits,
        PdfTraceabilityContext,
        PdfTraceabilityManifest,
    )
    from markweave.conversion.mermaid import (
        MermaidCliRenderer,
        MermaidConfig,
        MermaidLimits,
        MermaidPreprocessingConverter,
    )
    from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
    from markweave.conversion.service import DocxConversionService

__all__ = [
    "ApprovedDocument",
    "ApprovedResource",
    "ArchiveLimits",
    "ConversionError",
    "ConversionErrorCode",
    "DocxConversionService",
    "ImageLimits",
    "LibreOfficeConfig",
    "LibreOfficePdfConverter",
    "MermaidCliRenderer",
    "MermaidConfig",
    "MermaidLimits",
    "MermaidPreprocessingConverter",
    "PandocConfig",
    "PandocDocxConverter",
    "PdfArtifact",
    "PdfLimits",
    "PdfTraceabilityContext",
    "PdfTraceabilityManifest",
]

_EXPORT_MODULES = {
    "ApprovedDocument": "archive",
    "ApprovedResource": "archive",
    "ArchiveLimits": "archive",
    "ConversionError": "errors",
    "ConversionErrorCode": "errors",
    "DocxConversionService": "service",
    "ImageLimits": "images",
    "LibreOfficeConfig": "libreoffice",
    "LibreOfficePdfConverter": "libreoffice",
    "MermaidCliRenderer": "mermaid",
    "MermaidConfig": "mermaid",
    "MermaidLimits": "mermaid",
    "MermaidPreprocessingConverter": "mermaid",
    "PandocConfig": "pandoc",
    "PandocDocxConverter": "pandoc",
    "PdfArtifact": "libreoffice",
    "PdfLimits": "libreoffice",
    "PdfTraceabilityContext": "libreoffice",
    "PdfTraceabilityManifest": "libreoffice",
}


def __getattr__(name: str) -> Any:
    """Load only the optional conversion component requested by the caller."""

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the stable public surface without importing optional engines."""

    return sorted((*globals(), *__all__))
