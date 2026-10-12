"""Bound broker runtime work by the remaining transport dispatch budget."""

from __future__ import annotations

import stat
import time
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from markweave.broker import operation_deadline, podman_cgroups
from markweave.broker.operation_deadline import (
    bounded_seconds,
    dispatch_budget,
    operation_budget,
    remaining_seconds,
)
from markweave.broker.podman_runtime import (
    BoundedCommandRunner,
    CgroupEmptinessWait,
    PodmanCommandLimits,
    PodmanRuntimeError,
)

pytestmark = pytest.mark.unit


def test_dispatch_budget_reserves_a_tenth_of_the_operation_timeout() -> None:
    assert dispatch_budget(100.0, 5.0) == pytest.approx(99.5)
    assert operation_deadline.RESPONSE_RESERVE_FRACTION == 0.1


def test_budget_is_scoped_to_the_dispatch_and_restored_after_it() -> None:
    assert remaining_seconds() is None
    assert bounded_seconds(20.0) == 20.0
    with operation_budget(time.monotonic() + 10):
        assert 9 < (remaining_seconds() or 0) <= 10
        with operation_budget(time.monotonic() + 1):
            assert bounded_seconds(20.0) <= 1
        assert 9 < bounded_seconds(20.0) <= 10
        assert bounded_seconds(0.5) == 0.5
    assert remaining_seconds() is None


def test_command_runner_refuses_to_start_without_remaining_budget(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    popen = mocker.patch("markweave.broker.command_runner.subprocess.Popen")
    runner = BoundedCommandRunner(
        tmp_path / "unused", PodmanCommandLimits(20), environment={}
    )

    with (
        operation_budget(time.monotonic() - 0.1),
        pytest.raises(PodmanRuntimeError, match="bounds"),
    ):
        runner(("fixed",))

    popen.assert_not_called()


def test_command_runner_clamps_its_timeout_to_the_remaining_budget(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "slow"
    executable.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    runner = BoundedCommandRunner(
        executable, PodmanCommandLimits(20), environment={"PATH": "/usr/bin:/bin"}
    )
    started = time.monotonic()

    with (
        operation_budget(started + 0.3),
        pytest.raises(PodmanRuntimeError, match="bounds"),
    ):
        runner(("fixed",))

    assert time.monotonic() - started < 5


def test_emptiness_wait_with_a_spent_budget_never_pauses() -> None:
    pauses: list[float] = []

    with pytest.raises(PodmanRuntimeError, match="cgroup is not empty"):
        podman_cgroups._await_unpopulated(
            lambda: b"populated 1\nfrozen 0\n",
            CgroupEmptinessWait(5.0, 0.05),
            time.monotonic,
            pauses.append,
            -0.1,
        )
    assert pauses == []
    assert podman_cgroups._await_unpopulated(
        lambda: b"populated 0\nfrozen 0\n",
        CgroupEmptinessWait(5.0, 0.05),
        time.monotonic,
        pauses.append,
        -0.1,
    ) == {"frozen": 0, "populated": 0}
