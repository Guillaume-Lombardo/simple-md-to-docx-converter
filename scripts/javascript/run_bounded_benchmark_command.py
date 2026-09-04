#!/usr/bin/env python3
"""Run one Linux CI command under a deadline and reap its complete process tree."""

from __future__ import annotations

import ctypes
import os
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

PR_SET_CHILD_SUBREAPER = 36
TREE_VERIFICATION_SECONDS = 2.0
POLL_SECONDS = 0.05
MIN_ARGUMENTS = 6
ARGUMENT_ERRORS = (TypeError, ValueError)
PROC_DIRECTORY_ERRORS = (FileNotFoundError, PermissionError)
PROC_CHILDREN_ERRORS = (FileNotFoundError, PermissionError, ProcessLookupError)


class _ForwardedSignal(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError
    return parsed


def _enable_subreaper() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _direct_children(process_id: int) -> set[int]:
    children: set[int] = set()
    try:
        task_directories = tuple(Path(f"/proc/{process_id}/task").iterdir())
    except PROC_DIRECTORY_ERRORS:
        return children
    for task_directory in task_directories:
        try:
            text = (task_directory / "children").read_text(encoding="ascii")
        except PROC_CHILDREN_ERRORS:
            continue
        children.update(int(value) for value in text.split())
    return children


def _descendants(process_id: int) -> set[int]:
    descendants: set[int] = set()
    pending = list(_direct_children(process_id))
    while pending:
        child = pending.pop()
        if child in descendants:
            continue
        descendants.add(child)
        pending.extend(_direct_children(child))
    return descendants


def _reap_adopted_children(direct_process_id: int) -> None:
    for child in _direct_children(os.getpid()):
        if child == direct_process_id:
            continue
        with suppress(ChildProcessError):
            os.waitpid(child, os.WNOHANG)


def _signal_processes(process_ids: set[int], signum: signal.Signals) -> None:
    for process_id in process_ids:
        with suppress(ProcessLookupError):
            os.kill(process_id, signum)


def _wait_for_tree(
    process: subprocess.Popen[bytes], signum: signal.Signals, seconds: float
) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        process.poll()
        _reap_adopted_children(process.pid)
        descendants = _descendants(os.getpid())
        if not descendants:
            return True
        _signal_processes(descendants, signum)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(POLL_SECONDS, remaining))


def _terminate_tree(process: subprocess.Popen[bytes], grace_seconds: int) -> bool:
    if _wait_for_tree(process, signal.SIGTERM, float(grace_seconds)):
        return True
    return _wait_for_tree(process, signal.SIGKILL, TREE_VERIFICATION_SECONDS)


def _shell_status(return_code: int) -> int:
    if return_code < 0:
        return 128 + abs(return_code)
    return return_code


def _record_failure(log, log_path: str, message: str) -> None:
    print(message, file=log, flush=True)
    if log_path not in {"/dev/stderr", "/dev/stdout"}:
        print(message, file=sys.stderr, flush=True)


def _parse_arguments(arguments: list[str]) -> tuple[int, int, str, str, list[str]]:
    if len(arguments) < MIN_ARGUMENTS or arguments[4] != "--":
        raise ValueError
    timeout_seconds = _positive_integer(arguments[0])
    grace_seconds = _positive_integer(arguments[1])
    log_path, label = arguments[2:4]
    command = arguments[5:]
    if not log_path or not label or not command:
        raise ValueError
    return timeout_seconds, grace_seconds, log_path, label, command


def main(arguments: list[str] | None = None) -> int:
    try:
        timeout_seconds, grace_seconds, log_path, label, command = _parse_arguments(
            list(sys.argv[1:] if arguments is None else arguments)
        )
    except ARGUMENT_ERRORS:
        print(
            "Usage: run_bounded_benchmark_command.py TIMEOUT_SECONDS "
            "GRACE_SECONDS LOG LABEL -- COMMAND [ARGUMENT ...]",
            file=sys.stderr,
        )
        return 2

    _enable_subreaper()
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(log_path).open("a", encoding="utf-8") as log:
        print(
            f"boundary_start label={shlex.quote(label)} "
            f"timeout_seconds={timeout_seconds} grace_seconds={grace_seconds} "
            f"command={shlex.join(command)}",
            file=log,
            flush=True,
        )
        process = subprocess.Popen(  # noqa: S603 - exact reviewed CI command
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        def forward(signum: int, _frame: object) -> None:
            raise _ForwardedSignal(signum)

        cleanup_in_progress = False

        def forward_once(signum: int, _frame: object) -> None:
            if cleanup_in_progress:
                return
            forward(signum, _frame)

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, forward_once)
        deadline_reached = False
        forwarded_signal: int | None = None
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            deadline_reached = True
        except _ForwardedSignal as caught:
            forwarded_signal = caught.signum
        finally:
            cleanup_in_progress = True

        cleanup_complete = _terminate_tree(process, grace_seconds)
        if not cleanup_complete:
            _record_failure(
                log,
                log_path,
                f"boundary_cleanup_failed label={shlex.quote(label)} "
                f"direct_process={process.pid} status=125",
            )
            return 125
        if forwarded_signal is not None:
            status = 128 + forwarded_signal
            _record_failure(
                log,
                log_path,
                f"boundary_signal label={shlex.quote(label)} "
                f"deadline_reached=false signal={forwarded_signal} status={status}",
            )
            return status
        status = _shell_status(process.returncode)
        if deadline_reached:
            _record_failure(
                log,
                log_path,
                f"boundary_timeout label={shlex.quote(label)} "
                f"timeout_seconds={timeout_seconds} grace_seconds={grace_seconds} "
                f"deadline_reached=true observed_status={status} normalized_status=124",
            )
            return 124
        if status != 0:
            _record_failure(
                log,
                log_path,
                f"boundary_exit label={shlex.quote(label)} "
                f"deadline_reached=false status={status}",
            )
            return status
        print(
            f"boundary_complete label={shlex.quote(label)} "
            "deadline_reached=false status=0",
            file=log,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
