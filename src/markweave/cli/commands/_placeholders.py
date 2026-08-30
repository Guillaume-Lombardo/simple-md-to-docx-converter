"""Small parser helpers for command families pending implementation."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from typing import Any

from markweave.cli.errors import unavailable


def unavailable_command(
    parser: argparse.ArgumentParser, command: str, *, help_text: str
) -> None:
    """Register an initially unavailable leaf command."""
    del help_text
    parser.set_defaults(command_name=command, command_handler=_raise_unavailable)


def leaf(
    subparsers: Any,
    name: str,
    *,
    command: str,
    help_text: str,
) -> argparse.ArgumentParser:
    """Add a stable leaf with the common unavailable handler."""
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    unavailable_command(parser, command, help_text=help_text)
    return parser


def group(
    subparsers: Any,
    name: str,
    *,
    help_text: str,
    leaves: Iterable[tuple[str, str]],
) -> None:
    """Add a stable subgroup and its initially unavailable leaves."""
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    children = parser.add_subparsers(dest=f"{name}_command", metavar="COMMAND")
    for leaf_name, leaf_help in leaves:
        leaf(children, leaf_name, command=f"{name} {leaf_name}", help_text=leaf_help)


def _raise_unavailable(context: object, writer: object, command: str) -> None:
    del context, writer
    raise unavailable(command)
