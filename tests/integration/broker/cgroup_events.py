"""Bounded polling of cgroup-v2 event files for the real Podman broker tests."""

from __future__ import annotations

import time
from collections.abc import Callable


def await_cgroup_events(  # noqa: PLR0913 - injected clock keeps the bound testable
    read: Callable[[], str],
    expected: str,
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.02,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Re-read until ``expected`` is observed or the bound expires.

    The kernel clears a unit slice's ``populated`` flag only after every member,
    including Podman's conmon monitor and its cleanup command, has exited. The
    caller asserts on the returned last observation, so a persistent mismatch
    still fails once the bound expires. The first read is returned as observed;
    a later read counts only when both its pause and the read itself finished
    within the bound, so a delayed pause or read cannot pass after the bound.
    """

    if timeout_seconds <= 0 or not 0 < poll_interval_seconds <= timeout_seconds:
        raise ValueError("cgroup event polling bounds are invalid")
    deadline = monotonic() + timeout_seconds
    observed = read()
    while observed != expected:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(poll_interval_seconds, remaining))
        if deadline - monotonic() <= 0:
            break
        candidate = read()
        if deadline - monotonic() <= 0:
            break
        observed = candidate
    return observed
