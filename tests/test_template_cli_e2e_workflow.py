"""CI-policy checks for the dedicated final-image template CLI driver."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _driver_module():
    path = Path("tests/e2e/template_cli_workflow.py")
    specification = importlib.util.spec_from_file_location(
        "template_cli_e2e_workflow", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("profile", ("standalone", "distributed"))
def test_template_cli_e2e_uses_profile_specific_admin_state(
    profile: str, mocker, capsys
) -> None:
    driver = _driver_module()
    container = f"markweave-{profile}"
    pty = driver._exec_prefix(container, tty=True)
    plain = driver._exec_prefix(container, tty=False)
    run_pty = mocker.patch.object(driver, "_run_pty", return_value=(1, ""))

    assert driver._template_workflow(container, profile, pty, plain) == 1
    login_arguments = run_pty.call_args.args[0]
    profile_option = login_arguments.index("--profile")
    assert login_arguments[profile_option + 1] == f"template-admin-{profile}"
    assert "XDG_STATE_HOME=/tmp/markweave-cli-state" in login_arguments
    assert plain[-1] == "/opt/md-converter/venv/bin/markweave"
    assert "--tty" not in plain
    assert "template admin login" in capsys.readouterr().err


def test_template_cli_e2e_declares_the_complete_activation_font_set() -> None:
    driver = _driver_module()

    assert driver._TEMPLATE_FONTS == (
        "Aptos",
        "Aptos Display",
        "Calibri",
        "Cambria",
        "Cambria Math",
        "Consolas",
        "Courier New",
        "Times New Roman",
    )


def test_template_cli_e2e_reads_default_and_selected_context() -> None:
    source = Path("tests/e2e/template_cli_workflow.py").read_text(encoding="utf-8")

    assert source.count('["templates", "context", "--profile", owner_profile]') == 3
    assert 'initial.get("preferred_template_id") is not None' in source
    assert 'selected.get("preferred_template_id") != template_id' in source
    assert 'selected.get("system_fallback_template_id") != fallback_id' in source
    assert 'expected_stdout="Template upload limit: 1000000 bytes.\\n"' in source


def test_template_cli_e2e_requires_exact_context_human_output(mocker, capsys) -> None:
    driver = _driver_module()
    expected = "Template upload limit: 1000000 bytes.\n"
    run = mocker.patch.object(
        driver,
        "_run_captured",
        return_value=driver.subprocess.CompletedProcess([], 0, expected, ""),
    )

    assert (
        driver._plain_command(
            ["markweave"],
            ["templates", "context"],
            "context",
            expected_stdout=expected,
        )
        is None
    )
    run.return_value = driver.subprocess.CompletedProcess([], 0, expected.rstrip(), "")
    assert (
        driver._plain_command(
            ["markweave"],
            ["templates", "context"],
            "context",
            expected_stdout=expected,
        )
        == 1
    )
    assert "unexpected output" in capsys.readouterr().err


def test_template_cli_e2e_sends_login_secret_only_through_pty(mocker) -> None:
    driver = _driver_module()
    sentinel = "sentinel-login-secret"
    run_pty = mocker.patch.object(driver, "_run_pty", return_value=(0, ""))

    assert (
        driver._login(
            "markweave-standalone",
            ["podman", "exec", "markweave-standalone", "markweave"],
            username="fixture-owner",
            password=sentinel,
            profile="fixture-profile",
            inspect_process=False,
            stage="fixture login",
        )
        is None
    )
    arguments, prompted_secret = run_pty.call_args.args[:2]
    assert sentinel not in "\0".join(arguments)
    assert "env" not in run_pty.call_args.kwargs
    assert prompted_secret == sentinel


def test_template_cli_e2e_sends_regular_user_credentials_only_over_stdin(
    mocker,
) -> None:
    driver = _driver_module()
    owner_secret = "sentinel-owner-secret"  # noqa: S105 - non-secret test sentinel
    other_secret = "sentinel-other-secret"  # noqa: S105 - non-secret test sentinel
    run = mocker.patch.object(
        driver.subprocess,
        "run",
        return_value=driver.subprocess.CompletedProcess([], 0, "", ""),
    )

    assert (
        driver._setup_users(
            "markweave-standalone",
            "template-admin-standalone",
            (("fixture-owner", owner_secret), ("fixture-other", other_secret)),
        )
        is None
    )
    command = run.call_args.args[0]
    invocation = "\0".join(command)
    payload = run.call_args.kwargs["input"]
    assert owner_secret not in invocation
    assert other_secret not in invocation
    assert "env" not in run.call_args.kwargs
    assert json.loads(payload)["users"] == [
        {"username": "fixture-owner", "password": owner_secret},
        {"username": "fixture-other", "password": other_secret},
    ]
    assert "--interactive" in command
    assert "-c" in command


@pytest.mark.parametrize("wrapper", ("_plain_command", "_json_command"))
def test_template_cli_e2e_turns_command_timeout_into_staged_failure(
    wrapper: str, mocker, capsys
) -> None:
    driver = _driver_module()
    mocker.patch.object(
        driver.subprocess,
        "run",
        side_effect=driver.subprocess.TimeoutExpired(["markweave"], 45),
    )

    command = getattr(driver, wrapper)
    assert command(["markweave"], ["templates", "list"], "list") == 1
    assert "CLI E2E failed at list: exit=124" in capsys.readouterr().err


def test_template_cli_e2e_runs_after_browser_workflow_and_before_restart() -> None:
    runner = Path("scripts/e2e/run.sh").read_text(encoding="utf-8")
    browser = runner.index("/e2e/browser-final-image.test.mjs")
    templates = runner.index("tests.e2e.template_cli_workflow")
    restart = runner.index('podman restart --time 15 "$application_name"')

    assert browser < templates < restart
    assert runner.count("tests.e2e.template_cli_workflow") == 1
