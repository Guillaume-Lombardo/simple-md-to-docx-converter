"""Drive T35 commands from the installed final-image CLI."""

from __future__ import annotations

import argparse
import json
import os
import pty
import subprocess
import time
from collections.abc import Callable, Sequence

from tests.e2e.cli_workflow import (
    _assert_secret_free,
    _blocked_process_snapshot,
    _exec_prefix,
    _failure,
    _is_secret_free,
    _read_pty,
)

_SERVICE_URL = "http://127.0.0.1:8080"
_ADMIN_PROFILE = "t35-admin"
_USER_PROFILE = "t35-user"
_USER_NAME = "t35-cli-user"
_INITIAL_PASSWORD = "T35-cli-initial-password"  # noqa: S105 - isolated E2E fixture
_RESET_PASSWORD = "T35-cli-reset-password"  # noqa: S105 - isolated E2E fixture


class _WorkflowFailure(RuntimeError):
    """Carry a bounded stage name without captured output or credentials."""


def _health_commands() -> tuple[tuple[str, ...], ...]:
    return (
        ("health", "live", "--url", _SERVICE_URL),
        ("health", "ready", "--url", _SERVICE_URL),
        ("health", "metrics", "--url", _SERVICE_URL),
    )


def _administrator_commands(user_id: str) -> tuple[tuple[str, ...], ...]:
    return (
        (
            "users",
            "require-password-change",
            user_id,
            "--clear",
            "--force",
            "--profile",
            _ADMIN_PROFILE,
        ),
        (
            "users",
            "deactivate",
            user_id,
            "--force",
            "--profile",
            _ADMIN_PROFILE,
        ),
        (
            "users",
            "activate",
            user_id,
            "--force",
            "--profile",
            _ADMIN_PROFILE,
        ),
    )


def _administrator_login() -> tuple[str, str]:
    """Return the final-image fixture without exporting it to the CLI process."""
    return "e2e-admin", "e2e-admin-password"


