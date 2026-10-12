"""Absolute per-dispatch budget shared by the broker transports and runtime.

Both transports stop the broker when one dispatch outlives its operation
deadline. Each dispatch therefore publishes an absolute monotonic budget that
ends before that deadline, reserving time to encode and send the response and to
reap a cancelled command. Runtime commands and observation loops clamp
themselves to the remaining budget, so a slow operation is refused through the
ordinary error path instead of tripping the fatal dispatch watchdog. Like the
Kubernetes attester client, which bounds each exchange by
``min(request timeout, remaining readiness time)``, every bound here is the
smaller of its own policy limit and the time remaining.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Final

# Fraction of the transport operation timeout kept for the response and cleanup.
RESPONSE_RESERVE_FRACTION: Final = 0.1

_BUDGET: ContextVar[float | None] = ContextVar(
    "markweave_broker_operation_budget", default=None
)


def dispatch_budget(deadline: float, operation_timeout_seconds: float) -> float:
    """Return the absolute budget that ends before a dispatch deadline."""

    return deadline - operation_timeout_seconds * RESPONSE_RESERVE_FRACTION


@contextmanager
def operation_budget(budget: float) -> Generator[None]:
    """Bound every runtime step of the current dispatch by an absolute budget."""

    token = _BUDGET.set(budget)
    try:
        yield
    finally:
        _BUDGET.reset(token)


def remaining_seconds() -> float | None:
    """Return the time left in the current dispatch, or ``None`` outside one."""

    budget = _BUDGET.get()
    return None if budget is None else budget - monotonic()


def bounded_seconds(limit: float) -> float:
    """Return ``limit`` clamped to the remaining dispatch budget."""

    remaining = remaining_seconds()
    return limit if remaining is None else min(limit, remaining)
