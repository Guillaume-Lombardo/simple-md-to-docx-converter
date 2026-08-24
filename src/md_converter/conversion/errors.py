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


class ConversionError(RuntimeError):
    """A safe conversion failure suitable for later job/API translation."""

    def __init__(self, code: ConversionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def validation_error(message: str) -> ConversionError:
    return ConversionError(ConversionErrorCode.VALIDATION, message)
