"""Conversion and job command family placeholder; T33 owns its implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import group, leaf


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable conversion and job surface."""
    leaf(subparsers, "convert", command="convert", help_text="Submit a conversion.")
    group(
        subparsers,
        "jobs",
        help_text="Inspect and manage conversion jobs.",
        leaves=(
            ("list", "List conversion jobs."),
            ("show", "Show one conversion job."),
            ("wait", "Wait for one conversion job."),
            ("cancel", "Cancel one conversion job."),
            ("download", "Download a conversion result."),
            ("manifest", "Download a conversion manifest."),
        ),
    )
