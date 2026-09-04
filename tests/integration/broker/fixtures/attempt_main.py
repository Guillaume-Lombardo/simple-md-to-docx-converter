"""Controlled signal-resistant process tree for Podman lifecycle integration."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path


def _wait() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)


Path("/work/ready").write_text("ready", encoding="ascii")
if os.fork() == 0:
    _wait()
_wait()
