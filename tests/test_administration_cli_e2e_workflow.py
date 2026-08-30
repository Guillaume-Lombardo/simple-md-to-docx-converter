"""Policy checks for the dedicated T35 final-image CLI driver."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _driver_module():
    path = Path("tests/e2e/administration_cli_workflow.py")
    specification = importlib.util.spec_from_file_location(
        "administration_cli_e2e_workflow", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_administration_e2e_covers_public_and_administrator_commands() -> None:
    driver = _driver_module()

    assert driver._health_commands() == (
        ("health", "live", "--url", "http://127.0.0.1:8080"),
        ("health", "ready", "--url", "http://127.0.0.1:8080"),
        ("health", "metrics", "--url", "http://127.0.0.1:8080"),
    )
    assert driver._administrator_commands() == (
        ("users", "list"),
        ("audit", "--limit", "2"),
        ("logout",),
    )


def test_administration_e2e_password_exists_only_at_the_pty_boundary() -> None:
    driver = _driver_module()
    username, password = driver._administrator_login()
    arguments = [
        *driver._exec_prefix("markweave", tty=True),
        "login",
        "--url",
        "http://127.0.0.1:8080",
        "--username",
        username,
    ]

    assert username == "e2e-admin"
    assert password == "e2e-admin-password"  # noqa: S105 - final-image fixture
    assert password not in arguments


def test_administration_e2e_launcher_runs_every_stage(monkeypatch, mocker) -> None:
    driver = _driver_module()
    monkeypatch.setattr(
        sys, "argv", ["administration_cli_workflow.py", "--container", "application"]
    )
    run_pty = mocker.patch.object(driver, "_run_pty", return_value=(0, "Signed in."))
    mocker.patch.object(driver, "_blocked_process_snapshot", return_value="safe")
    run = mocker.patch.object(
        driver.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, stdout="safe", stderr=""),
    )

    assert driver.main() == 0
    assert run.call_count == 6
    assert run_pty.call_count == 1
    login_arguments = run_pty.call_args.args[0]
    assert "e2e-admin-password" not in login_arguments
    assert login_arguments[-2:] == ["--username", "e2e-admin"]
