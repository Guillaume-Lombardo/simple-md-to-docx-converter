"""Deterministically delay the real attempt runner for pending-response coverage."""

from importlib import import_module
from time import sleep

if __name__ == "__main__":
    sleep(0.5)
    import_module("markweave.reversions.attempt_worker").main()
