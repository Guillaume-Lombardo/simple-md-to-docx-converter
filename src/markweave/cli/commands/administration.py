"""Administration, audit, and health placeholders; T35 owns implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import group, leaf


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable administration, audit, and health surface."""
    group(
        subparsers,
        "users",
        help_text="Administer local users.",
        leaves=(
            ("list", "List users."),
            ("create", "Create a user."),
            ("activate", "Activate a user."),
            ("deactivate", "Deactivate a user."),
            ("reset-password", "Reset a user password."),
            ("require-password-change", "Require a user to change their password."),
        ),
    )
    leaf(subparsers, "audit", command="audit", help_text="Inspect audit records.")
    group(
        subparsers,
        "health",
        help_text="Inspect service health.",
        leaves=(
            ("live", "Inspect liveness."),
            ("ready", "Inspect readiness."),
            ("metrics", "Inspect service metrics."),
        ),
    )
