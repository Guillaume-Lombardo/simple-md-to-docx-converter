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
    policy = {
        "user_idle_minutes": 25,
        "admin_idle_minutes": 10,
        "absolute_lifetime_seconds": 28_800,
        "revision": 2,
        "user_idle_minutes_bounds": {
            "minimum_minutes": 5,
            "default_minutes": 30,
            "maximum_minutes": 300,
        },
        "admin_idle_minutes_bounds": {
            "minimum_minutes": 5,
            "default_minutes": 15,
            "maximum_minutes": 60,
        },
        "idle_minutes_granularity": 1,
    }

    def plain(_prefix, command, **_kwargs):
        if tuple(command[:3]) == ("--json", "session-policy", "get"):
            return _completed(json.dumps({"session_policy": policy}))
        if tuple(command[:2]) == ("session-policy", "get"):
            return _completed(
                "Users: 25 minutes; administrators: 10 minutes; absolute lifetime: "
                "28800 seconds; revision: 2; user bounds: 5-300 minutes (default 30); "
                "administrator bounds: 5-60 minutes (default 15); granularity: 1 minute.\n"
            )
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
    assert any(
        command[:3] == ("--json", "session-policy", "get") for command in commands
    )
    assert any(command[:2] == ("session-policy", "get") for command in commands)
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


def test_administration_e2e_modes_are_wired_at_healthy_and_failed_boundaries() -> None:
    runner = Path("scripts/e2e/run.sh").read_text(encoding="utf-8")

    exercise = runner.index(
        'tests.e2e.administration_cli_workflow \\\n  --container "$application_name"\n'
    )
    readiness = runner.index(
        "tests.e2e.administration_cli_workflow \\\n"
        '  --container "$application_name" expect-readiness-failure'
    )
    shared = runner.index("tests.e2e.cli_workflow")
    conversions = runner.index("tests.e2e.conversion_cli_workflow")
    failed_http = runner.index('require_http_status "$base_url/health/ready" 503')
    standalone_failure = runner.index('chmod 000 "$data_directory"')
    distributed_failure = runner.index(
        'podman stop --time 10 "$rustfs_name" >/dev/null'
    )
    standalone_restore = runner.index('chmod 0770 "$data_directory"', readiness)
    distributed_restore = runner.index(
        'podman start "$rustfs_name" >/dev/null', readiness
    )

    assert shared < conversions < exercise < standalone_failure
    assert standalone_failure < failed_http < readiness < standalone_restore
    assert distributed_failure < failed_http < readiness < distributed_restore
    assert runner.count("tests.e2e.administration_cli_workflow") == 2
    assert runner.count("expect-readiness-failure") == 1
