"""Unit coverage for the explicit local mode of ``markweave convert`` (T106, D1)."""

from __future__ import annotations

import argparse
import json
import os
import signal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from markweave.cli.commands import conversions, local_conversion
from markweave.cli.main import build_parser, main
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext, OutputFormat
from markweave.conversion import local as local_module
from markweave.conversion.engine_versions import (
    EngineObservation,
    EngineVersionStatus,
)
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.local import (
    LocalConversionError,
    LocalConversionResult,
    LocalOutput,
)
from markweave.conversion.local_settings import (
    LocalConfigurationError,
    LocalConversionSettings,
)
from markweave.jobs.models import SourceKind
from tests.settings import local_conversion_settings

pytestmark = pytest.mark.unit

PANDOC = EngineObservation("pandoc", "3.10.2", "3.10.2", EngineVersionStatus.PINNED)
OLD_PANDOC = EngineObservation(
    "pandoc", "3.10.2", "3.9.0", EngineVersionStatus.DIFFERENT
)


class FakeConverter:
    """Capture one local request and return or raise a configured outcome."""

    instances: ClassVar[list[FakeConverter]] = []
    outcome: ClassVar[Any] = None

    def __init__(self, settings: LocalConversionSettings) -> None:
        self.settings = settings
        self.requests: list[Any] = []
        self.options: dict[str, Any] = {}
        FakeConverter.instances.append(self)

    def convert(self, request: Any, **options: Any) -> LocalConversionResult:
        self.requests.append(request)
        self.options = options
        outcome = FakeConverter.outcome
        if callable(outcome):
            outcome = outcome(request, options)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return outcome
        return LocalConversionResult(
            {output: f"{output.value}-bytes".encode() for output in request.outputs},
            b'{"mode":"local"}',
            (PANDOC,),
        )


@pytest.fixture
def local(tmp_path: Path, mocker) -> Path:
    FakeConverter.instances = []
    FakeConverter.outcome = None
    mocker.patch.object(local_module, "LocalConverter", FakeConverter)
    mocker.patch.object(
        LocalConversionSettings,
        "load",
        return_value=local_conversion_settings(conversion_upload_max_bytes=64),
    )
    (tmp_path / "out").mkdir()
    (tmp_path / "doc.md").write_text("# Local\n", encoding="utf-8")
    return tmp_path


def run(capsys, *arguments: str) -> tuple[int, str, str]:
    status = main(arguments)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_help_documents_the_local_group_without_changing_remote_options() -> None:
    parser = build_parser()
    commands = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    help_text = commands.choices["convert"].format_help()
    assert "local conversion (no server):" in help_text
    for option in (
        "--local",
        "--to FORMATS",
        "--template DOCX",
        "-o, --output-dir",
        "--require-pinned",
        "--overwrite",
    ):
        assert option in help_text
    for option in ("--output", "--template-id", "--idempotency-key", "--profile"):
        assert option in help_text


def test_remote_convert_still_routes_to_the_http_handler() -> None:
    namespace = build_parser().parse_args(
        ("convert", "doc.md", "--output", "pdf", "--profile", "work")
    )
    assert namespace.command_handler is conversions._convert
    assert namespace.command_name.values == {
        "profile": "work",
        "output": "pdf",
        "retries": "0",
        "source": "doc.md",
    }


def test_local_docx_conversion_publishes_owner_only_outputs(
    local: Path, capsys
) -> None:
    status, out, err = run(
        capsys, "convert", str(local / "doc.md"), "--local", "-o", str(local / "out")
    )

    assert (status, err) == (0, "")
    assert (
        out == "Converted locally to doc.docx; traceability: doc.traceability.json.\n"
    )
    assert (local / "out" / "doc.docx").read_bytes() == b"docx-bytes"
    assert (local / "out" / "doc.traceability.json").read_bytes() == (
        b'{"mode":"local"}'
    )
    assert sorted(path.name for path in (local / "out").iterdir()) == [
        "doc.docx",
        "doc.traceability.json",
    ]
    assert (local / "out" / "doc.docx").stat().st_mode & 0o777 == 0o600
    request = FakeConverter.instances[0].requests[0]
    assert request.source == b"# Local\n"
    assert request.source_kind is SourceKind.MARKDOWN
    assert request.outputs == frozenset({LocalOutput.DOCX})
    assert request.template is None
    assert request.require_pinned is False
    assert FakeConverter.instances[0].options["deadline_monotonic"] is None


