"""Exercise conversion commands inside an exact rootless final image."""

from __future__ import annotations

import argparse
import errno
import json
import os
import pty
import select
import subprocess
import sys
import time
from uuid import UUID

_STATE_HOME = "/tmp/markweave-t33-cli-state"  # noqa: S108 - container tmpfs
_SOURCE = "/tmp/markweave-t33-source.md"  # noqa: S108 - container tmpfs
_RESULT = "/tmp/markweave-t33-result.zip"  # noqa: S108 - container tmpfs
_MANIFEST = "/tmp/markweave-t33-manifest.json"  # noqa: S108 - container tmpfs


def main() -> int:  # noqa: PLR0911, PLR0912 - bounded stage-specific E2E driver
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    arguments = parser.parse_args()
    prefix = _exec_prefix(arguments.container, tty=False)
    username = "e2e-admin"
    password = "e2e-admin-password"  # noqa: S105 - final-image fixture only
    setup = _inside(
        arguments.container,
        "from pathlib import Path; "
        f"Path({_SOURCE!r}).write_text('# CLI final image\\n', encoding='utf-8')",
    )
    if setup.returncode != 0:
        return _failure("source setup", setup.returncode)
    login, login_output = _run_pty(
        [
            *_exec_prefix(arguments.container, tty=True),
            "login",
            "--url",
            "http://127.0.0.1:8080",
            "--username",
            username,
        ],
        password,
    )
    if login != 0 or password in login_output:
        return _failure("login", login)
    key = f"t33-final-image-{arguments.profile}"
    submission = _run(
        [
            *prefix,
            "--json",
            "convert",
            _SOURCE,
            "--output",
            "both",
            "--idempotency-key",
            key,
        ]
    )
    job = _json_result(submission)
    if job is None or job.get("template_mode") != "pandoc-default":
        return _failure("submission", submission.returncode)
    try:
        job_id = str(UUID(str(job["id"])))
    except KeyError, ValueError:
        return _failure("submission identity", submission.returncode)
    replay = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "convert",
                _SOURCE,
                "--output",
                "both",
                "--idempotency-key",
                key,
            ]
        )
    )
    if replay is None or replay.get("id") != job_id:
        return _failure("idempotent replay", 1)
    listing = _json_result(_run([*prefix, "--json", "jobs", "list", "--limit", "10"]))
    items = listing.get("items") if listing is not None else None
    if not isinstance(items, list) or not any(
        item.get("id") == job_id for item in items if isinstance(item, dict)
    ):
        return _failure("job list", 1)
    shown = _json_result(_run([*prefix, "--json", "jobs", "show", job_id]))
    if shown is None or shown.get("id") != job_id:
        return _failure("job show", 1)
    waited = _json_result(
        _run(
            [
                *prefix,
                "--json",
                "--timeout",
                "180",
                "jobs",
                "wait",
                job_id,
                "--poll-interval",
                "1",
            ],
            timeout=190,
        )
    )
    if waited is None or waited.get("state") != "succeeded":
        return _failure("job wait", 1)
    for command, destination in (("download", _RESULT), ("manifest", _MANIFEST)):
        result = _json_result(
            _run([*prefix, "--json", "jobs", command, job_id, destination])
        )
        if result is None or result.get("status") != "downloaded":
            return _failure(f"{command} result", 1)
    refused = _run([*prefix, "jobs", "download", job_id, _RESULT])
    if refused.returncode != 1 or "already exists" not in refused.stderr:
        return _failure("download clobber refusal", refused.returncode)
    overwritten = _run([*prefix, "jobs", "download", job_id, _RESULT, "--overwrite"])
    if overwritten.returncode != 0:
        return _failure("download overwrite", overwritten.returncode)
    validation = _inside(
        arguments.container,
        "import json; from pathlib import Path; "
        f"assert Path({_RESULT!r}).read_bytes().startswith(b'PK'); "
        f"assert isinstance(json.loads(Path({_MANIFEST!r}).read_text()), dict)",
    )
    if validation.returncode != 0:
        return _failure("download validation", validation.returncode)
    logout = _run([*prefix, "logout"])
    if logout.returncode != 0:
        return _failure("logout", logout.returncode)
    cleanup = _inside(
        arguments.container,
        "import shutil; from pathlib import Path; "
        f"shutil.rmtree({_STATE_HOME!r}, ignore_errors=True); "
        f"[Path(path).unlink(missing_ok=True) for path in {[_SOURCE, _RESULT, _MANIFEST]!r}]",
    )
    if cleanup.returncode != 0:
        return _failure("cleanup", cleanup.returncode)
    print(f"Conversion CLI final-image E2E passed for {arguments.profile}.")
    return 0


def _run(
    arguments: list[str], *, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments, check=False, capture_output=True, text=True, timeout=timeout
    )


def _inside(container: str, code: str) -> subprocess.CompletedProcess[str]:
    return _run(
        [
            "podman",
            "exec",
            container,
            "/opt/md-converter/venv/bin/python",
            "-c",
            code,
        ]
    )


def _json_result(result: subprocess.CompletedProcess[str]) -> dict[str, object] | None:
    if result.returncode != 0 or result.stderr:
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _run_pty(arguments: list[str], password: str) -> tuple[int, str]:
    master, slave = pty.openpty()
    output = bytearray()
    process: subprocess.Popen[bytes] | None = None
    sent = False
    try:
        process = subprocess.Popen(
            arguments, stdin=slave, stdout=slave, stderr=slave, close_fds=True
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if (
                _read_pty(master, output, timeout=0.1)
                and not sent
                and b"Password: " in output
            ):
                os.write(master, password.encode() + b"\n")
                sent = True
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
            return 1, "deadline"
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


def _exec_prefix(container: str, *, tty: bool) -> list[str]:
    prefix = [
        "podman",
        "exec",
        "--env",
        f"XDG_STATE_HOME={_STATE_HOME}",
        container,
        "/usr/bin/env",
        "-i",
        f"XDG_STATE_HOME={_STATE_HOME}",
        "/opt/md-converter/venv/bin/markweave",
    ]
    if tty:
        prefix[2:2] = ["--interactive", "--tty"]
    return prefix


def _failure(stage: str, exit_code: int) -> int:
    print(f"Conversion CLI E2E failed at {stage}: exit={exit_code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
