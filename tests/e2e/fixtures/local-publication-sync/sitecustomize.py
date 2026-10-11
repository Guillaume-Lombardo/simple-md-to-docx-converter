"""Test-only publication synchronization for the T106 final-image E2E.

This file is never packaged or installed. The E2E script mounts it read-only and
puts it on ``PYTHONPATH`` for a single container, so production images and
installations cannot load it. Document engines run under ``python -I -S`` and are
unaffected. It only observes and pauses at publication steps; it never changes
their outcome.
"""

import fcntl
import os
import time
from pathlib import Path

_SYNC = Path("/work/sync")
_MODES = frozenset(
    filter(None, os.environ.get("MARKWEAVE_E2E_PUBLICATION_SYNC", "").split(","))
)


def _mark(name: str) -> None:
    (_SYNC / name).write_text("ready\n", encoding="utf-8")


def _wait_for(name: str) -> None:
    deadline = time.monotonic() + 120
    while not (_SYNC / name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(name)
        time.sleep(0.05)


if _MODES & {"pause-after-first-replacement", "pause-before-restore"}:
    _replace = os.replace
    _paused: set[str] = set()

    def _synchronized_replace(source, target, *arguments, **options):
        restoring = str(source).endswith(".previous")
        if restoring and "pause-before-restore" in _MODES and "restore" not in _paused:
            _paused.add("restore")
            _mark("restore-started")
            _wait_for("continue")
        result = _replace(source, target, *arguments, **options)
        if (
            not restoring
            and "pause-after-first-replacement" in _MODES
            and "replacement" not in _paused
        ):
            _paused.add("replacement")
            _mark("replaced")
            _wait_for("continue")
        return result

    setattr(os, "replace", _synchronized_replace)  # noqa: B010

if "report-lock-wait" in _MODES:
    _flock = fcntl.flock

    def _reporting_flock(descriptor, operation):
        try:
            return _flock(descriptor, operation)
        except BlockingIOError:
            if not (_SYNC / "lock-wait").exists():
                _mark("lock-wait")
            raise

    setattr(fcntl, "flock", _reporting_flock)  # noqa: B010
