"""Run bounded release commands and reap their complete Linux process groups."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from scripts.release.artifacts import ArtifactError

TERMINATION_GRACE_SECONDS = 5.0
GROUP_POLL_SECONDS = 0.02


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise ArtifactError("cannot inspect release command process group") from error
    return True


def _wait_group_exit(process_group: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while _group_exists(process_group):
        if time.monotonic() >= deadline:
            return False
        time.sleep(GROUP_POLL_SECONDS)
    return True


def _signal_group(process_group: int, requested_signal: signal.Signals) -> None:
    try:
        os.killpg(process_group, requested_signal)
    except ProcessLookupError:
        return
    except OSError as error:
        raise ArtifactError("cannot terminate release command process group") from error


def _terminate_group(process: subprocess.Popen[bytes], *, label: str) -> None:
    process_group = process.pid
    _signal_group(process_group, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    if _wait_group_exit(process_group, timeout=TERMINATION_GRACE_SECONDS):
        return
    _signal_group(process_group, signal.SIGKILL)
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ArtifactError(f"{label} process leader could not be reaped") from error
    if not _wait_group_exit(process_group, timeout=TERMINATION_GRACE_SECONDS):
        raise ArtifactError(f"{label} process group could not be reaped")


def run_command(
    command: tuple[str, ...], *, cwd: Path, label: str, timeout: float
) -> None:
    """Run argv in a new session, enforcing a deadline for the whole group."""
    try:
        process = subprocess.Popen(  # noqa: S603 - argv only, no shell interpretation
            command,
            cwd=cwd,
            start_new_session=True,
        )
    except OSError as error:
        raise ArtifactError(f"{label} failed to start") from error
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        _terminate_group(process, label=label)
        raise ArtifactError(f"{label} timed out") from error
    if _group_exists(process.pid):
        _terminate_group(process, label=label)
        if return_code == 0:
            raise ArtifactError(f"{label} left descendant processes running")
    if return_code != 0:
        raise ArtifactError(f"{label} failed with exit code {return_code}")
