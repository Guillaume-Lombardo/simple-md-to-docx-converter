"""Recovery operations placeholder; T37 exclusively owns its implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import leaf


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable local recovery-operation commands."""
    leaf(subparsers, "backup", command="backup", help_text="Create a local backup.")
    leaf(subparsers, "restore", command="restore", help_text="Restore a local backup.")
