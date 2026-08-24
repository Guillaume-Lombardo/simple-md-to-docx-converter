"""Unit and security coverage for isolated LibreOffice PDF conversion."""

from __future__ import annotations

import io
import json
import signal
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from pypdf import PdfWriter
from pypdf.errors import LimitReachedError, PyPdfError
from pypdf.generic import DictionaryObject, NameObject, TextStringObject
from pytest_mock import MockerFixture

from md_converter.conversion import libreoffice
from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.libreoffice import (
    LibreOfficeConfig,
    LibreOfficePdfConverter,
    PdfLimits,
    PdfTraceabilityContext,
)

pytestmark = pytest.mark.unit
LIMITS = PdfLimits(
    max_docx_bytes=1_000_000,
    max_docx_entries=1_000,
    max_docx_member_uncompressed_bytes=1_000_000,
    max_docx_total_uncompressed_bytes=2_000_000,
    max_docx_compression_ratio=100.0,
    max_pdf_bytes=1_000_000,
    max_pdf_decoded_stream_bytes=1_000_000,
    max_pages=10,
    max_pdf_objects=1_000,
    max_pdf_object_depth=40,
)
TRACE = PdfTraceabilityContext(
    application_version="0.1.0",
    conversion_contract_version="1",
    template_id="template-id",
    template_version="template-version",
    template_sha256="1" * 64,
    pandoc_version="3.10.2",
    pandoc_reader="commonmark_x",
    mermaid_version="11.12.0",
    chromium_version="140.0.7339.207",
    font_manifest_sha256="2" * 64,
)


def _docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            archive.writestr(name, b"safe")
    return output.getvalue()


def _pdf(*, pages: int = 1, active: bool = False, encrypted: bool = False) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if active:
        writer.root_object[NameObject("/OpenAction")] = TextStringObject("unsafe")
    if encrypted:
        writer.encrypt("secret")
    writer.write(output)
    return output.getvalue()


def _config(tmp_path: Path) -> LibreOfficeConfig:
    return LibreOfficeConfig(
        "soffice",
        "LibreOffice 26.2.5.2 approved-build",
        2.0,
        0.2,
        0.05,
        tmp_path,
    )


def _converter(tmp_path: Path) -> LibreOfficePdfConverter:
    return LibreOfficePdfConverter(
        _config(tmp_path),
        LIMITS,
        {
            "PATH": "/bin",
            "FONTCONFIG_FILE": "/fonts.conf",
            "FONTCONFIG_PATH": "/fonts",
            "SECRET": "must-not-pass",
        },
    )


def _successful_process(
    mocker: MockerFixture, pdf: bytes | None = None
) -> tuple[Any, Any]:
    process = mocker.Mock(pid=321)
    process.wait.return_value = 0

    def start(*_: object, **kwargs: object) -> Any:
        workspace = Path(str(kwargs["cwd"]))
        (workspace / "output" / "source.pdf").write_bytes(pdf or _pdf())
        return process

    popen = mocker.patch.object(libreoffice.subprocess, "Popen", side_effect=start)
    mocker.patch.object(libreoffice, "_terminate_group")
    return process, popen


