"""Branch coverage for isolated template engine orchestration."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from md_converter.templates import engines
from md_converter.templates.engines import (
    TemplateActivationContext,
    TemplateEngineConfig,
    validate_template_for_activation,
)
from md_converter.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from md_converter.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    TemplateLimits,
    ValidatedTemplate,
)

pytestmark = pytest.mark.unit
LIMITS = TemplateLimits(1000, 10, 500, 1000, 10.0, 100, 10, 100, 5, 40)
DECLARATION = TemplateFontDeclaration(("Calibri",))
VALIDATED = ValidatedTemplate(
    "0" * 64,
    ("word/document.xml",),
    ("Calibri",),
    ("Calibri",),
    (("Calibri", "Carlito"),),
)


@pytest.mark.parametrize(
    "arguments",
    (
        ("", "soffice", 1.0, 1.0, None),
        ("pandoc", "bad\0path", 1.0, 1.0, None),
        ("pandoc", "soffice", 0.0, 1.0, None),
        ("pandoc", "soffice", 1.0, float("inf"), None),
        ("pandoc", "soffice", 1.0, 1.0, Path("/absent-template-root")),
    ),
)
def test_engine_configuration_rejects_unsafe_or_unbounded_values(
    arguments: tuple[str, str, float, float, Path | None],
) -> None:
    with pytest.raises(ValueError):
        TemplateEngineConfig(*arguments)


def test_environment_is_allowlisted_and_workspace_scoped(tmp_path: Path) -> None:
    environment = engines._environment(
        tmp_path,
        {
            "PATH": "/bin",
            "FONTCONFIG_FILE": "/fonts.conf",
            "FONTCONFIG_PATH": "/fonts",
            "SECRET": "must-not-pass",
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


def test_bounded_output_requires_a_nonempty_regular_file(tmp_path: Path) -> None:
    output = tmp_path / "output.docx"
    output.write_bytes(b"valid")
    assert engines._bounded_regular_file(output, 5) == b"valid"
    for invalid in (tmp_path / "absent", tmp_path):
        with pytest.raises(TemplateValidationError) as captured:
            engines._bounded_regular_file(invalid, 5)
        assert captured.value.code is TemplateValidationErrorCode.ENGINE_FAILURE
    output.write_bytes(b"too-large")
    with pytest.raises(TemplateValidationError):
        engines._bounded_regular_file(output, 5)
    output.write_bytes(b"valid")
    link = tmp_path / "output-link.docx"
    link.symlink_to(output)
    with pytest.raises(TemplateValidationError):
        engines._bounded_regular_file(link, 5)
    empty = tmp_path / "empty.docx"
    empty.touch()
    with pytest.raises(TemplateValidationError):
        engines._bounded_regular_file(empty, 5)


@dataclass(frozen=True)
class _RunFailure:
    start_error: OSError | None
    return_code: int
    wait_error: subprocess.TimeoutExpired | None
    expected: TemplateValidationErrorCode


@pytest.mark.parametrize(
    "case",
    (
        _RunFailure(
            FileNotFoundError(), 0, None, TemplateValidationErrorCode.ENGINE_UNAVAILABLE
        ),
        _RunFailure(
            PermissionError(), 0, None, TemplateValidationErrorCode.ENGINE_UNAVAILABLE
        ),
        _RunFailure(OSError(), 0, None, TemplateValidationErrorCode.ENGINE_FAILURE),
        _RunFailure(None, 7, None, TemplateValidationErrorCode.ENGINE_FAILURE),
        _RunFailure(
            None,
            0,
            subprocess.TimeoutExpired("engine", 1.0),
            TemplateValidationErrorCode.ENGINE_TIMEOUT,
        ),
    ),
)
def test_run_normalizes_start_exit_and_timeout_failures(
    mocker: MockerFixture,
    tmp_path: Path,
    case: _RunFailure,
) -> None:
    process = mocker.Mock()
    process.wait.side_effect = case.wait_error
    if case.wait_error is None:
        process.wait.return_value = case.return_code
    popen = mocker.patch.object(
        engines.subprocess,
        "Popen",
        side_effect=case.start_error,
        return_value=process,
    )
    terminate = mocker.patch.object(engines, "_terminate_group")
    config = TemplateEngineConfig("engine", "soffice", 1.0, 0.5, tmp_path)
    with pytest.raises(TemplateValidationError) as captured:
        engines._run(("engine", "--fixed"), tmp_path, {"PATH": "/bin"}, config)
    assert captured.value.code is case.expected
    if case.start_error is None:
        popen.assert_called_once()
    if case.wait_error is not None:
        terminate.assert_called_once_with(process, 0.5)


def test_run_success_uses_shell_free_isolated_process(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    process = mocker.Mock()
    process.wait.return_value = 0
    popen = mocker.patch.object(engines.subprocess, "Popen", return_value=process)
    config = TemplateEngineConfig("engine", "soffice", 1.0, 0.5, tmp_path)
    engines._run(("engine", "--fixed"), tmp_path, {"PATH": "/bin"}, config)
    assert popen.call_args.kwargs["start_new_session"] is True
    assert popen.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_terminate_group_checks_the_whole_group_before_escalating(
    mocker: MockerFixture,
) -> None:
    process = mocker.Mock(pid=321)
    process.poll.return_value = 0
    group_exists = mocker.patch.object(
        engines, "_process_group_exists", side_effect=(True, True)
    )
    mocker.patch.object(engines.time, "monotonic", side_effect=(0.0, 0.1))
    killpg = mocker.patch.object(engines.os, "killpg")
    engines._terminate_group(process, 0.1)
    assert killpg.call_args_list == [
        mocker.call(321, engines.signal.SIGTERM),
        mocker.call(321, engines.signal.SIGKILL),
    ]
    assert group_exists.call_count == 2
    process.wait.assert_called_once_with()


def test_terminate_group_accepts_an_already_absent_group(
    mocker: MockerFixture,
) -> None:
    process = mocker.Mock(pid=321)
    killpg = mocker.patch.object(engines.os, "killpg", side_effect=ProcessLookupError)
    engines._terminate_group(process, 0.1)
    killpg.assert_called_once_with(321, engines.signal.SIGTERM)
    process.wait.assert_not_called()


def test_process_group_probe_and_graceful_termination(
    mocker: MockerFixture,
) -> None:
    killpg = mocker.patch.object(engines.os, "killpg")
    assert engines._process_group_exists(123) is True
    killpg.side_effect = ProcessLookupError
    assert engines._process_group_exists(123) is False

    process = mocker.Mock(pid=321)
    process.poll.return_value = None
    process.wait.return_value = 0
    mocker.patch.object(
        engines, "_process_group_exists", side_effect=(True, False, False)
    )
    mocker.patch.object(engines.time, "monotonic", side_effect=(0.0, 0.05))
    killpg.side_effect = None
    engines._terminate_group(process, 0.1)
    killpg.assert_called_with(321, engines.signal.SIGTERM)
    process.wait.assert_called_once_with(timeout=0.05)


def test_activation_runs_static_pandoc_and_libreoffice_checks_in_order(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    static = mocker.patch.object(engines, "validate_template", return_value=VALIDATED)
    run = mocker.patch.object(engines, "_run")
    bounded = mocker.patch.object(
        engines, "_bounded_regular_file", side_effect=(b"pandoc", b"libreoffice")
    )
    rewritten = mocker.patch.object(engines, "_validate_template")
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig("pandoc", "soffice", 1.0, 0.5, tmp_path),
        {"PATH": os.environ.get("PATH", "")},
    )
    assert (
        validate_template_for_activation(b"template", DECLARATION, context) is VALIDATED
    )
    assert static.call_count == 2
    assert run.call_count == 2
    assert bounded.call_count == 2
    rewritten.assert_called_once_with(
        b"libreoffice",
        DECLARATION,
        LIMITS,
        APPROVED_FONT_POLICY,
        enforce_referenced_fonts=False,
    )
    assert (tmp_path / run.call_args_list[0].args[1].name).exists() is False


def test_activation_reports_workspace_creation_failure(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch.object(engines, "validate_template", return_value=VALIDATED)
    mocker.patch.object(engines.tempfile, "TemporaryDirectory", side_effect=OSError)
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig("pandoc", "soffice", 1.0, 0.5, tmp_path),
        {},
    )
    with pytest.raises(TemplateValidationError) as captured:
        validate_template_for_activation(b"template", DECLARATION, context)
    assert captured.value.code is TemplateValidationErrorCode.ENGINE_FAILURE
