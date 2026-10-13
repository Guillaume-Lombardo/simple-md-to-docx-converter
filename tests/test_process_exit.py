"""The descendant-exit helper decides from one latched ``/proc`` observation (T143)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from tests.process_exit import process_state, wait_for_process_exit

pytestmark = pytest.mark.unit


def _states(
    *states: str | None,
) -> tuple[list[int], Callable[[int], str | None]]:
    calls: list[int] = []
    remaining = list(states)

    def read_state(pid: int) -> str | None:
        calls.append(pid)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return calls, read_state


def test_a_zombie_seen_once_stays_exited_while_it_is_reaped_or_its_pid_reused() -> None:
    # /proc shows Z, then X while the reaper reaps it, then possibly a new
    # process under the reused PID. Re-reading after Z made the T143 run fail.
    calls, read_state = _states("Z", "X", "S")

    assert wait_for_process_exit(4229, 2.0, read_state=read_state)
    assert calls == [4229]


@pytest.mark.parametrize("state", ["Z", "X", None])
def test_zombie_reaping_and_vanished_processes_have_exited(state: str | None) -> None:
    calls, read_state = _states(state)

    assert wait_for_process_exit(17, 2.0, read_state=read_state)
    assert calls == [17]


def test_waits_until_a_killed_process_exits() -> None:
    calls, read_state = _states("R", "D", "Z")

    assert wait_for_process_exit(17, 2.0, read_state=read_state)
    assert calls == [17, 17, 17]


@pytest.mark.parametrize("state", ["R", "S", "D", "T"])
def test_reports_a_process_that_outlives_the_timeout(state: str) -> None:
    calls, read_state = _states(state)

    assert not wait_for_process_exit(17, 0.03, read_state=read_state)
    assert len(calls) >= 2


def test_reads_the_state_after_a_command_name_with_spaces_and_parentheses(
    mocker: MockerFixture,
) -> None:
    read_text = mocker.patch.object(
        Path, "read_text", return_value="42 (odd) name ) Z 1 42 42 0 -1\n"
    )

    assert process_state(42) == "Z"
    read_text.assert_called_once_with(encoding="utf-8")


@pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
def test_a_missing_or_concurrently_reaped_entry_has_no_state(
    mocker: MockerFixture, error: type[OSError]
) -> None:
    mocker.patch.object(Path, "read_text", side_effect=error)

    assert process_state(42) is None


def test_reads_the_state_of_a_live_process() -> None:
    assert process_state(os.getpid()) == "R"
