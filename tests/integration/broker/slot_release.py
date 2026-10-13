"""Widen the window between a broker handler's work and its slot release."""

from __future__ import annotations

from threading import BoundedSemaphore
from time import sleep
from typing import Any

from pytest_mock import MockerFixture

# Long enough that a client reconnecting right after its exchange completes always
# lands inside the window when a server releases a slot after completing the
# exchange, and short enough to keep a few sequential exchanges well within their
# operation deadlines.
SLOT_RELEASE_DELAY_SECONDS = 0.3


def delay_slot_releases(
    mocker: MockerFixture,
    semaphore: BoundedSemaphore,
    delay_seconds: float = SLOT_RELEASE_DELAY_SECONDS,
) -> Any:
    """Make every release of one server slot sleep before it frees the slot."""

    original = semaphore.release

    def delayed(n: int = 1) -> None:
        sleep(delay_seconds)
        original(n)

    return mocker.patch.object(semaphore, "release", side_effect=delayed)
