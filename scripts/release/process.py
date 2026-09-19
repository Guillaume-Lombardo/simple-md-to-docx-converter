"""Run bounded release commands and reap their complete Linux process groups."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Lock

from scripts.release.artifacts import ArtifactError

TERMINATION_GRACE_SECONDS = 5.0
GROUP_POLL_SECONDS = 0.02
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
_SUBREAPER_LOCK = Lock()


@contextmanager
def _subreaper() -> Iterator[None]:
    """Own orphan descendants during one serialized Linux release command."""
    with _SUBREAPER_LOCK:
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(previous), 0, 0, 0) != 0:
            raise ArtifactError("cannot inspect release command child reaper")
        if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
            raise ArtifactError("cannot enable release command child reaper")
        try:
            yield
        finally:
            if libc.prctl(PR_SET_CHILD_SUBREAPER, previous.value, 0, 0, 0) != 0:
                raise ArtifactError("cannot restore release command child reaper")


def _reap_group(process_group: int) -> None:
    """Reap only adopted children in this group, never an unrelated command."""
    while True:
        try:
            process_id, _ = os.waitpid(-process_group, os.WNOHANG)
        except ChildProcessError:
            return
        except OSError as error:
            raise ArtifactError("cannot reap release command descendants") from error
        if process_id == 0:
            return


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise ArtifactError("cannot inspect release command process group") from error
    return True


def _wait_group_exit(process: subprocess.Popen[bytes], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        # Popen owns its leader's exit status; waitpid owns only adopted descendants.
        if process.poll() is not None:
            _reap_group(process.pid)
        if not _group_exists(process.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(GROUP_POLL_SECONDS)


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
    if _wait_group_exit(process, timeout=TERMINATION_GRACE_SECONDS):
        return
    _signal_group(process_group, signal.SIGKILL)
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ArtifactError(f"{label} process leader could not be reaped") from error
    if not _wait_group_exit(process, timeout=TERMINATION_GRACE_SECONDS):
        raise ArtifactError(f"{label} process group could not be reaped")


def run_command(
    command: tuple[str, ...], *, cwd: Path, label: str, timeout: float
) -> None:
    """Run fixed argv in a new session with Linux orphan adoption and reaping."""
    with _subreaper():
        _run_command(command, cwd=cwd, label=label, timeout=timeout)


def _run_command(
    command: tuple[str, ...], *, cwd: Path, label: str, timeout: float
) -> None:
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
    _reap_group(process.pid)
    if _group_exists(process.pid):
        _terminate_group(process, label=label)
        if return_code == 0:
            raise ArtifactError(f"{label} left descendant processes running")
    if return_code != 0:
        raise ArtifactError(f"{label} failed with exit code {return_code}")
