"""Stable, content-free reverse-conversion failures."""

from enum import StrEnum
from typing import Never


class ReverseErrorCategory(StrEnum):
    """Machine-readable failure categories approved by the reverse contract."""

    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    ENCRYPTED = "encrypted"
    RESOURCE_LIMIT = "resource_limit"
    NEEDS_OCR = "needs_ocr"
    ASSET_INVALID = "asset_invalid"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LEASE_LOST = "lease_lost"
    PROTOCOL_ERROR = "protocol_error"


_MESSAGES: dict[ReverseErrorCategory, str] = {
    ReverseErrorCategory.UNSUPPORTED: "The document format is not supported.",
    ReverseErrorCategory.MALFORMED: "The document is malformed.",
    ReverseErrorCategory.ENCRYPTED: "Encrypted documents are not supported.",
    ReverseErrorCategory.RESOURCE_LIMIT: (
        "The document exceeds a configured resource limit."
    ),
    ReverseErrorCategory.NEEDS_OCR: "The document requires OCR, which is not available.",
    ReverseErrorCategory.ASSET_INVALID: "The document contains an invalid image asset.",
    ReverseErrorCategory.CANCELLED: "The reverse conversion was cancelled.",
    ReverseErrorCategory.TIMED_OUT: "The reverse conversion timed out.",
    ReverseErrorCategory.LEASE_LOST: "The reverse conversion lease was lost.",
    ReverseErrorCategory.PROTOCOL_ERROR: (
        "The reverse-conversion attempt protocol is invalid."
    ),
}


class ReverseConversionError(RuntimeError):
    """A fixed-message failure safe for logs and later API translation."""

    def __init__(self, category: ReverseErrorCategory) -> None:
        self.category = category
        self.message = _MESSAGES[category]
        super().__init__(self.message)


def reject(category: ReverseErrorCategory) -> Never:
    """Raise one fresh content-free reverse-conversion error."""

    raise ReverseConversionError(category)
