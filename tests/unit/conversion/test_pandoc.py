"""Unit tests for the isolated Pandoc adapter."""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import pytest
from PIL import Image

from markweave.conversion.archive import ApprovedResource
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits, normalize_image
from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
from markweave.conversion.validation import PANDOC_READER, ApprovedMarkdown

IMAGE_LIMITS = ImageLimits(100_000, 256, 256, 65_536, 1_000, 64)


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
            "LANG": "host-dependent",
            "LC_ALL": "host-dependent",
            "PATH": "/approved/bin",
            "TZ": "host-dependent",
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
            f"--reference-doc={workspace / 'reference.docx'}",
            f"--resource-path={workspace / 'package'}",
            f"--output={workspace / 'output.docx'}",
            str(workspace / "package/input.md"),
        ]
        assert options["shell"] is False
        assert options["start_new_session"] is True
        assert options["stdin"] is subprocess.DEVNULL
        assert options["stdout"] is subprocess.DEVNULL
        assert options["stderr"] is subprocess.DEVNULL
        assert set(options["env"]) == {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "TMPDIR",
            "TZ",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
        }
        assert "SECRET_VALUE" not in options["env"]
        assert options["env"]["LANG"] == "C.UTF-8"
        assert options["env"]["LC_ALL"] == "C.UTF-8"
        assert options["env"]["TZ"] == "UTC"
        return process

    popen = mocker.patch(
        "markweave.conversion.pandoc.subprocess.Popen", side_effect=start
    )
    result = converter(tmp_path).convert(ApprovedMarkdown("# Safe"), reference)
    assert result == reference
    assert popen.call_count == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_adapter_uses_pandoc_default_when_reference_is_omitted(
    tmp_path: Path, mocker
) -> None:
    output = minimal_docx()
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(arguments, **options):
        workspace = options["cwd"]
        assert not (workspace / "reference.docx").exists()
        assert not any(
            argument.startswith("--reference-doc=") for argument in arguments
        )
        (workspace / "output.docx").write_bytes(output)
        return process

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    assert converter(tmp_path).convert(ApprovedMarkdown("# Default"), None) == output


@pytest.mark.unit
def test_each_conversion_uses_a_distinct_cleaned_workspace(
    tmp_path: Path, mocker
) -> None:
    reference = minimal_docx()
    workspaces: list[Path] = []
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(_arguments, **options):
        workspace = options["cwd"]
        workspaces.append(workspace)
        (workspace / "output.docx").write_bytes(reference)
        return process

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    adapter = converter(tmp_path)
    adapter.convert(ApprovedMarkdown("# First"), reference)
    adapter.convert(ApprovedMarkdown("# Second"), reference)
    assert len(set(workspaces)) == 2
    assert all(not workspace.exists() for workspace in workspaces)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_adapter_materializes_only_approved_package_resources(
    tmp_path: Path, mocker
) -> None:
    reference = minimal_docx()
    source = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(source, format="PNG")
    png = normalize_image(PurePosixPath("image.png"), source.getvalue(), IMAGE_LIMITS)
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(arguments, **options):
        workspace = options["cwd"]
        assert (workspace / "package/docs/readme.md").read_text() == (
            "![safe](../assets/image.svg)"
        )
        assert (workspace / "package/assets/image.svg").read_bytes() == png
        assert arguments[4] == f"--resource-path={workspace / 'package/docs'}"
        assert arguments[6] == str(workspace / "package/docs/readme.md")
        (workspace / "output.docx").write_bytes(reference)
        return process

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    approved = ApprovedMarkdown(
        "![safe](../assets/image.svg)",
        entrypoint=PurePosixPath("docs/readme.md"),
        resources=(ApprovedResource(PurePosixPath("assets/image.svg"), png),),
        image_limits=IMAGE_LIMITS,
    )
    assert converter(tmp_path).convert(approved, reference) == reference
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_reference_document_is_opaque_to_t07(tmp_path: Path, mocker) -> None:
    process = mocker.Mock()
    process.wait.return_value = 2

    def start(_arguments, **options):
        assert (options["cwd"] / "reference.docx").read_bytes() == b"opaque-reference"
        return process

    popen = mocker.patch(
        "markweave.conversion.pandoc.subprocess.Popen", side_effect=start
    )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), b"opaque-reference")
    assert captured.value.code is ConversionErrorCode.PANDOC_FAILURE
    popen.assert_called_once()


