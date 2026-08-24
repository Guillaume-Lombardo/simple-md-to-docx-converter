"""Stable, content-free conversion errors."""

from enum import StrEnum


class ConversionErrorCode(StrEnum):
    """Stable machine-readable failure categories."""

    VALIDATION = "validation"
    WORKSPACE_FAILURE = "workspace_failure"
    PANDOC_UNAVAILABLE = "pandoc_unavailable"
    PANDOC_TIMEOUT = "pandoc_timeout"
    PANDOC_FAILURE = "pandoc_failure"
    INVALID_DOCX = "invalid_docx"
    MERMAID_UNAVAILABLE = "mermaid_unavailable"
    MERMAID_TIMEOUT = "mermaid_timeout"
    MERMAID_FAILURE = "mermaid_failure"
    INVALID_MERMAID_OUTPUT = "invalid_mermaid_output"
    LIBREOFFICE_UNAVAILABLE = "libreoffice_unavailable"
    PDF_TIMEOUT = "pdf_timeout"
    PDF_CANCELLED = "pdf_cancelled"
    PDF_FAILURE = "pdf_failure"
    PDF_LIMIT_EXCEEDED = "pdf_limit_exceeded"
    INVALID_PDF = "invalid_pdf"


class ConversionError(RuntimeError):
    """A safe conversion failure suitable for later job/API translation."""

    def __init__(self, code: ConversionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def validation_error(message: str) -> ConversionError:
    return ConversionError(ConversionErrorCode.VALIDATION, message)
