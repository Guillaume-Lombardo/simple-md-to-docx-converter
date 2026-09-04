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
PROC_STAT_START_TIME_INDEX = 19


class _ProcInspectionError(RuntimeError):
    """The supervisor cannot prove the identity or ancestry of a process."""


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


def _direct_children(process_id: int, *, required: bool = False) -> set[int]:
    children: set[int] = set()
    try:
        task_directories = tuple(Path(f"/proc/{process_id}/task").iterdir())
    except FileNotFoundError as error:
        if required:
            raise _ProcInspectionError("required process tree disappeared") from error
        return children
    except PermissionError as error:
        raise _ProcInspectionError("process tree is not readable") from error
    readable_tasks = 0
    for task_directory in task_directories:
        try:
            text = (task_directory / "children").read_text(encoding="ascii")
        except FileNotFoundError:
            continue
        except (PermissionError, ProcessLookupError) as error:
            raise _ProcInspectionError("process children are not readable") from error
        readable_tasks += 1
        children.update(int(value) for value in text.split())
    if required and readable_tasks == 0:
        raise _ProcInspectionError("required process tree has no readable task")
    return children


def _descendants(process_id: int) -> dict[int, int]:
    descendants: dict[int, int] = {}
    pending = list(_direct_children(process_id, required=True))
    while pending:
        child = pending.pop()
        if child in descendants:
            continue
        start_time = _process_start_time(child)
        if start_time is None:
            continue
        descendants[child] = start_time
        pending.extend(_direct_children(child))
    return descendants


def _reap_adopted_children(direct_process_id: int) -> None:
    for child in _direct_children(os.getpid(), required=True):
        if child == direct_process_id:
            continue
        with suppress(ChildProcessError):
            os.waitpid(child, os.WNOHANG)


def _process_start_time(process_id: int) -> int | None:
    try:
        stat = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except (PermissionError, ProcessLookupError) as error:
        raise _ProcInspectionError("process identity is not readable") from error
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise _ProcInspectionError("process identity is malformed")
    fields_after_name = stat[closing_parenthesis + 2 :].split()
    if len(fields_after_name) <= PROC_STAT_START_TIME_INDEX:
        raise _ProcInspectionError("process identity is malformed")
    try:
        return int(fields_after_name[PROC_STAT_START_TIME_INDEX])
    except ValueError as error:
        raise _ProcInspectionError("process identity is malformed") from error


def _signal_process(
    process_id: int, expected_start_time: int, signum: signal.Signals
) -> None:
    before = _process_start_time(process_id)
    if before is None:
        return
    if before != expected_start_time:
        return
    try:
        process_fd = os.pidfd_open(process_id)
    except ProcessLookupError:
        return
    except OSError as error:
        raise _ProcInspectionError("process identity cannot be opened") from error
    try:
        after = _process_start_time(process_id)
        if after is None:
            return
        if expected_start_time != after:
            raise _ProcInspectionError("process identity changed while opening pidfd")
        try:
            signal.pidfd_send_signal(process_fd, signum)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise _ProcInspectionError("process cannot be signalled") from error
    finally:
        os.close(process_fd)


def _signal_processes(processes: dict[int, int], signum: signal.Signals) -> None:
    for process_id, start_time in processes.items():
        _signal_process(process_id, start_time, signum)


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
    try:
        if _wait_for_tree(process, signal.SIGTERM, float(grace_seconds)):
            return True
        return _wait_for_tree(process, signal.SIGKILL, TREE_VERIFICATION_SECONDS)
    except _ProcInspectionError:
        with suppress(_ProcInspectionError):
            start_time = _process_start_time(process.pid)
            if start_time is not None:
                _signal_process(process.pid, start_time, signal.SIGKILL)
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=TREE_VERIFICATION_SECONDS)
        return False


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
        received_signal: int | None = None

        def remember_first_signal(signum: int, _frame: object) -> None:
            nonlocal received_signal
            if received_signal is None:
                received_signal = signum

        previous_handlers = {
            signum: signal.signal(signum, remember_first_signal)
            for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        }

        try:
            process = subprocess.Popen(  # noqa: S603 - exact reviewed CI command
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            for signum, previous in previous_handlers.items():
                signal.signal(signum, previous)
            raise
        deadline_reached = False
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None and received_signal is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                deadline_reached = True
                break
            time.sleep(min(POLL_SECONDS, remaining))

        cleanup_complete = _terminate_tree(process, grace_seconds)
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        if not cleanup_complete:
            _record_failure(
                log,
                log_path,
                f"boundary_cleanup_failed label={shlex.quote(label)} "
                f"direct_process={process.pid} status=125",
            )
            return 125
        if received_signal is not None:
            status = 128 + received_signal
            _record_failure(
                log,
                log_path,
                f"boundary_signal label={shlex.quote(label)} "
                f"deadline_reached=false signal={received_signal} status={status}",
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
