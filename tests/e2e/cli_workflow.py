"""Drive the installed final-image CLI through a real pseudo-terminal."""

from __future__ import annotations

import argparse
import errno
import os
import pty
import select
import subprocess
import time
from collections.abc import Callable


def _run_pty(
    arguments: list[str], password: str, *, on_prompt: Callable[[], None] | None = None
) -> tuple[int, str]:
    """Send a fixture password after inspection, drain output, and always reap."""
    master, slave = pty.openpty()
    output = bytearray()
    sent = False
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            arguments, stdin=slave, stdout=slave, stderr=slave, close_fds=True
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            _read_pty(master, output, timeout=0.1)
            if not sent and b"Password: " in output:
                if on_prompt is not None:
                    on_prompt()
                os.write(master, password.encode() + b"\n")
                sent = True
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            raise RuntimeError(
                "CLI password prompt did not complete before its deadline."
            )
        while _read_pty(master, output, timeout=0):
            pass
        return process.returncode, output.decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if slave >= 0:
            os.close(slave)
        os.close(master)


def _read_pty(master: int, output: bytearray, *, timeout: float) -> bool:
    """Drain a pseudo-terminal, treating Linux EIO as its ordinary hangup signal."""
    readable, _, _ = select.select([master], [], [], timeout)
    if not readable:
        return False
    try:
        chunk = os.read(master, 4096)
    except OSError as error:
        if error.errno == errno.EIO:
            return False
        raise
    if not chunk:
        return False
    output.extend(chunk)
    return True


def _blocked_process_snapshot(container: str) -> str:
    """Read only the currently blocked CLI process arguments and environment."""
    result = subprocess.run(
        [
            "podman",
            "exec",
            container,
            "/bin/sh",
            "-c",
            "for process in /proc/[0-9]*; do "
            "command=$(tr '\\000' '\\n' <\"$process/cmdline\" 2>/dev/null || true); "
            "case $command in *'/opt/md-converter/venv/bin/markweave'*login*) "
            "printf '%s\\n' \"$command\"; "
            "tr '\\000' '\\n' <\"$process/environ\" 2>/dev/null || true ;; esac; "
            "done",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError("Could not inspect the blocked CLI process.")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    namespace = parser.parse_args()
    prefix = [
        "podman",
        "exec",
        "--interactive",
        "--tty",
        "--env",
        "XDG_STATE_HOME=/tmp/markweave-cli-state",
        namespace.container,
        "/usr/bin/env",
        "-i",
        "XDG_STATE_HOME=/tmp/markweave-cli-state",
        "/opt/md-converter/venv/bin/markweave",
    ]
    password = "e2e-admin-password"  # noqa: S105 - final-image fixture credential
    try:
        login, output = _run_pty(
            [
                *prefix,
                "login",
                "--url",
                "http://127.0.0.1:8080",
                "--username",
                "e2e-admin",
            ],
            password,
            on_prompt=lambda: _assert_secret_free(
                _blocked_process_snapshot(namespace.container), password
            ),
        )
    except OSError, RuntimeError, subprocess.TimeoutExpired:
        return 1
    if login != 0 or not _is_secret_free(output, password):
        return 1
    for command in (("whoami",), ("logout",)):
        result = subprocess.run(
            [*prefix, *command], check=False, capture_output=True, text=True
        )
        if result.returncode != 0 or not _is_secret_free(
            result.stdout + result.stderr, password
        ):
            return 1
    non_interactive = subprocess.run(
        [*prefix, "--non-interactive", "password", "change"],
        check=False,
        capture_output=True,
        text=True,
    )
    if (
        non_interactive.returncode != 1
        or "interactive input" not in non_interactive.stderr
        or not _is_secret_free(
            non_interactive.stdout + non_interactive.stderr, password
        )
    ):
        return 1
    logs = subprocess.run(
        ["podman", "logs", "--tail", "200", namespace.container],
        check=False,
        capture_output=True,
        text=True,
    )
    return (
        0
        if logs.returncode == 0 and _is_secret_free(logs.stdout + logs.stderr, password)
        else 1
    )


def _assert_secret_free(value: str, secret: str) -> None:
    """Fail immediately when a prompted value reaches a process-visible boundary."""
    if not _is_secret_free(value, secret):
        raise RuntimeError("CLI secret reached process arguments or environment.")


def _is_secret_free(value: str, secret: str) -> bool:
    """Keep test failures non-disclosing while checking all captured boundaries."""
    return secret not in value


if __name__ == "__main__":
    raise SystemExit(main())