@pytest.mark.parametrize(
    "arguments",
    (
        ("", "version", 1.0, 1.0, 0.1, None),
        ("bad\0path", "version", 1.0, 1.0, 0.1, None),
        ("soffice", " ", 1.0, 1.0, 0.1, None),
        ("soffice", "version", 0.0, 1.0, 0.1, None),
        ("soffice", "version", 1.0, float("inf"), 0.1, None),
        ("soffice", "version", 1.0, 1.0, True, None),
        ("soffice", "version", 1.0, 1.0, 0.1, Path("/absent")),
    ),
)
def test_configuration_rejects_unsafe_or_unbounded_values(
    arguments: tuple[str, str, float, float, float, Path | None],
) -> None:
    with pytest.raises(ValueError):
        LibreOfficeConfig(*arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_docx_bytes", 0),
        ("max_docx_entries", 0),
        ("max_docx_member_uncompressed_bytes", 0),
        ("max_docx_total_uncompressed_bytes", 0),
        ("max_pdf_bytes", True),
        ("max_pdf_decoded_stream_bytes", 0),
        ("max_pages", -1),
        ("max_pdf_objects", 0),
        ("max_pdf_object_depth", 0),
    ),
)
def test_limits_require_positive_integers(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(LIMITS, **{field: value})


@pytest.mark.parametrize("value", (0.99, float("inf"), True))
def test_docx_compression_ratio_must_be_explicit_and_bounded(value: object) -> None:
    with pytest.raises(ValueError):
        replace(LIMITS, max_docx_compression_ratio=value)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("application_version", ""),
        ("template_id", "bad\nvalue"),
        ("template_version", "x" * 257),
        ("template_sha256", "A" * 64),
        ("font_manifest_sha256", "0" * 63),
    ),
)
def test_traceability_context_rejects_unsafe_metadata(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        replace(TRACE, **{field: value})


def test_environment_is_allowlisted_and_workspace_scoped(tmp_path: Path) -> None:
    environment = libreoffice._environment(
        tmp_path,
        {
            "PATH": "/bin",
            "FONTCONFIG_FILE": "/fonts.conf",
            "FONTCONFIG_PATH": "/fonts",
            "SECRET": "hidden",
        },
    )
    assert environment == {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PATH": "/bin",
        "FONTCONFIG_FILE": "/fonts.conf",
        "FONTCONFIG_PATH": "/fonts",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }


def test_success_uses_fixed_isolated_arguments_and_canonical_manifest(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    expected_pdf = _pdf()
    _, popen = _successful_process(mocker, expected_pdf)
    artifact = _converter(tmp_path).convert(_docx(), TRACE)
    assert artifact.pdf == expected_pdf
    manifest = artifact.manifest
    assert manifest.schema_version == 1
    assert manifest.output_pdf_bytes == len(expected_pdf)
    assert manifest.pages[0].width_points == 612
    decoded = json.loads(manifest.canonical_json())
    assert decoded["template_id"] == "template-id"
    assert decoded["export_filter"] == "pdf:writer_pdf_Export"
    assert decoded["output_format"] == "pdf"
    assert decoded["source_docx_sha256"] != decoded["output_pdf_sha256"]
    assert b"/tmp/" not in manifest.canonical_json()
    arguments = popen.call_args.args[0]
    assert arguments[1:5] == [
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
    ]
    assert "pdf:writer_pdf_Export" in arguments
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["start_new_session"] is True
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("docx", "limits", "expected"),
    (
        (b"invalid", LIMITS, ConversionErrorCode.INVALID_DOCX),
        (
            _docx(),
            replace(LIMITS, max_docx_bytes=1),
            ConversionErrorCode.PDF_LIMIT_EXCEEDED,
        ),
    ),
)
def test_input_is_validated_before_engine_start(
    mocker: MockerFixture,
    tmp_path: Path,
    docx: bytes,
    limits: PdfLimits,
    expected: ConversionErrorCode,
) -> None:
    popen = mocker.patch.object(libreoffice.subprocess, "Popen")
    converter = LibreOfficePdfConverter(_config(tmp_path), limits, {})
    with pytest.raises(ConversionError) as captured:
        converter.convert(docx, TRACE)
    assert captured.value.code is expected
    popen.assert_not_called()


@pytest.mark.parametrize("docx", (b"", cast(bytes, "not-bytes")))
def test_empty_and_non_byte_inputs_are_rejected(
    mocker: MockerFixture, tmp_path: Path, docx: bytes
) -> None:
    popen = mocker.patch.object(libreoffice.subprocess, "Popen")
    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(docx, TRACE)
    assert captured.value.code is ConversionErrorCode.INVALID_DOCX
    popen.assert_not_called()


def test_oversized_input_is_rejected_before_zip_preflight(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    preflight = mocker.patch.object(libreoffice, "_safe_docx")
    converter = LibreOfficePdfConverter(
        _config(tmp_path), replace(LIMITS, max_docx_bytes=1), {}
    )
    with pytest.raises(ConversionError) as captured:
        converter.convert(_docx(), TRACE)
    assert captured.value.code is ConversionErrorCode.PDF_LIMIT_EXCEEDED
    preflight.assert_not_called()


def test_docx_preflight_enforces_archive_structure_limits() -> None:
    assert not libreoffice._safe_docx(_docx(), replace(LIMITS, max_docx_entries=2))
    assert not libreoffice._safe_docx(
        _docx(), replace(LIMITS, max_docx_total_uncompressed_bytes=3)
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"x" * 10_000)
        archive.writestr("_rels/.rels", b"safe")
        archive.writestr("word/document.xml", b"safe")
    assert not libreoffice._safe_docx(
        output.getvalue(), replace(LIMITS, max_docx_compression_ratio=2.0)
    )


def test_cancellation_before_start_and_probe_failure_are_stable(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    popen = mocker.patch.object(libreoffice.subprocess, "Popen")
    converter = _converter(tmp_path)
    with pytest.raises(ConversionError) as cancelled:
        converter.convert(_docx(), TRACE, lambda: True)
    assert cancelled.value.code is ConversionErrorCode.PDF_CANCELLED

    def broken_probe() -> bool:
        raise RuntimeError("sensitive")

    with pytest.raises(ConversionError) as failed:
        converter.convert(_docx(), TRACE, broken_probe)
    assert failed.value.code is ConversionErrorCode.PDF_FAILURE
    assert "sensitive" not in str(failed.value)

    with pytest.raises(ConversionError) as non_boolean:
        converter.convert(_docx(), TRACE, cast(Callable[[], bool], lambda: 1))
    assert non_boolean.value.code is ConversionErrorCode.PDF_FAILURE
    popen.assert_not_called()


def test_cancellation_during_wait_terminates_the_group(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    process = mocker.Mock(pid=321)
    popen = mocker.patch.object(libreoffice.subprocess, "Popen", return_value=process)
    terminate = mocker.patch.object(libreoffice, "_terminate_group")
    probe = mocker.Mock(side_effect=(False, True))
    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(_docx(), TRACE, probe)
    assert captured.value.code is ConversionErrorCode.PDF_CANCELLED
    popen.assert_called_once()
    terminate.assert_called_once_with(process, 0.2)


def test_cancellation_probe_failure_during_wait_terminates_the_group(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    process = mocker.Mock(pid=321)
    mocker.patch.object(libreoffice.subprocess, "Popen", return_value=process)
    terminate = mocker.patch.object(libreoffice, "_terminate_group")

    probe_calls = 0

    def broken_probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls > 1:
            raise RuntimeError("sensitive")
        return False

    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(_docx(), TRACE, broken_probe)
    assert captured.value.code is ConversionErrorCode.PDF_FAILURE
    assert "sensitive" not in str(captured.value)
    terminate.assert_called_once_with(process, 0.2)


def test_cancellation_after_success_prevents_output_publication(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    _successful_process(mocker)
    probe = mocker.Mock(side_effect=(False, False, True))
    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(_docx(), TRACE, probe)
    assert captured.value.code is ConversionErrorCode.PDF_CANCELLED


def test_timeout_and_nonzero_exit_are_distinct(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    converter = _converter(tmp_path)
    terminate = mocker.patch.object(libreoffice, "_terminate_group")
    process = mocker.Mock(pid=321)
    mocker.patch.object(libreoffice.subprocess, "Popen", return_value=process)
    mocker.patch.object(libreoffice.time, "monotonic", side_effect=(0.0, 3.0))
    with pytest.raises(ConversionError) as timed_out:
        converter.convert(_docx(), TRACE)
    assert timed_out.value.code is ConversionErrorCode.PDF_TIMEOUT
    terminate.assert_called_once_with(process, 0.2)

    process.wait.return_value = 7
    mocker.patch.object(libreoffice.time, "monotonic", side_effect=(0.0, 0.1))
    terminate.reset_mock()
    with pytest.raises(ConversionError) as failed:
        converter.convert(_docx(), TRACE)
    assert failed.value.code is ConversionErrorCode.PDF_FAILURE
    terminate.assert_called_once_with(process, 0.2)


def test_unavailable_engine_is_content_free(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.object(libreoffice.subprocess, "Popen", side_effect=OSError("path"))
    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(_docx(), TRACE)
    assert captured.value.code is ConversionErrorCode.LIBREOFFICE_UNAVAILABLE
    assert "path" not in str(captured.value)


@pytest.mark.parametrize("mode", ("absent", "empty", "directory", "symlink"))
def test_invalid_output_files_fail_closed(
    mocker: MockerFixture, tmp_path: Path, mode: str
) -> None:
    process = mocker.Mock(pid=321)
    process.wait.return_value = 0

    def start(*_: object, **kwargs: object) -> Any:
        output = Path(str(kwargs["cwd"])) / "output" / "source.pdf"
        if mode == "empty":
            output.touch()
        elif mode == "directory":
            output.mkdir()
        elif mode == "symlink":
            output.symlink_to("/etc/passwd")
        return process

    mocker.patch.object(libreoffice.subprocess, "Popen", side_effect=start)
    mocker.patch.object(libreoffice, "_terminate_group")
    with pytest.raises(ConversionError) as captured:
        _converter(tmp_path).convert(_docx(), TRACE)
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


def test_oversized_and_malformed_pdf_outputs_are_rejected(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    oversized = _pdf()
    _successful_process(mocker, oversized)
    converter = LibreOfficePdfConverter(
        _config(tmp_path), replace(LIMITS, max_pdf_bytes=len(oversized) - 1), {}
    )
    with pytest.raises(ConversionError) as too_large:
        converter.convert(_docx(), TRACE)
    assert too_large.value.code is ConversionErrorCode.PDF_LIMIT_EXCEEDED

    _successful_process(mocker, b"%PDF-1.7\ntruncated")
    with pytest.raises(ConversionError) as invalid:
        _converter(tmp_path).convert(_docx(), TRACE)
    assert invalid.value.code is ConversionErrorCode.INVALID_PDF


def test_pdf_structure_rejects_encryption_active_features_and_limits() -> None:
    for data in (_pdf(encrypted=True), _pdf(active=True)):
        with pytest.raises(ConversionError) as captured:
            libreoffice._validate_pdf(data, LIMITS)
        assert captured.value.code is ConversionErrorCode.INVALID_PDF
    with pytest.raises(ConversionError) as pages:
        libreoffice._validate_pdf(_pdf(pages=2), replace(LIMITS, max_pages=1))
    assert pages.value.code is ConversionErrorCode.PDF_LIMIT_EXCEEDED
    with pytest.raises(ConversionError) as objects:
        libreoffice._validate_pdf(_pdf(), replace(LIMITS, max_pdf_objects=1))
    assert objects.value.code is ConversionErrorCode.PDF_LIMIT_EXCEEDED


@pytest.mark.parametrize("action_name", ("/Launch", "/SubmitForm", "/ImportData"))
def test_pdf_structure_rejects_indirect_active_action_name(
    action_name: str,
) -> None:
    writer = PdfWriter()
    action = writer._add_object(NameObject(action_name))
    dictionary = DictionaryObject({NameObject("/S"): action})
    with pytest.raises(ConversionError) as captured:
        libreoffice._walk_pdf_dictionary(
            dictionary, libreoffice._PdfWalkState(LIMITS), 0
        )
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


def test_pdf_structure_allows_only_navigation_and_uri_actions() -> None:
    for action_name, extra in (
        ("/GoTo", {}),
        ("/URI", {NameObject("/URI"): TextStringObject("https://example.com/path")}),
    ):
        dictionary = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Action"),
                NameObject("/S"): NameObject(action_name),
                **extra,
            }
        )
        libreoffice._walk_pdf_dictionary(
            dictionary,
            libreoffice._PdfWalkState(LIMITS),
            0,
        )
    unknown = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Action"),
            NameObject("/S"): NameObject("/UnknownAction"),
        }
    )
    with pytest.raises(ConversionError) as captured:
        libreoffice._walk_pdf_dictionary(
            unknown,
            libreoffice._PdfWalkState(LIMITS),
            0,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


@pytest.mark.parametrize(
    "target",
    ("file:///etc/passwd", "javascript:alert(1)", "https:///missing-host"),
)
def test_pdf_structure_rejects_unsafe_uri_actions(target: str) -> None:
    dictionary = DictionaryObject(
        {
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject(target),
        }
    )
    parent = DictionaryObject({NameObject("/A"): dictionary})
    with pytest.raises(ConversionError) as captured:
        libreoffice._walk_pdf_dictionary(
            parent,
            libreoffice._PdfWalkState(LIMITS),
            0,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


def test_pdf_structure_rejects_unknown_action_without_optional_type() -> None:
    action = DictionaryObject({NameObject("/S"): NameObject("/UnknownAction")})
    annotation = DictionaryObject({NameObject("/A"): action})
    with pytest.raises(ConversionError) as captured:
        libreoffice._walk_pdf_dictionary(
            annotation,
            libreoffice._PdfWalkState(LIMITS),
            0,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


@pytest.mark.parametrize(
    "action",
    (
        DictionaryObject({NameObject("/S"): NameObject("/UnknownAction")}),
        DictionaryObject(
            {
                NameObject("/S"): NameObject("/URI"),
                NameObject("/URI"): TextStringObject("file:///etc/passwd"),
            }
        ),
    ),
)
def test_pdf_structure_revalidates_shared_indirect_objects_as_actions(
    action: DictionaryObject,
) -> None:
    writer = PdfWriter()
    shared = writer._add_object(action)
    annotation = DictionaryObject(
        {
            NameObject("/X"): shared,
            NameObject("/A"): shared,
        }
    )
    with pytest.raises(ConversionError) as captured:
        libreoffice._walk_pdf_dictionary(
            annotation,
            libreoffice._PdfWalkState(LIMITS),
            0,
        )
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


def test_pdf_structure_allows_structure_attributes_named_a() -> None:
    attributes = DictionaryObject(
        {
            NameObject("/O"): NameObject("/Layout"),
            NameObject("/Placement"): NameObject("/Inline"),
        }
    )
    structure_element = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructElem"),
            NameObject("/S"): NameObject("/P"),
            NameObject("/A"): attributes,
        }
    )
    libreoffice._walk_pdf_dictionary(
        structure_element,
        libreoffice._PdfWalkState(LIMITS),
        0,
    )


def test_malformed_page_tree_is_normalized_to_invalid_pdf() -> None:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    del writer.root_object[NameObject("/Pages")]
    writer.write(output)
    with pytest.raises(ConversionError) as captured:
        libreoffice._validate_pdf(output.getvalue(), LIMITS)
    assert captured.value.code is ConversionErrorCode.INVALID_PDF


@pytest.mark.parametrize(
    ("parser_error", "expected"),
    (
        (LimitReachedError("limit"), ConversionErrorCode.PDF_LIMIT_EXCEEDED),
        (PyPdfError("invalid"), ConversionErrorCode.INVALID_PDF),
    ),
)
def test_pdf_parser_errors_are_stable_and_filter_limits_are_restored(
    mocker: MockerFixture,
    parser_error: PyPdfError,
    expected: ConversionErrorCode,
) -> None:
    original = libreoffice.pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH
    mocker.patch.object(libreoffice, "PdfReader", side_effect=parser_error)
    with pytest.raises(ConversionError) as captured:
        libreoffice._validate_pdf(_pdf(), LIMITS)
    assert captured.value.code is expected
    assert original == libreoffice.pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH


def test_process_group_termination_checks_descendants(
    mocker: MockerFixture,
) -> None:
    process = mocker.Mock(pid=321)
    process.poll.return_value = 0
    exists = mocker.patch.object(
        libreoffice, "_process_group_exists", side_effect=(True, True)
    )
    mocker.patch.object(libreoffice.time, "monotonic", side_effect=(0.0, 0.2))
    killpg = mocker.patch.object(libreoffice.os, "killpg")
    libreoffice._terminate_group(process, 0.2)
    assert killpg.call_args_list == [
        mocker.call(321, signal.SIGTERM),
        mocker.call(321, signal.SIGKILL),
    ]
    assert exists.call_count == 2


def test_process_group_lifecycle_handles_absent_and_graceful_exit(
    mocker: MockerFixture,
) -> None:
    killpg = mocker.patch.object(
        libreoffice.os, "killpg", side_effect=ProcessLookupError
    )
    assert not libreoffice._process_group_exists(321)
    process = mocker.Mock(pid=321)
    libreoffice._terminate_group(process, 0.2)
    assert killpg.call_count == 2

    killpg.side_effect = None
    exists = mocker.patch.object(
        libreoffice, "_process_group_exists", side_effect=(True, False, False)
    )
    process.poll.return_value = None
    process.wait.return_value = 0
    mocker.patch.object(libreoffice.time, "monotonic", return_value=0.0)
    libreoffice._terminate_group(process, 0.2)
    assert exists.call_count == 3
    process.wait.assert_called_once_with(timeout=0.2)


def test_workspace_and_cleanup_failures_are_stable(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.object(libreoffice.tempfile, "TemporaryDirectory", side_effect=OSError)
    with pytest.raises(ConversionError) as creation:
        _converter(tmp_path).convert(_docx(), TRACE)
    assert creation.value.code is ConversionErrorCode.WORKSPACE_FAILURE

    temporary = mocker.Mock()
    temporary.name = str(tmp_path / "workspace")
    Path(temporary.name).mkdir()
    temporary.cleanup.side_effect = OSError
    mocker.patch.object(
        libreoffice.tempfile, "TemporaryDirectory", return_value=temporary
    )
    _successful_process(mocker)
    with pytest.raises(ConversionError) as cleanup:
        _converter(tmp_path).convert(_docx(), TRACE)
    assert cleanup.value.code is ConversionErrorCode.WORKSPACE_FAILURE
