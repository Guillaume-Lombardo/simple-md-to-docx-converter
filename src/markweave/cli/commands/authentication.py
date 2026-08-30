"""Authentication command family placeholder; T32 owns its implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import group, leaf


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable authentication surface."""
    leaf(subparsers, "login", command="login", help_text="Sign in to a remote service.")
    leaf(
        subparsers,
        "logout",
        command="logout",
        help_text="Sign out of a remote service.",
    )
    leaf(
        subparsers,
        "whoami",
        command="whoami",
        help_text="Show the active remote session.",
    )
    group(
        subparsers,
        "password",
        help_text="Manage the current account password.",
        leaves=(("change", "Change the current account password."),),
    )