@pytest.mark.unit
def test_adapter_revalidates_approved_type_before_subprocess(
    tmp_path: Path, mocker
) -> None:
    popen = mocker.patch("markweave.conversion.pandoc.subprocess.Popen")
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
        "markweave.conversion.pandoc.subprocess.Popen",
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
    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", return_value=process)
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

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
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

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    sensitive = OSError("sensitive workspace path and document")
    if failure_point == "create":
        mocker.patch(
            "markweave.conversion.pandoc.tempfile.TemporaryDirectory",
            side_effect=sensitive,
        )
    elif failure_point == "prepare":
        mocker.patch("markweave.conversion.pandoc.Path.mkdir", side_effect=sensitive)
    elif failure_point == "read":
        mocker.patch(
            "markweave.conversion.pandoc.Path.read_bytes", side_effect=sensitive
        )
    else:
        mocker.patch(
            "markweave.conversion.pandoc.tempfile.TemporaryDirectory.cleanup",
            side_effect=sensitive,
        )
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.WORKSPACE_FAILURE
    assert str(captured.value) == "The conversion workspace failed."


@pytest.mark.unit
def test_cleanup_failure_replaces_conversion_failure_with_workspace_error(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock()
    process.wait.return_value = 23
    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", return_value=process)
    mocker.patch(
        "markweave.conversion.pandoc.tempfile.TemporaryDirectory.cleanup",
        side_effect=OSError("sensitive cleanup path"),
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
    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", return_value=process)
    killpg = mocker.patch("markweave.conversion.pandoc.os.killpg")
    with pytest.raises(ConversionError) as captured:
        converter(tmp_path).convert(ApprovedMarkdown("# Safe"), minimal_docx())
    assert captured.value.code is ConversionErrorCode.PANDOC_TIMEOUT
    assert str(captured.value) == "Pandoc conversion timed out."
    assert killpg.call_args_list == [mocker.call(4321, 15), mocker.call(4321, 9)]
    assert process.wait.call_count == 3


@pytest.mark.unit
def test_active_cancellation_terminates_pandoc_process_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock(pid=4321)
    process.wait.side_effect = [subprocess.TimeoutExpired("pandoc", 0.1), 0]
    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", return_value=process)
    killpg = mocker.patch("markweave.conversion.pandoc.os.killpg")
    cancelled = mocker.Mock(side_effect=(False, True))

    with pytest.raises(ConversionError, match="interrupted") as captured:
        converter(tmp_path).convert(
            ApprovedMarkdown("# Safe"),
            minimal_docx(),
            cancellation_requested=cancelled,
        )

    assert captured.value.code is ConversionErrorCode.PANDOC_FAILURE
    killpg.assert_called_once_with(4321, 15)
    assert process.wait.call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize("probe_failure", [False, True])
def test_cancellable_wait_handles_expired_deadline_and_probe_failure(
    tmp_path: Path, mocker, probe_failure: bool
) -> None:
    process = mocker.Mock(pid=4321)
    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", return_value=process)
    mocker.patch("markweave.conversion.pandoc.time.monotonic", return_value=10.0)
    killpg = mocker.patch(
        "markweave.conversion.pandoc.os.killpg", side_effect=ProcessLookupError
    )
    probe = (
        mocker.Mock(side_effect=RuntimeError("probe failed"))
        if probe_failure
        else mocker.Mock(return_value=False)
    )

    expected = RuntimeError if probe_failure else ConversionError
    with pytest.raises(expected):
        converter(tmp_path).convert(
            ApprovedMarkdown("# Safe"),
            minimal_docx(),
            deadline_monotonic=9.0,
            cancellation_requested=probe,
        )
    killpg.assert_called_once_with(4321, 15)


@pytest.mark.unit
def test_adapter_without_host_path_and_invalid_workspace_are_explicit(
    tmp_path: Path, mocker
) -> None:
    reference = minimal_docx()
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(_arguments, **options):
        assert "PATH" not in options["env"]
        (options["cwd"] / "output.docx").write_bytes(reference)
        return process

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    adapter = PandocDocxConverter(PandocConfig("pandoc", 5.0, 0.5, tmp_path), {})
    assert adapter.convert(ApprovedMarkdown("# Safe"), reference) == reference

    with pytest.raises(ValueError, match="workspace root"):
        PandocConfig("pandoc", 5.0, 0.5, tmp_path / "missing")


@pytest.mark.unit
def test_worker_deadline_caps_pandoc_engine_timeout(tmp_path: Path, mocker) -> None:
    reference = minimal_docx()
    process = mocker.Mock()
    process.wait.return_value = 0

    def start(_arguments, **options):
        (options["cwd"] / "output.docx").write_bytes(reference)
        return process

    mocker.patch("markweave.conversion.pandoc.subprocess.Popen", side_effect=start)
    mocker.patch("markweave.conversion.pandoc.time.monotonic", return_value=10.0)

    assert (
        converter(tmp_path).convert(
            ApprovedMarkdown("# Safe"),
            reference,
            deadline_monotonic=11.25,
        )
        == reference
    )
    process.wait.assert_called_once_with(timeout=1.25)


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