def test_local_json_output_reports_files_engines_and_warnings(
    local: Path, capsys, mocker
) -> None:
    (local / "t.docx").write_bytes(b"template")
    (local / "bundle.zip").write_bytes(b"PK\x03\x04archive")
    FakeConverter.outcome = lambda request, options: LocalConversionResult(
        {LocalOutput.DOCX: b"d", LocalOutput.PDF: b"pdf"},
        b"{}",
        (OLD_PANDOC,),
        ("Pandoc 3.9.0 differs from the approved pin 3.10.2.",),
    )
    monotonic = mocker.patch.object(local_conversion.time, "monotonic")
    monotonic.return_value = 10.0

    status, out, err = run(
        capsys,
        "--json",
        "--timeout",
        "30",
        "convert",
        str(local / "bundle.zip"),
        "--local",
        "--to",
        " PDF ,docx,pdf",
        "--template",
        str(local / "t.docx"),
        "--output-dir",
        str(local / "out"),
        "--require-pinned",
    )

    assert (status, err) == (0, "")
    assert json.loads(out) == {
        "engines": [
            {
                "actual_version": "3.9.0",
                "approved_version": "3.10.2",
                "name": "pandoc",
                "status": "different",
            }
        ],
        "engines_pinned": False,
        "manifest_file": "bundle.traceability.json",
        "mode": "local",
        "outputs": [
            {"bytes": 1, "file": "bundle.docx", "format": "docx"},
            {"bytes": 3, "file": "bundle.pdf", "format": "pdf"},
        ],
        "status": "succeeded",
        "warnings": ["Pandoc 3.9.0 differs from the approved pin 3.10.2."],
    }
    converter = FakeConverter.instances[0]
    request = converter.requests[0]
    assert request.source_kind is SourceKind.ARCHIVE
    assert request.template == b"template"
    assert request.require_pinned is True
    assert request.outputs == frozenset({LocalOutput.DOCX, LocalOutput.PDF})
    assert converter.options["deadline_monotonic"] == 40.0
    assert (local / "out" / "bundle.pdf").read_bytes() == b"pdf"


def test_human_warnings_go_to_stderr(local: Path, capsys) -> None:
    FakeConverter.outcome = LocalConversionResult(
        {LocalOutput.DOCX: b"d"},
        b"{}",
        (OLD_PANDOC,),
        ("Pandoc 3.9.0 differs from the approved pin 3.10.2.",),
    )
    status, out, err = run(
        capsys, "convert", str(local / "doc.md"), "--local", "-o", str(local / "out")
    )
    assert status == 0
    assert out.startswith("Converted locally")
    assert err == "warning: Pandoc 3.9.0 differs from the approved pin 3.10.2.\n"


def test_default_output_directory_is_the_current_directory(
    local: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(local / "out")
    status, _, _ = run(capsys, "convert", str(local / "doc.md"), "--local")
    assert status == 0
    assert (local / "out" / "doc.docx").is_file()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--to", "pdf"), "Local conversion options require --local."),
        (("-o", "out"), "Local conversion options require --local."),
        (("--local", "--profile", "work"), "--profile is not supported with --local."),
        (("--output", "pdf", "--local"), "--output is not supported with --local."),
        (
            ("--local", "--idempotency-key", "k"),
            "--idempotency-key is not supported with --local.",
        ),
        (("--local", "--to", "pptx"), "--to accepts a comma-separated list"),
        (("--local", "--to", "docx,,pdf"), "--to accepts a comma-separated list"),
    ],
)
def test_local_and_remote_options_cannot_be_mixed(
    local: Path, capsys, arguments: tuple[str, ...], message: str
) -> None:
    status, out, err = run(capsys, "convert", str(local / "doc.md"), *arguments)
    assert status == 2
    assert out == ""
    assert message in err
    assert FakeConverter.instances == []


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("doc.txt", "source_type_invalid"),
        (".md", "source_type_invalid"),
        ("doc\x07.md", "source_name_invalid"),
        (".hidden.md", "source_name_invalid"),
    ],
)
def test_source_names_are_validated(local: Path, capsys, name: str, code: str) -> None:
    status, _, err = run(
        capsys, "--json", "convert", str(local / name), "--local", "-o", str(local)
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == code


def test_unsafe_unavailable_empty_and_oversized_sources_are_refused(
    local: Path, capsys
) -> None:
    (local / "link.md").symlink_to(local / "doc.md")
    (local / "dir.md").mkdir()
    (local / "empty.md").write_bytes(b"")
    (local / "large.md").write_bytes(b"x" * 65)
    for name, code in (
        ("link.md", "source_unsafe"),
        ("dir.md", "source_unsafe"),
        ("missing.md", "source_unavailable"),
        ("empty.md", "source_empty"),
        ("large.md", "source_too_large"),
    ):
        status, _, err = run(
            capsys, "--json", "convert", str(local / name), "--local", "-o", str(local)
        )
        assert status == 1
        assert json.loads(err)["error"]["code"] == code, name
    assert FakeConverter.instances == []


def test_fifo_source_is_unsafe_without_blocking(local: Path, capsys) -> None:
    os.mkfifo(local / "pipe.md")
    status, _, err = run(
        capsys, "--json", "convert", str(local / "pipe.md"), "--local", "-o", str(local)
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "source_unsafe"


def test_template_must_be_a_bounded_regular_docx(local: Path, capsys) -> None:
    (local / "t.dotx").write_bytes(b"x")
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "--template",
        str(local / "t.dotx"),
    )
    assert (status, json.loads(err)["error"]["code"]) == (1, "template_type_invalid")
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "--template",
        str(local / "missing.docx"),
        "-o",
        str(local / "out"),
    )
    assert (status, json.loads(err)["error"]["code"]) == (1, "template_unavailable")


