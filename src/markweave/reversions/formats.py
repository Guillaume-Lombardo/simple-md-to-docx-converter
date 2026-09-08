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
    content_detection: str
    selected_parser_format: str | None = None


APPROVED_FORMATS = (
    ApprovedFormat(
        FormatFamily.WORD,
        (".doc", ".docx", ".docm"),
        ("doc", "docx"),
        "OLE WordDocument stream, or OPC officeDocument relationship/content type/root/main-part fallback",
    ),
    ApprovedFormat(
        FormatFamily.POWERPOINT,
        (".ppt", ".pps", ".pot", ".pptx", ".pptm", ".ppsx", ".ppsm"),
        ("ppt", "pptx"),
        "OLE PowerPoint Document stream, or OPC officeDocument relationship/content type/root/main-part fallback",
    ),
    ApprovedFormat(
        FormatFamily.EXCEL,
        (".xls", ".xlsx", ".xlsm", ".xlsb"),
        ("xlsx",),
        "OLE Workbook/Book stream, or OPC officeDocument relationship/content type/root/main-part fallback; the binding reports the shared parser as xlsx",
    ),
    ApprovedFormat(
        FormatFamily.OPENDOCUMENT,
        (".odt", ".ods", ".odp"),
        ("odt", "ods", "odp"),
        "ZIP mimetype entry or root manifest media type; template MIME variants map to their base parser",
    ),
    ApprovedFormat(
        FormatFamily.RTF,
        (".rtf",),
        ("rtf",),
        r"bytes start with the RTF opening group {\rtf",
    ),
    ApprovedFormat(
        FormatFamily.EPUB,
        (".epub",),
        ("epub",),
        "ZIP mimetype application/epub+zip or OCF META-INF/container.xml",
    ),
    ApprovedFormat(
        FormatFamily.CSV,
        (".csv",),
        (),
        "no content signature; .csv is only an admission hint and the server must explicitly select the CSV parser after bounded text validation",
        selected_parser_format="csv",
    ),
    ApprovedFormat(
        FormatFamily.PDF,
        (".pdf",),
        ("pdf",),
        "%PDF- within the first 1024 bytes, after higher-priority RTF/OLE/ZIP checks",
    ),
)


@dataclass(frozen=True, slots=True)
class ReverseAdmissionPolicy:
    """One typed authoritative capability and admission policy."""

    schema_version: int
    formats: tuple[ApprovedFormat, ...]
    extension_is_hint: bool
    mismatch_policy: str
    undetected_policy: str
    csv_policy: str
    scanner_order: str

    def normalize_extension(self, extension: str) -> str:
        """Return one case-normalized hint admitted by this policy."""

        if type(extension) is not str:
            reject(ReverseErrorCategory.UNSUPPORTED)
        normalized = extension.casefold()
        if not any(normalized in approved.extensions for approved in self.formats):
            reject(ReverseErrorCategory.UNSUPPORTED)
        return normalized

    def admit(
        self,
        extension: str,
        detected_format: str | None,
        *,
        csv_text_validated: bool = False,
    ) -> FormatAdmission:
        """Apply this policy to one extension hint and detected content format."""

        if detected_format is not None and type(detected_format) is not str:
            reject(ReverseErrorCategory.UNSUPPORTED)
        normalized_extension = self.normalize_extension(extension)
        approved = next(
            approved
            for approved in self.formats
            if normalized_extension in approved.extensions
        )

        if approved.family is FormatFamily.CSV:
            if detected_format is not None or csv_text_validated is not True:
                reject(ReverseErrorCategory.UNSUPPORTED)
            return FormatAdmission(
                family=approved.family,
                extension=normalized_extension,
                detected_format=None,
                parser_format=approved.selected_parser_format or "",
            )

        if detected_format is None:
            reject(ReverseErrorCategory.UNSUPPORTED)
        normalized_detected = detected_format.casefold()
        detected_family = next(
            (
                candidate
                for candidate in self.formats
                if normalized_detected in candidate.detected_formats
            ),
            None,
        )
        if detected_family is None or detected_family.family is not approved.family:
            reject(ReverseErrorCategory.UNSUPPORTED)
        return FormatAdmission(
            family=approved.family,
            extension=normalized_extension,
            detected_format=normalized_detected,
            parser_format=normalized_detected,
        )


REVERSE_ADMISSION_POLICY = ReverseAdmissionPolicy(
    schema_version=FORMAT_CAPABILITY_SCHEMA_VERSION,
    formats=APPROVED_FORMATS,
    extension_is_hint=True,
    mismatch_policy=(
        "reject when the content-detected family differs from the extension family"
    ),
    undetected_policy="reject except CSV after bounded text validation",
    csv_policy=next(
        approved.content_detection
        for approved in APPROVED_FORMATS
        if approved.family is FormatFamily.CSV
    ),
    scanner_order="malware scan before format parsing or durable persistence",
)


def normalize_extension_hint(extension: str) -> str:
    """Return one approved case-normalized hint without making it authoritative."""
    return REVERSE_ADMISSION_POLICY.normalize_extension(extension)


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
        expected = next(
            (
                approved
                for approved in REVERSE_ADMISSION_POLICY.formats
                if self.extension in approved.extensions
            ),
            None,
        )
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
    return REVERSE_ADMISSION_POLICY.admit(
        extension,
        detected_format,
        csv_text_validated=csv_text_validated,
    )
