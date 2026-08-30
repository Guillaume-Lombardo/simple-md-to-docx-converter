"""Runtime operations placeholder; T36 exclusively owns its implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import leaf


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable local runtime-operation commands."""
    leaf(subparsers, "serve", command="serve", help_text="Run the local HTTP service.")
    leaf(subparsers, "worker", command="worker", help_text="Run a local worker.")
    leaf(subparsers, "doctor", command="doctor", help_text="Check local prerequisites.")
    leaf(
        subparsers, "migrate", command="migrate", help_text="Apply database migrations."
    )