def test_configuration_errors_name_variables_only(local: Path, capsys, mocker) -> None:
    mocker.patch.object(
        LocalConversionSettings,
        "load",
        side_effect=LocalConfigurationError(
            ("MARKWEAVE_CONVERSION_MAX_FILES", "MARKWEAVE_TEMPLATE_MAX_ENTRIES")
        ),
    )
    status, _, err = run(capsys, "convert", str(local / "doc.md"), "--local")
    assert status == 1
    assert err == (
        "error: Local conversion configuration is missing or invalid: "
        "MARKWEAVE_CONVERSION_MAX_FILES, MARKWEAVE_TEMPLATE_MAX_ENTRIES.\n"
    )


def test_output_directory_must_be_an_existing_real_directory(
    local: Path, capsys
) -> None:
    (local / "linked").symlink_to(local / "out", target_is_directory=True)
    for target in ("missing", "linked", "doc.md"):
        status, _, err = run(
            capsys,
            "--json",
            "convert",
            str(local / "doc.md"),
            "--local",
            "-o",
            str(local / target),
        )
        assert status == 1
        assert json.loads(err)["error"]["code"] == "output_directory_invalid"
    assert FakeConverter.instances == []


def test_existing_outputs_are_refused_before_conversion(local: Path, capsys) -> None:
    (local / "out" / "doc.traceability.json").write_bytes(b"keep")
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_exists"
    assert FakeConverter.instances == []
    assert (local / "out" / "doc.traceability.json").read_bytes() == b"keep"
    assert not (local / "out" / "doc.docx").exists()


def test_overwrite_replaces_only_regular_files(local: Path, capsys) -> None:
    (local / "out" / "doc.docx").write_bytes(b"old")
    status, _, _ = run(
        capsys,
        "convert",
        str(local / "doc.md"),
        "--local",
        "--overwrite",
        "-o",
        str(local / "out"),
    )
    assert status == 0
    assert (local / "out" / "doc.docx").read_bytes() == b"docx-bytes"

    (local / "out" / "doc.docx").unlink()
    (local / "out" / "doc.docx").symlink_to(local / "doc.md")
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "--overwrite",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_unsafe"
    assert (local / "doc.md").read_text(encoding="utf-8") == "# Local\n"


def test_concurrent_destination_creation_is_not_overwritten(
    local: Path, capsys
) -> None:
    def create_concurrently(request: Any, options: Any) -> LocalConversionResult:
        (local / "out" / "doc.traceability.json").write_bytes(b"concurrent")
        return LocalConversionResult({LocalOutput.DOCX: b"d"}, b"{}", (PANDOC,))

    FakeConverter.outcome = create_concurrently
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_exists"
    assert sorted(path.name for path in (local / "out").iterdir()) == [
        "doc.traceability.json"
    ]
    assert (local / "out" / "doc.traceability.json").read_bytes() == b"concurrent"


def test_link_race_and_write_failures_leave_no_partial_outputs(
    local: Path, capsys, mocker
) -> None:
    original_link = os.link
    calls = {"count": 0}

    def racing_link(source: str, target: str, **options: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise FileExistsError(target)
        original_link(source, target, **options)

    mocker.patch.object(local_conversion.os, "link", side_effect=racing_link)
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_exists"
    assert list((local / "out").iterdir()) == []

    mocker.patch.object(local_conversion.os, "link", side_effect=OSError("full"))
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_write_failed"
    assert list((local / "out").iterdir()) == []


def test_temporary_output_creation_failure_is_safe(local: Path, capsys, mocker) -> None:
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **options: Any) -> int:
        if str(path).startswith(".markweave-local-"):
            raise PermissionError(path)
        return original_open(path, flags, *args, **options)

    mocker.patch.object(local_conversion.os, "open", side_effect=failing_open)
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_write_failed"


