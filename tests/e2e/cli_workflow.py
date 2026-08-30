"""Drive the installed final-image CLI through a real pseudo-terminal."""

from __future__ import annotations

import argparse
import os
import pty
import select
import subprocess
import time


def _run_pty(arguments: list[str], password: str) -> tuple[int, str]:
    """Send the fixture password only after the non-echoing prompt appears."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        arguments, stdin=slave, stdout=slave, stderr=slave, close_fds=True
    )
    os.close(slave)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        readable, _, _ = select.select([master], [], [], 0.1)
        if readable:
            chunk = os.read(master, 4096)
            if not chunk:
                break
            output.extend(chunk)
            if not sent and b"Password: " in output:
                os.write(master, password.encode() + b"\n")
                sent = True
        if process.poll() is not None:
            break
    os.close(master)
    return process.wait(timeout=5), output.decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    namespace = parser.parse_args()
    prefix = [
        "podman",
        "exec",
        "--tty",
        "--env",
        "XDG_STATE_HOME=/tmp/markweave-cli-state",
        namespace.container,
        "/opt/md-converter/venv/bin/markweave",
    ]
    login, output = _run_pty(
        [*prefix, "login", "--url", "http://127.0.0.1:8080", "--username", "e2e-admin"],
        "e2e-admin-password",
    )
    if login != 0 or "e2e-admin-password" in output:
        return 1
    for command in (("whoami",), ("logout",)):
        result = subprocess.run(
            [*prefix, *command], check=False, capture_output=True, text=True
        )
        if (
            result.returncode != 0
            or "e2e-admin-password" in result.stdout + result.stderr
        ):
            return 1
    non_interactive = subprocess.run(
        [*prefix, "--non-interactive", "password", "change"],
        check=False,
        capture_output=True,
        text=True,
    )
    return (
        0
        if non_interactive.returncode == 1
        and "interactive input" in non_interactive.stderr
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
