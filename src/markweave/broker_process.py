"""Dependency-light console boundary for the external isolation broker."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from importlib import import_module
from typing import Protocol, cast


class _BrokerMain(Protocol):
    def __call__(self, argv: Sequence[str] | None = None) -> int: ...


def main(argv: Sequence[str] | None = None) -> int:
    """Validate invocation before loading the reverse-attempt dependency surface."""

    arguments = tuple(argv if argv is not None else sys.argv[1:])
    if len(arguments) != 1:
        os.write(2, b"broker configuration failed\n")
        return 2
    try:
        run_broker = cast(_BrokerMain, import_module("markweave.broker.process").main)
        return run_broker(arguments)
    except BaseException:
        os.write(2, b"broker runtime failed\n")
        return 1


if __name__ == "__main__":  # pragma: no cover - console script boundary
    raise SystemExit(main())