def _run_dialog(
    arguments: list[str],
    exchanges: Sequence[tuple[bytes, str]],
    *,
    on_prompt: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """Answer a fixed terminal dialogue without placing answers in process state."""
    master, slave = pty.openpty()
    output = bytearray()
    process: subprocess.Popen[bytes] | None = None
    next_exchange = 0
    search_from = 0
    try:
        process = subprocess.Popen(
            arguments, stdin=slave, stdout=slave, stderr=slave, close_fds=True
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            _read_pty(master, output, timeout=0.1)
            if next_exchange >= len(exchanges):
                continue
            prompt, response = exchanges[next_exchange]
            location = output.find(prompt, search_from)
            if location < 0:
                continue
            if on_prompt is not None:
                on_prompt(response)
            os.write(master, response.encode() + b"\n")
            search_from = location + len(prompt)
            next_exchange += 1
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
            raise _WorkflowFailure("interactive command timeout")
        while _read_pty(master, output, timeout=0):
            pass
        if next_exchange != len(exchanges):
            raise _WorkflowFailure("interactive prompt contract")
        return process.returncode, output.decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if slave >= 0:
            os.close(slave)
        os.close(master)


def _plain(
    prefix: Sequence[str],
    command: Sequence[str],
    *,
    expected: int = 0,
    message: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [*prefix, *command], check=False, capture_output=True, text=True, timeout=20
    )
    combined = result.stdout + result.stderr
    if result.returncode != expected or (
        message is not None and message.casefold() not in combined.casefold()
    ):
        raise _WorkflowFailure(" ".join(command[:3]))
    return result


def _interactive(
    container: str,
    command: Sequence[str],
    exchanges: Sequence[tuple[bytes, str]],
    *,
    inspect_login: bool = False,
) -> str:
    secrets = tuple(response for _prompt, response in exchanges)
    arguments = [*_exec_prefix(container, tty=True), *command]
    if any(secret in arguments for secret in secrets):
        raise _WorkflowFailure("secret in process arguments")

    def inspect(secret: str) -> None:
        if inspect_login:
            _assert_secret_free(_blocked_process_snapshot(container), secret)

    status, output = _run_dialog(arguments, exchanges, on_prompt=inspect)
    if status != 0 or any(not _is_secret_free(output, secret) for secret in secrets):
        raise _WorkflowFailure("interactive command result")
    return output


def _login(container: str, profile: str, username: str, password: str) -> None:
    _interactive(
        container,
        (
            "login",
            "--url",
            _SERVICE_URL,
            "--username",
            username,
            "--profile",
            profile,
        ),
        ((b"Password: ", password),),
        # The shared CLI pilot immediately preceding T35 inspects the blocked
        # login process. This pilot still rejects secrets in argv and output,
        # without racing a second /proc snapshot for each role transition.
        inspect_login=False,
    )


def _exercise(container: str) -> None:
    plain = _exec_prefix(container, tty=False)
    for command in _health_commands():
        _plain(plain, command)

    admin_username, admin_password = _administrator_login()
    _login(container, _ADMIN_PROFILE, admin_username, admin_password)
    policy_result = _plain(
        plain,
        ("--json", "session-policy", "get", "--profile", _ADMIN_PROFILE),
    )
    try:
        policy = json.loads(policy_result.stdout)["session_policy"]
    except (KeyError, TypeError, ValueError) as error:
        raise _WorkflowFailure("session policy metadata") from error
    if (
        not isinstance(policy, dict)
        or not isinstance(policy.get("user_idle_minutes"), int)
        or not isinstance(policy.get("admin_idle_minutes"), int)
        or not isinstance(policy.get("revision"), int)
        or policy.get("absolute_lifetime_seconds") != 28_800
        or policy.get("user_idle_minutes_bounds")
        != {"minimum_minutes": 5, "default_minutes": 30, "maximum_minutes": 300}
        or policy.get("admin_idle_minutes_bounds")
        != {"minimum_minutes": 5, "default_minutes": 15, "maximum_minutes": 60}
        or policy.get("idle_minutes_granularity") != 1
    ):
        raise _WorkflowFailure("session policy metadata")
    human_policy = _plain(plain, ("session-policy", "get", "--profile", _ADMIN_PROFILE))
    expected_policy = (
        f"Users: {policy['user_idle_minutes']} minutes; administrators: "
        f"{policy['admin_idle_minutes']} minutes; absolute lifetime: 28800 seconds; "
        f"revision: {policy['revision']}; user bounds: 5-300 minutes (default 30); "
        "administrator bounds: 5-60 minutes (default 15); granularity: 1 minute.\n"
    )
    if human_policy.stdout != expected_policy:
        raise _WorkflowFailure("session policy human output")
    _interactive(
        container,
        (
            "users",
            "create",
            "--username",
            _USER_NAME,
            "--require-password-change",
            "--force",
            "--profile",
            _ADMIN_PROFILE,
        ),
        (
            (b"New password: ", _INITIAL_PASSWORD),
            (b"Confirm new password: ", _INITIAL_PASSWORD),
        ),
    )
    listing = _plain(plain, ("--json", "users", "list", "--profile", _ADMIN_PROFILE))
    try:
        users = json.loads(listing.stdout)["users"]
        user_id = next(user["id"] for user in users if user["username"] == _USER_NAME)
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise _WorkflowFailure("created user discovery") from error
    if not isinstance(user_id, str):
        raise _WorkflowFailure("created user identifier")

    _login(container, _USER_PROFILE, _USER_NAME, _INITIAL_PASSWORD)
    _plain(
        plain,
        ("users", "list", "--profile", _USER_PROFILE),
        expected=1,
        message="password change is required",
    )
    clear_requirement, deactivate, activate = _administrator_commands(user_id)
    _plain(plain, clear_requirement)
    _login(container, _USER_PROFILE, _USER_NAME, _INITIAL_PASSWORD)
    _plain(
        plain,
        ("users", "list", "--profile", _USER_PROFILE),
        expected=1,
        message="not authorized",
    )

    _interactive(
        container,
        (
            "users",
            "reset-password",
            user_id,
            "--force",
            "--profile",
            _ADMIN_PROFILE,
        ),
        (
            (b"New password: ", _RESET_PASSWORD),
            (b"Confirm new password: ", _RESET_PASSWORD),
        ),
    )
    _plain(
        plain,
        ("whoami", "--profile", _USER_PROFILE),
        expected=1,
        message="authentication is required",
    )
    _login(container, _USER_PROFILE, _USER_NAME, _RESET_PASSWORD)
    _plain(plain, ("whoami", "--profile", _USER_PROFILE))

    _plain(plain, deactivate)
    _plain(
        plain,
        ("whoami", "--profile", _USER_PROFILE),
        expected=1,
        message="authentication is required",
    )
    _plain(plain, activate)
    audit = _plain(
        plain,
        (
            "--json",
            "audit",
            "--offset",
            "1",
            "--limit",
            "2",
            "--profile",
            _ADMIN_PROFILE,
        ),
    )
    try:
        page = json.loads(audit.stdout)
    except (TypeError, ValueError) as error:
        raise _WorkflowFailure("audit pagination response") from error
    if (
        page.get("offset") != 1
        or page.get("limit") != 2
        or len(page.get("items", [])) != 2
    ):
        raise _WorkflowFailure("audit pagination contract")
    _plain(plain, ("logout", "--profile", _ADMIN_PROFILE))


def _expect_readiness_failure(container: str) -> None:
    result = _plain(
        _exec_prefix(container, tty=False),
        ("--json", "health", "ready", "--url", _SERVICE_URL),
        expected=1,
        message="not ready",
    )
    try:
        error = json.loads(result.stderr)["error"]
    except (KeyError, TypeError, ValueError) as exception:
        raise _WorkflowFailure("readiness error envelope") from exception
    if error.get("code") != "not_ready":
        raise _WorkflowFailure("readiness error code")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("exercise", "expect-readiness-failure"),
        default="exercise",
    )
    namespace = parser.parse_args()
    try:
        if namespace.mode == "exercise":
            _exercise(namespace.container)
        else:
            _expect_readiness_failure(namespace.container)
    except (OSError, subprocess.TimeoutExpired, _WorkflowFailure) as error:
        return _failure("administration workflow", str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
