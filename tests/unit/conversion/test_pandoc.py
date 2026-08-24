"""Unit tests for the isolated Pandoc adapter."""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.pandoc import PandocConfig, PandocDocxConverter
from md_converter.conversion.validation import PANDOC_READER, ApprovedMarkdown


def minimal_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("word/document.xml", "<document/>")
    return output.getvalue()


def converter(tmp_path: Path) -> PandocDocxConverter:
    return PandocDocxConverter(
        PandocConfig("pandoc", 5.0, 0.5, tmp_path),
        {
            "LANG": "C.UTF-8",
            "PATH": "/approved/bin",
            "SECRET_VALUE": "must-not-pass",
        },
    )


@pytest.mark.unit
def test_adapter_uses_fixed_arguments_isolated_workspace_and_allowlisted_environment(
    tmp_path: Path, mocker
) -> None:
    reference = minimal_docx()
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(arguments, **options):
        workspace = options["cwd"]
        assert isinstance(workspace, Path)
        (workspace / "output.docx").write_bytes(reference)
        assert arguments == [
            "pandoc",
            f"--from={PANDOC_READER}",
            "--to=docx",
            "--reference-doc=reference.docx",
            f"--resource-path={workspace}",
            "--output=output.docx",
            "input.md",
        ]
        assert options["shell"] is False
        assert options["start_new_session"] is True
        assert options["stdin"] is subprocess.DEVNULL
        assert options["stdout"] is subprocess.DEVNULL
        assert options["stderr"] is subprocess.DEVNULL
        assert set(options["env"]) == {
            "HOME",
            "LANG",
            "PATH",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
        }
        assert "SECRET_VALUE" not in options["env"]
        return process

    popen = mocker.patch(
        "md_converter.conversion.pandoc.subprocess.Popen", side_effect=start
    )
    result = converter(tmp_path).convert(ApprovedMarkdown("# Safe"), reference)
    assert result == reference
    assert popen.call_count == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_reference_document_is_opaque_to_t07(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.wait.return_value = 2

    def start(_arguments, **options):
        assert (options["cwd"] / "reference.docx").read_bytes() == b"opaque-reference"
        return process

    popen = mocker.patch(
        "md_converter.conversion.pandoc.subprocess.Popen", side_effect=start
    )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), b"opaque-reference")
    assert captured.value.code is ConversionErrorCode.PANDOC_FAILURE
    popen.assert_called_once()


@pytest.mark.unit
def test_adapter_revalidates_approved_type_before_subprocess(
    tmp_path: Path, mocker
) -> None:
    popen = mocker.patch("md_converter.conversion.pandoc.subprocess.Popen")
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(
            ApprovedMarkdown("<script>bad()</script>"), b"opaque"
        )
    assert captured.value.code is ConversionErrorCode.VALIDATION
    popen.assert_not_called()


@pytest.mark.unit
def test_unavailable_pandoc_has_stable_content_free_error(
    tmp_path: Path, mocker
) -> None:
    mocker.patch(
        "md_converter.conversion.pandoc.subprocess.Popen",
        side_effect=FileNotFoundError("sensitive path and input"),
    )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("secret document"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.PANDOC_UNAVAILABLE
    assert str(captured.value) == "Pandoc is unavailable."


@pytest.mark.unit
def test_nonzero_exit_has_stable_error(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.wait.return_value = 23
    mocker.patch(
        "md_converter.conversion.pandoc.subprocess.Popen", return_value=process
    )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.PANDOC_FAILURE
    assert str(captured.value) == "Pandoc conversion failed."


@pytest.mark.unit
def test_invalid_output_has_stable_error(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(_arguments, **options):
        (options["cwd"] / "output.docx").write_bytes(b"invalid")
        return process

    mocker.patch("md_converter.conversion.pandoc.subprocess.Popen", side_effect=start)
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.INVALID_DOCX
    assert str(captured.value) == "Pandoc produced an invalid DOCX document."


@pytest.mark.unit
@pytest.mark.parametrize("failure_point", ["create", "prepare", "read", "cleanup"])
def test_workspace_failures_have_stable_content_free_error(
    tmp_path: Path, mocker, failure_point: str
) -> None:
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(_arguments, **options):
        (options["cwd"] / "output.docx").write_bytes(minimal_docx())
        return process

    mocker.patch("md_converter.conversion.pandoc.subprocess.Popen", side_effect=start)
    sensitive = OSError("sensitive workspace path and document")
    if failure_point == "create":
        mocker.patch(
            "md_converter.conversion.pandoc.tempfile.TemporaryDirectory",
            side_effect=sensitive,
        )
    elif failure_point == "prepare":
        mocker.patch("md_converter.conversion.pandoc.Path.mkdir", side_effect=sensitive)
    elif failure_point == "read":
        mocker.patch(
            "md_converter.conversion.pandoc.Path.read_bytes", side_effect=sensitive
        )
    else:
        mocker.patch(
            "md_converter.conversion.pandoc.tempfile.TemporaryDirectory.cleanup",
            side_effect=sensitive,
        )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.WORKSPACE_FAILURE
    assert str(captured.value) == "The conversion workspace failed."


@pytest.mark.unit
def test_timeout_terminates_then_kills_the_process_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock(pid=4321)
    process.wait.side_effect = [
        subprocess.TimeoutExpired("pandoc", 5.0),
        subprocess.TimeoutExpired("pandoc", 0.5),
        0,
    ]
    mocker.patch(
        "md_converter.conversion.pandoc.subprocess.Popen", return_value=process
    )
    killpg = mocker.patch("md_converter.conversion.pandoc.os.killpg")
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.PANDOC_TIMEOUT
    assert str(captured.value) == "Pandoc conversion timed out."
    assert killpg.call_args_list == [mocker.call(4321, 15), mocker.call(4321, 9)]
    assert process.wait.call_count == 3


@pytest.mark.unit
@pytest.mark.parametrize(
    "config",
    [
        ("", 1.0, 1.0),
        ("pandoc", 0.0, 1.0),
        ("pandoc", float("inf"), 1.0),
        ("pandoc", float("nan"), 1.0),
        ("pandoc", True, 1.0),
        ("pandoc", 1.0, 0.0),
    ],
)
def test_adapter_configuration_fails_closed(config: tuple[str, float, float]) -> None:
    with pytest.raises(ValueError):
        PandocConfig(*config)
