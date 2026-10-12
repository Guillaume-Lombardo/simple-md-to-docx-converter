"""Unit checks for the bounded cgroup event poll used by real Podman tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from tests.integration.broker.cgroup_events import await_cgroup_events

EMPTY = "populated 0\nfrozen 0\n"
POPULATED = "populated 1\nfrozen 0\n"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _reader(
    values: Iterator[str], clock: FakeClock, reads: list[float]
) -> Callable[[], str]:
    def read() -> str:
        reads.append(clock.now)
        return next(values)

    return read


@pytest.mark.unit
def test_returns_immediately_when_the_first_read_matches() -> None:
    clock = FakeClock()
    reads: list[float] = []

    observed = await_cgroup_events(
        _reader(iter((EMPTY,)), clock, reads),
        EMPTY,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert observed == EMPTY
    assert reads == [0.0]
    assert clock.sleeps == []


@pytest.mark.unit
def test_waits_for_a_transiently_populated_slice() -> None:
    clock = FakeClock()
    reads: list[float] = []

    observed = await_cgroup_events(
        _reader(iter((POPULATED, POPULATED, EMPTY)), clock, reads),
        EMPTY,
        timeout_seconds=1.0,
        poll_interval_seconds=0.25,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert observed == EMPTY
    assert reads == [0.0, 0.25, 0.5]
    assert clock.sleeps == [0.25, 0.25]


@pytest.mark.unit
def test_returns_the_persistent_mismatch_once_the_bound_expires() -> None:
    clock = FakeClock()
    reads: list[float] = []

    def persistent() -> str:
        reads.append(clock.now)
        return POPULATED

    observed = await_cgroup_events(
        persistent,
        EMPTY,
        timeout_seconds=1.0,
        poll_interval_seconds=0.375,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert observed == POPULATED
    assert observed != EMPTY
    assert reads == [0.0, 0.375, 0.75, 1.0]
    assert clock.sleeps == [0.375, 0.375, 0.25]
    assert clock.now == 1.0


@pytest.mark.unit
def test_a_persistent_mismatch_fails_the_callers_assertion() -> None:
    clock = FakeClock()

    with pytest.raises(AssertionError):
        assert (
            await_cgroup_events(
                lambda: POPULATED,
                EMPTY,
                timeout_seconds=0.5,
                poll_interval_seconds=0.25,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            == EMPTY
        )
    assert clock.now == 0.5


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timeout_seconds", "poll_interval_seconds"),
    [(0, 0.1), (-1, 0.1), (1, 0), (1, -0.1), (1, 2)],
)
def test_rejects_unbounded_or_invalid_polling(
    timeout_seconds: float, poll_interval_seconds: float
) -> None:
    with pytest.raises(ValueError, match="bounds are invalid"):
        await_cgroup_events(
            lambda: EMPTY,
            EMPTY,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
