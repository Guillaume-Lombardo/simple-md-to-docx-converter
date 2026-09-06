"""Deterministic scheduling coverage for mixed conversion workers."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.jobs.runner import WorkerSchedule
from markweave.jobs.worker import ConversionWorker
from markweave.reversion_jobs.runner import (
    FairWorkerLoop,
    ReversionSchedule,
    StopSignalBridge,
)
from markweave.reversion_jobs.worker import ReversionWorker

pytestmark = pytest.mark.unit


class _StopAfter:
    def __init__(self, events: list[str], count: int) -> None:
        self.events = events
        self.count = count
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return len(self.events) >= self.count

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return True


class _StopAfterChecks(_StopAfter):
    def __init__(self, checks: int) -> None:
        super().__init__([], 1)
        self._remaining = checks

    def is_set(self) -> bool:
        self._remaining -= 1
        return self._remaining < 0


def _loop(
    mocker: MockerFixture, events: list[str]
) -> tuple[FairWorkerLoop, Any, Any, StopSignalBridge]:
    forward = mocker.Mock(spec=ConversionWorker)
    reverse = mocker.Mock(spec=ReversionWorker)
    forward.recover.return_value = 0
    reverse.recover.return_value = 0
    forward.cleanup.return_value = 0
    reverse.cleanup.return_value = 0
    forward.run_once.side_effect = lambda **_kwargs: events.append("forward") or True
    reverse.run_once.side_effect = lambda: events.append("reverse") or True
    bridge = StopSignalBridge()
    clock_value = 0.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.01
        return clock_value

    loop = FairWorkerLoop(
        cast(ConversionWorker, forward),
        cast(ReversionWorker, reverse),
        WorkerSchedule(0.1, 100, 5, 2),
        ReversionSchedule(100, 3),
        stop_bridge=bridge,
        monotonic_clock=clock,
    )
    return loop, forward, reverse, bridge


def test_successful_jobs_alternate_strictly_and_start_with_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, _forward, _reverse, bridge = _loop(mocker, events)
    stop = _StopAfter(events, 4)

    loop.run(stop)

    assert events == ["forward", "reverse", "forward", "reverse"]
    assert bridge.is_set()


def test_empty_preferred_family_falls_back_immediately(mocker: MockerFixture) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    forward.run_once.side_effect = lambda **_kwargs: (
        events.append("forward-empty") or False
    )
    reverse.run_once.side_effect = lambda: events.append("reverse") or True

    loop.run(_StopAfter(events, 2))

    assert events == ["forward-empty", "reverse"]


def test_reverse_broker_failure_does_not_block_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    forward.run_once.side_effect = [False, True]
    reverse.run_once.side_effect = BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE)
    stop = _StopAfterChecks(2)
    loop.run(stop)

    assert forward.run_once.call_count >= 1
    reverse.run_once.assert_called_once_with()


def test_reverse_recovery_failure_does_not_block_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    reverse.recover.side_effect = BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE)

    loop.run(_StopAfter(events, 1))

    forward.run_once.assert_called_once()
    reverse.run_once.assert_not_called()


def test_stop_bridge_is_inert_until_bound_and_rejects_rebinding() -> None:
    bridge = StopSignalBridge()
    first = _StopAfter([], 1)
    second = _StopAfter([], 1)

    assert not bridge.is_set()
    assert not bridge.wait(0.01)
    bridge.bind(first)
    bridge.bind(first)
    with pytest.raises(RuntimeError, match="already bound"):
        bridge.bind(second)


@pytest.mark.parametrize("values", [(0, 1), (1, 0), (float("inf"), 1), (True, 1)])
def test_reverse_schedule_requires_positive_finite_values(
    values: tuple[float, float],
) -> None:
    with pytest.raises(ValueError, match="positive"):
        ReversionSchedule(*values)
