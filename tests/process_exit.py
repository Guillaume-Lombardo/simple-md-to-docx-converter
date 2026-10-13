"""Observe whether a process has exited without trusting a later reuse of its PID."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# A zombie (Z) has exited and awaits its reaper; a task in EXIT_DEAD (X) is being
# reaped right now. Neither can execute code again. /proc reports X briefly after
# Z, so a check that treats X as running sees a dead process come back to life.
EXITED_STATES = frozenset({"Z", "X"})


def process_state(pid: int) -> str | None:
    """Return the ``/proc`` state letter of ``pid``, or ``None`` once it is gone."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError, ProcessLookupError:
        # Reading the entry of a process reaped during the read yields ESRCH.
        return None
    # The command name is parenthesized and may itself contain spaces or ")".
    return stat.rpartition(")")[2].split()[0]


def wait_for_process_exit(
    pid: int,
    timeout_seconds: float,
    *,
    read_state: Callable[[int], str | None] = process_state,
) -> bool:
    """Return whether ``pid`` was observed to exit within ``timeout_seconds``.

    The first exited observation is final and ends the wait. The process can
    never run again after it, so a live entry seen later under the same PID can
    only be a new process that reused the PID.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = read_state(pid)
        if state is None or state in EXITED_STATES:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