def test_unreadable_destination_metadata_is_a_safe_failure(
    local: Path, capsys, mocker
) -> None:
    mocker.patch.object(
        local_conversion.os, "stat", side_effect=PermissionError("denied")
    )
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"]["code"] == "output_write_failed"


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (
            LocalConversionError(
                "engine_missing",
                "The required LibreOffice engine is missing or cannot run.",
            ),
            "engine_missing",
            "The required LibreOffice engine is missing or cannot run.",
        ),
        (
            ConversionError(
                ConversionErrorCode.VALIDATION, "Markdown input contains raw HTML."
            ),
            "validation",
            "Markdown input contains raw HTML.",
        ),
        (
            ConversionError(ConversionErrorCode.PDF_FAILURE, ""),
            "pdf_failure",
            "The local conversion failed.",
        ),
    ],
)
def test_conversion_failures_keep_stable_safe_errors(
    local: Path, capsys, error: Exception, code: str, message: str
) -> None:
    FakeConverter.outcome = error
    status, out, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert (status, out) == (1, "")
    assert json.loads(err) == {"error": {"code": code, "message": message}}
    assert list((local / "out").iterdir()) == []


def test_elapsed_global_timeout_is_reported_as_a_timeout(
    local: Path, capsys, mocker
) -> None:
    clock = iter((0.0, 5.0))
    mocker.patch.object(
        local_conversion.time, "monotonic", side_effect=lambda: next(clock)
    )
    FakeConverter.outcome = ConversionError(
        ConversionErrorCode.PANDOC_TIMEOUT, "Pandoc conversion timed out."
    )
    status, _, err = run(
        capsys,
        "--json",
        "--timeout",
        "1",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"] == {
        "code": "conversion_timeout",
        "message": "The local conversion timed out.",
    }


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signals_cancel_cooperatively_and_restore_handlers(
    local: Path, capsys, signum: signal.Signals
) -> None:
    previous = signal.getsignal(signum)

    def interrupted(request: Any, options: Any) -> ConversionError:
        os.kill(os.getpid(), signum)
        assert options["cancellation_requested"]() is True
        return ConversionError(
            ConversionErrorCode.PANDOC_FAILURE, "Pandoc conversion was interrupted."
        )

    FakeConverter.outcome = interrupted
    status, _, err = run(
        capsys,
        "--json",
        "convert",
        str(local / "doc.md"),
        "--local",
        "-o",
        str(local / "out"),
    )
    assert status == 1
    assert json.loads(err)["error"] == {
        "code": "conversion_cancelled",
        "message": "The local conversion was cancelled.",
    }
    assert signal.getsignal(signum) is previous
    assert list((local / "out").iterdir()) == []


def test_cancellation_after_successful_conversion_publishes_nothing(
    local: Path, capsys
) -> None:
    def cancelled_late(request: Any, options: Any) -> LocalConversionResult:
        os.kill(os.getpid(), signal.SIGTERM)
        return LocalConversionResult({LocalOutput.DOCX: b"d"}, b"{}", (PANDOC,))

    FakeConverter.outcome = cancelled_late
    status, _, err = run(
        capsys, "convert", str(local / "doc.md"), "--local", "-o", str(local / "out")
    )
    assert status == 1
    assert err == "error: The local conversion was cancelled.\n"
    assert list((local / "out").iterdir()) == []


def test_signal_handlers_are_not_installed_off_the_main_thread(mocker) -> None:
    mocker.patch.object(
        local_conversion.threading, "current_thread", return_value=object()
    )
    install = mocker.patch.object(local_conversion.signal, "signal")
    with local_conversion._Cancellation() as cancelled:
        assert not cancelled.is_set()
    install.assert_not_called()


def test_invalid_positional_value_is_a_usage_error() -> None:
    namespace = build_parser().parse_args(("convert", "doc.md", "--local"))
    namespace.command_name.namespace.source = ""
    with pytest.raises(local_conversion.CliError) as captured:
        local_conversion._convert_locally(
            CommandContext(
                OutputFormat.HUMAN, non_interactive=True, timeout_seconds=None
            ),
            OutputWriter(OutputFormat.HUMAN),
            namespace.command_name,
        )
    assert captured.value.code == "invalid_request"
    assert local_conversion._formats(None) == ("docx",)
    with pytest.raises(local_conversion.CliError):
        local_conversion._formats(["docx"])
