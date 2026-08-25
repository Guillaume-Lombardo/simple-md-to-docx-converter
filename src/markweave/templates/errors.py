"""Stable template-domain failures independent of HTTP delivery."""

from enum import StrEnum


class TemplateUnavailableError(LookupError):
    """A requested identity is missing, inactive, or invisible."""

    def __init__(self) -> None:
        super().__init__("Template is not available")


class TemplatePreconditionRequiredError(RuntimeError):
    """A mutation omitted the required If-Match validator."""


class TemplateConflictError(RuntimeError):
    """A mutation raced with a newer representation or violates a guard."""


class TemplateStorageError(RuntimeError):
    """Template metadata or content storage failed without leaking details."""


class TemplateIntegrityError(RuntimeError):
    """Persisted bytes do not match immutable template metadata."""


class TemplateRequestError(ValueError):
    """User-supplied template metadata is invalid."""


class TemplateValidationErrorCode(StrEnum):
    """Content-free template validation categories for later API translation."""

    INVALID_PACKAGE = "invalid_package"
    LIMIT_EXCEEDED = "limit_exceeded"
    ACTIVE_CONTENT = "active_content"
    EXTERNAL_RELATIONSHIP = "external_relationship"
    REQUIRED_STYLES = "required_styles"
    FONT_CONTRACT = "font_contract"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    ENGINE_TIMEOUT = "engine_timeout"
    ENGINE_FAILURE = "engine_failure"


class TemplateValidationError(RuntimeError):
    """A stable failure that never includes uploaded content or local paths."""

    def __init__(self, code: TemplateValidationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
