"""Isolate tests of ``scripts.testing.services`` from the session's own stack.

The PostgreSQL and S3 suites start a session-scoped local service stack in the
same process, before the lifecycle tests run in a serial session. Its owner
lock and bookkeeping live in the module-level tables these tests exercise, so
a test that clears those tables closes the live stack's ``flock`` mid-session,
records fake command groups in its lock file, and leaves that file behind
after the session's teardown.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass

from scripts.testing import services


@dataclass(frozen=True)
class _Bookkeeping:
    """The ownership tables of ``services`` at one moment."""

    held: dict[str, services._HeldLock]
    owned: dict[str, dict[str, str]]
    unsettled: dict[int, subprocess.Popen[str]]
    creation_attempted: set[str]


def _set_aside() -> _Bookkeeping:
    """Empty the ownership tables in place and return what they held."""
    saved = _Bookkeeping(
        dict(services._HELD),
        dict(services._OWNED),
        dict(services._UNSETTLED),
        set(services._CREATION_ATTEMPTED),
    )
    services._HELD.clear()
    services._OWNED.clear()
    services._UNSETTLED.clear()
    services._CREATION_ATTEMPTED.clear()
    return saved


def _restore(saved: _Bookkeeping) -> None:
    services._HELD.update(saved.held)
    services._OWNED.update(saved.owned)
    services._UNSETTLED.update(saved.unsettled)
    services._CREATION_ATTEMPTED.update(saved.creation_attempted)


@contextmanager
def isolated_service_bookkeeping() -> Generator[None]:
    """Run a block against empty ownership tables, then put the outer ones back.

    Every owner lock the block left held is closed and the block's entries are
    dropped; the outer entries, such as a live session stack's, come back
    untouched. Callers stop the block's command groups first when they own
    real processes.
    """
    outer = _set_aside()
    try:
        yield
    finally:
        try:
            for held in services._HELD.values():
                os.close(held.descriptor)
        finally:
            _set_aside()
            _restore(outer)
