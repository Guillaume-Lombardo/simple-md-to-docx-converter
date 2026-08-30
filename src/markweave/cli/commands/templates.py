"""Template command family placeholder; T34 owns its implementation."""

from __future__ import annotations

import argparse

from markweave.cli.commands._placeholders import group


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable template and preference surface."""
    group(
        subparsers,
        "templates",
        help_text="Discover and manage document templates.",
        leaves=(
            ("list", "List visible templates."),
            ("search", "Search visible templates."),
            ("show", "Show one template."),
            ("create", "Create a template."),
            ("download", "Download a template."),
            ("update", "Update template metadata."),
            ("replace", "Replace template content."),
            ("archive", "Archive a template."),
            ("delete", "Delete a template."),
            ("versions", "List template versions."),
            ("version-download", "Download a template version."),
            ("restore", "Restore a template version."),
            ("preferred", "Manage the preferred template."),
            ("fallback", "Manage the administrator fallback template."),
        ),
    )
