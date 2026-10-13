"""Simulate a killed command group that takes time to be reaped."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pytest_mock import MockerFixture

from markweave.broker import command_runner
from markweave.broker.command_runner import BoundedCommandRunner, PodmanCommandLimits


def install_slow_reaping(
    mocker: MockerFixture, delay_seconds: float
) -> Callable[[], None]:
    """Delay reaping of every command group the runner kills.

    The double keeps the real ``Popen.wait`` contract: a wait shorter than the
    delay sleeps for its whole timeout and raises ``TimeoutExpired``. The
    returned callable reaps every process left behind by such a refusal.
    """

    killed: set[int] = set()
    unreaped: list[subprocess.Popen[Any]] = []
    real_killpg = os.killpg
    real_wait = subprocess.Popen.wait

    def killpg(pid: int, signal_number: int) -> None:
        killed.add(pid)
        real_killpg(pid, signal_number)

    def wait(process: subprocess.Popen[Any], timeout: float | None = None) -> int:
        if process.pid in killed:
            if timeout is not None and timeout < delay_seconds:
                time.sleep(timeout)
                unreaped.append(process)
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(delay_seconds)
        return real_wait(process, timeout)

    mocker.patch.object(command_runner.os, "killpg", side_effect=killpg)
    mocker.patch.object(subprocess.Popen, "wait", autospec=True, side_effect=wait)

    def reap_all() -> None:
        while unreaped:
            real_wait(unreaped.pop(), 5)

    return reap_all


def budget_exhausting_runner(directory: Path) -> BoundedCommandRunner:
    """Return a runner whose command outlives any dispatch budget."""

    executable = directory / "budget-exhausting-command"
    executable.write_text("#!/bin/sh\nexec sleep 30\n", encoding="ascii")
    executable.chmod(0o700)
    return BoundedCommandRunner(
        executable, PodmanCommandLimits(20), environment={"PATH": "/usr/bin:/bin"}
    )
