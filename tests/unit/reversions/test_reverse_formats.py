"""Unit coverage for the exact T69 format-admission matrix."""

import json
from pathlib import Path
from typing import cast

import pytest
from pytest_mock import MockerFixture

from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.formats import (
    APPROVED_FORMATS,
    FORMAT_CAPABILITY_SCHEMA_VERSION,
    REVERSE_ADMISSION_POLICY,
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


def test_typed_policy_matches_the_approved_evidence_contract() -> None:
    contract = json.loads(
        (Path(__file__).parents[3] / "spikes/anydoc/contract.json").read_text()
    )
    policy = REVERSE_ADMISSION_POLICY

    assert policy.schema_version == contract["schema_version"]
    assert [
        {
            "family": approved.family.value,
            "extensions": list(approved.extensions),
            "detected_formats": list(approved.detected_formats),
            "content_detection": approved.content_detection,
            **(
                {"selected_parser_format": approved.selected_parser_format}
                if approved.selected_parser_format is not None
                else {}
            ),
        }
        for approved in policy.formats
    ] == contract["format_families"]
    assert (
        policy.extension_is_hint
        is not contract["admission"]["extension_is_authoritative"]
    )
    assert policy.mismatch_policy == contract["admission"]["mismatch_policy"]
    assert policy.undetected_policy == contract["admission"]["undetected_policy"]
    assert policy.scanner_order == contract["admission"]["scanner_order"]
    assert policy.csv_policy == contract["format_families"][6]["content_detection"]


def test_admit_format_delegates_to_the_exposed_policy(mocker: MockerFixture) -> None:
    expected = FormatAdmission(FormatFamily.WORD, ".docx", "docx", "docx")
    policy = mocker.patch("markweave.reversions.formats.REVERSE_ADMISSION_POLICY")
    policy.admit.return_value = expected

    assert admit_format(".DOCX", "DOCX") is expected
    policy.admit.assert_called_once_with(".DOCX", "DOCX", csv_text_validated=False)


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
