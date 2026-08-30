"""Drive T35 commands from the installed final-image CLI."""

from __future__ import annotations

import argparse
import subprocess

from tests.e2e.cli_workflow import (
    _assert_secret_free,
    _blocked_process_snapshot,
    _exec_prefix,
    _failure,
    _is_secret_free,
    _run_pty,
)


def _health_commands() -> tuple[tuple[str, ...], ...]:
    return (
        ("health", "live", "--url", "http://127.0.0.1:8080"),
        ("health", "ready", "--url", "http://127.0.0.1:8080"),
        ("health", "metrics", "--url", "http://127.0.0.1:8080"),
    )


def _administrator_commands() -> tuple[tuple[str, ...], ...]:
    return (("users", "list"), ("audit", "--limit", "2"), ("logout",))


def _administrator_login() -> tuple[str, str]:
    """Return the final-image fixture without exporting it to the CLI process."""
    return "e2e-admin", "e2e-admin-password"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    namespace = parser.parse_args()
    plain_prefix = _exec_prefix(namespace.container, tty=False)
    for command in _health_commands():
        result = subprocess.run(
            [*plain_prefix, *command], check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            return _failure("health command", f"exit={result.returncode}")

    username, password = _administrator_login()
    try:
        login, output = _run_pty(
            [
                *_exec_prefix(namespace.container, tty=True),
                "login",
                "--url",
                "http://127.0.0.1:8080",
                "--username",
                username,
            ],
            password,
            on_prompt=lambda: _assert_secret_free(
                _blocked_process_snapshot(namespace.container), password
            ),
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return _failure("administrator login execution", type(error).__name__)
    if login != 0 or not _is_secret_free(output, password):
        return _failure(
            "administrator login result",
            f"exit={login}; secret_free={_is_secret_free(output, password)}",
        )
    for command in _administrator_commands():
        result = subprocess.run(
            [*plain_prefix, *command], check=False, capture_output=True, text=True
        )
        if result.returncode != 0 or not _is_secret_free(
            result.stdout + result.stderr, password
        ):
            return _failure(
                "administrator command",
                f"exit={result.returncode}; secret_free={_is_secret_free(result.stdout + result.stderr, password)}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
