"""Approved reverse-conversion format admission without engine imports."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from markweave.reversions.errors import ReverseErrorCategory, reject

FORMAT_CAPABILITY_SCHEMA_VERSION = 1


class FormatFamily(StrEnum):
    """Ordered format families approved by T69."""

    WORD = "word"
    POWERPOINT = "powerpoint"
    EXCEL = "excel"
    OPENDOCUMENT = "opendocument"
    RTF = "rtf"
    EPUB = "epub"
    CSV = "csv"
    PDF = "pdf"


@dataclass(frozen=True, slots=True)
class ApprovedFormat:
    """One immutable family entry from the approved capability matrix."""

    family: FormatFamily
    extensions: tuple[str, ...]
    detected_formats: tuple[str, ...]
    csv_parser_format: str | None = None


APPROVED_FORMATS = (
    ApprovedFormat(FormatFamily.WORD, (".doc", ".docx", ".docm"), ("doc", "docx")),
    ApprovedFormat(
        FormatFamily.POWERPOINT,
        (".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm"),
        ("ppt", "pptx"),
    ),
    ApprovedFormat(
        FormatFamily.EXCEL,
        (".xls", ".xlsx", ".xlsm", ".xlsb"),
        ("xlsx",),
    ),
    ApprovedFormat(
        FormatFamily.OPENDOCUMENT,
        (".odt", ".ods", ".odp"),
        ("odt", "ods", "odp"),
    ),
    ApprovedFormat(FormatFamily.RTF, (".rtf",), ("rtf",)),
    ApprovedFormat(FormatFamily.EPUB, (".epub",), ("epub",)),
    ApprovedFormat(FormatFamily.CSV, (".csv",), (), csv_parser_format="csv"),
    ApprovedFormat(FormatFamily.PDF, (".pdf",), ("pdf",)),
)

_BY_EXTENSION = {
    extension: approved
    for approved in APPROVED_FORMATS
    for extension in approved.extensions
}
_BY_DETECTED_FORMAT = {
    detected: approved
    for approved in APPROVED_FORMATS
    for detected in approved.detected_formats
}


def normalize_extension_hint(extension: str) -> str:
    """Return one approved case-normalized hint without making it authoritative."""

    if type(extension) is not str:
        reject(ReverseErrorCategory.UNSUPPORTED)
    normalized = extension.casefold()
    if normalized not in _BY_EXTENSION:
        reject(ReverseErrorCategory.UNSUPPORTED)
    return normalized


@dataclass(frozen=True, slots=True)
class FormatAdmission:
    """Content-detected format selected for one filename-extension hint."""

    family: FormatFamily
    extension: str
    detected_format: str | None
    parser_format: str

    def __post_init__(self) -> None:
        if (
            type(self.family) is not FormatFamily
            or type(self.extension) is not str
            or (
                self.detected_format is not None
                and type(self.detected_format) is not str
            )
            or type(self.parser_format) is not str
        ):
            reject(ReverseErrorCategory.UNSUPPORTED)
        expected = _BY_EXTENSION.get(self.extension)
        if expected is None or expected.family is not self.family:
            reject(ReverseErrorCategory.UNSUPPORTED)
        if self.family is FormatFamily.CSV:
            if self.detected_format is not None or self.parser_format != "csv":
                reject(ReverseErrorCategory.UNSUPPORTED)
            return
        if (
            self.detected_format not in expected.detected_formats
            or self.parser_format != self.detected_format
        ):
            reject(ReverseErrorCategory.UNSUPPORTED)


def admit_format(
    extension: str,
    detected_format: str | None,
    *,
    csv_text_validated: bool = False,
) -> FormatAdmission:
    """Apply the extension-hint/content-detection agreement fixed by T69."""

    if detected_format is not None and type(detected_format) is not str:
        reject(ReverseErrorCategory.UNSUPPORTED)
    normalized_extension = normalize_extension_hint(extension)
    approved = _BY_EXTENSION[normalized_extension]

    if approved.family is FormatFamily.CSV:
        if detected_format is not None or csv_text_validated is not True:
            reject(ReverseErrorCategory.UNSUPPORTED)
        return FormatAdmission(
            family=approved.family,
            extension=normalized_extension,
            detected_format=None,
            parser_format="csv",
        )

    if detected_format is None:
        reject(ReverseErrorCategory.UNSUPPORTED)
    normalized_detected = detected_format.casefold()
    detected_family = _BY_DETECTED_FORMAT.get(normalized_detected)
    if detected_family is None or detected_family.family is not approved.family:
        reject(ReverseErrorCategory.UNSUPPORTED)
    return FormatAdmission(
        family=approved.family,
        extension=normalized_extension,
        detected_format=normalized_detected,
        parser_format=normalized_detected,
    )
