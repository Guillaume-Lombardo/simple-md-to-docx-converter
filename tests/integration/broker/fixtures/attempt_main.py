"""Controlled signal-resistant process tree for Podman lifecycle integration."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path


def _wait() -> None:
    while True:
        time.sleep(1)


def main() -> None:
    """Start a signal-resistant tree, then publish readiness from its parent."""

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if os.fork() == 0:
        _wait()
        return
    Path("/work/ready").write_text("ready", encoding="ascii")
    _wait()


if __name__ == "__main__":
    main()
