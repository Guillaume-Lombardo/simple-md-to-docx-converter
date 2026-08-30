"""Policy checks for the dedicated T35 final-image CLI driver."""

from __future__ import annotations

import importlib.util
import json
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


def _completed(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def test_administration_e2e_declares_public_and_primary_mutation_commands() -> None:
    driver = _driver_module()
    user_id = "00000000-0000-0000-0000-000000000001"

    assert driver._health_commands() == (
        ("health", "live", "--url", "http://127.0.0.1:8080"),
        ("health", "ready", "--url", "http://127.0.0.1:8080"),
        ("health", "metrics", "--url", "http://127.0.0.1:8080"),
    )
    mutations = driver._administrator_commands(user_id)
    assert {command[1] for command in mutations} == {
        "require-password-change",
        "deactivate",
        "activate",
    }
    assert all(user_id in command and "--force" in command for command in mutations)


def test_administration_e2e_passwords_exist_only_at_the_pty_boundary(mocker) -> None:
    driver = _driver_module()
    username, password = driver._administrator_login()
    run_dialog = mocker.patch.object(driver, "_run_dialog", return_value=(0, "safe"))
    mocker.patch.object(driver, "_blocked_process_snapshot", return_value="safe")

    driver._login("markweave", "admin", username, password)

    arguments, exchanges = run_dialog.call_args.args
    assert username == "e2e-admin"
    assert password == "e2e-admin-password"  # noqa: S105 - final-image fixture
    assert password not in arguments
    assert exchanges == ((b"Password: ", password),)


def test_administration_e2e_exercises_authz_revocation_and_pagination(mocker) -> None:
    driver = _driver_module()
    user_id = "00000000-0000-0000-0000-000000000002"
    listing = json.dumps({"users": [{"id": user_id, "username": driver._USER_NAME}]})
    audit = json.dumps({"items": [{}, {}], "offset": 1, "limit": 2})

    def plain(_prefix, command, **_kwargs):
        if tuple(command[:3]) == ("--json", "users", "list"):
            return _completed(listing)
        if tuple(command[:3]) == ("--json", "audit", "--offset"):
            return _completed(audit)
        return _completed()

    plain_mock = mocker.patch.object(driver, "_plain", side_effect=plain)
    login = mocker.patch.object(driver, "_login")
    interactive = mocker.patch.object(driver, "_interactive", return_value="safe")

    driver._exercise("application")

    commands = [tuple(call.args[1]) for call in plain_mock.call_args_list]
    assert ("users", "list", "--profile", driver._USER_PROFILE) in commands
    assert commands.count(("whoami", "--profile", driver._USER_PROFILE)) == 3
    assert any(command[:3] == ("--json", "audit", "--offset") for command in commands)
    assert any(command[1:2] == ("deactivate",) for command in commands)
    assert any(command[1:2] == ("activate",) for command in commands)
    assert login.call_count == 4
    assert interactive.call_count == 2
    assert any("create" in call.args[1] for call in interactive.call_args_list)
    assert any("reset-password" in call.args[1] for call in interactive.call_args_list)

    restricted = next(
        call
        for call in plain_mock.call_args_list
        if call.args[1] == ("users", "list", "--profile", driver._USER_PROFILE)
        and call.kwargs.get("message") == "password change is required"
    )
    unauthorized = next(
        call
        for call in plain_mock.call_args_list
        if call.args[1] == ("users", "list", "--profile", driver._USER_PROFILE)
        and call.kwargs.get("message") == "not authorized"
    )
    assert restricted.kwargs["expected"] == unauthorized.kwargs["expected"] == 1


def test_administration_e2e_has_a_strict_readiness_failure_mode(mocker) -> None:
    driver = _driver_module()
    plain = mocker.patch.object(
        driver,
        "_plain",
        return_value=_completed(
            stderr=json.dumps({"error": {"code": "not_ready", "message": "Not ready."}})
        ),
    )

    driver._expect_readiness_failure("application")

    assert plain.call_args.kwargs == {"expected": 1, "message": "not ready"}
    assert plain.call_args.args[1] == (
        "--json",
        "health",
        "ready",
        "--url",
        "http://127.0.0.1:8080",
    )


def test_administration_e2e_launcher_dispatches_modes(monkeypatch, mocker) -> None:
    driver = _driver_module()
    exercise = mocker.patch.object(driver, "_exercise")
    readiness = mocker.patch.object(driver, "_expect_readiness_failure")

    monkeypatch.setattr(
        sys, "argv", ["administration_cli_workflow.py", "--container", "application"]
    )
    assert driver.main() == 0
    exercise.assert_called_once_with("application")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "administration_cli_workflow.py",
            "--container",
            "application",
            "expect-readiness-failure",
        ],
    )
    assert driver.main() == 0
    readiness.assert_called_once_with("application")
