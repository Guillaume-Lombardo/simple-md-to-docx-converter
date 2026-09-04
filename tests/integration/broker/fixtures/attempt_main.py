"""Controlled signal-resistant process tree for Podman lifecycle integration."""

from __future__ import annotations

import os
import signal
import time


def _wait() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)


if os.fork() == 0:
    _wait()
_wait()
