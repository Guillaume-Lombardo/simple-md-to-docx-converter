"""Unit coverage for the exact T69 format-admission matrix."""

from typing import cast

import pytest

from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.formats import (
    APPROVED_FORMATS,
    FORMAT_CAPABILITY_SCHEMA_VERSION,
    FormatAdmission,
    FormatFamily,
    admit_format,
)

pytestmark = pytest.mark.unit


def test_approved_matrix_is_ordered_and_contains_all_twenty_one_extensions() -> None:
    assert FORMAT_CAPABILITY_SCHEMA_VERSION == 1
    assert tuple(entry.family for entry in APPROVED_FORMATS) == tuple(FormatFamily)
    assert sum(len(entry.extensions) for entry in APPROVED_FORMATS) == 21
    assert tuple(
        extension for entry in APPROVED_FORMATS for extension in entry.extensions
    ) == (
        ".doc",
        ".docx",
        ".docm",
        ".ppt",
        ".pps",
        ".pot",
        ".pptx",
        ".pptm",
        ".ppsx",
        ".ppsm",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".epub",
        ".csv",
        ".pdf",
    )


@pytest.mark.parametrize(
    ("extension", "detected", "family"),
    (
        (".doc", "doc", FormatFamily.WORD),
        (".DOCM", "DOCX", FormatFamily.WORD),
        (".pps", "ppt", FormatFamily.POWERPOINT),
        (".pptm", "pptx", FormatFamily.POWERPOINT),
        (".xls", "xlsx", FormatFamily.EXCEL),
        (".xlsb", "xlsx", FormatFamily.EXCEL),
        (".odt", "odt", FormatFamily.OPENDOCUMENT),
        (".ods", "ods", FormatFamily.OPENDOCUMENT),
        (".odp", "odp", FormatFamily.OPENDOCUMENT),
        (".rtf", "rtf", FormatFamily.RTF),
        (".epub", "epub", FormatFamily.EPUB),
        (".pdf", "pdf", FormatFamily.PDF),
    ),
)
def test_content_detection_must_agree_with_extension_family(
    extension: str, detected: str, family: FormatFamily
) -> None:
    admission = admit_format(extension, detected)

    assert admission.family is family
    assert admission.extension == extension.casefold()
    assert admission.detected_format == detected.casefold()
    assert admission.parser_format == detected.casefold()


def test_csv_requires_the_extension_and_separate_bounded_text_validation() -> None:
    admission = admit_format(".CSV", None, csv_text_validated=True)

    assert admission == FormatAdmission(FormatFamily.CSV, ".csv", None, "csv")


@pytest.mark.parametrize(
    ("extension", "detected", "csv_validated"),
    (
        (".unknown", "docx", False),
        (".docx", None, False),
        (".docx", "pdf", False),
        (".pdf", "docx", False),
        (".csv", None, False),
        (".csv", None, 1),
        (".csv", "csv", True),
        ("csv", None, True),
    ),
)
def test_unknown_undetected_and_mismatched_inputs_fail_content_free(
    extension: str, detected: str | None, csv_validated: bool
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        admit_format(extension, detected, csv_text_validated=csv_validated)

    assert captured.value.category is ReverseErrorCategory.UNSUPPORTED
    assert extension not in str(captured.value)


def test_admission_models_reject_type_confusion_and_inconsistent_parser_values() -> (
    None
):
    invalid = (
        lambda: admit_format(cast(str, 1), "docx"),
        lambda: FormatAdmission(cast(FormatFamily, "word"), ".docx", "docx", "docx"),
        lambda: FormatAdmission(FormatFamily.CSV, ".csv", None, "docx"),
        lambda: FormatAdmission(FormatFamily.WORD, ".docx", "docx", "doc"),
    )
    for operation in invalid:
        with pytest.raises(ReverseConversionError) as captured:
            operation()
        assert captured.value.category is ReverseErrorCategory.UNSUPPORTED
