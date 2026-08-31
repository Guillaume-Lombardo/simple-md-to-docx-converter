"""Root command registry and stable process contract for ``markweave``."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable, Sequence

from markweave.cli.commands import (
    administration,
    authentication,
    conversions,
    recovery,
    runtime,
    templates,
)
from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext, ExitCode, OutputFormat
from markweave.version import VERSION

CommandHandler = Callable[[CommandContext, OutputWriter, str], None]


def build_parser() -> argparse.ArgumentParser:
    """Build the immutable root registry without importing application backends."""
    parser = argparse.ArgumentParser(
        prog="markweave",
        description="Command-line interface for Markweave.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"markweave {VERSION}",
        help="Show version.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Write machine-readable output."
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Fail instead of prompting for input.",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_timeout,
        metavar="SECONDS",
        help="Bound this command's network or operational wait.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    authentication.register(subparsers)
    conversions.register(subparsers)
    templates.register(subparsers)
    administration.register(subparsers)
    runtime.register(subparsers)
    recovery.register(subparsers)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one CLI command and return its documented process status."""
    parser = build_parser()
    namespace = parser.parse_args(arguments)
    output_format = OutputFormat.JSON if namespace.json else OutputFormat.HUMAN
    writer = OutputWriter(output_format)
    if not hasattr(namespace, "command_handler"):
        parser.print_help(sys.stderr)
        return ExitCode.USAGE
    context = CommandContext(
        output_format=output_format,
        non_interactive=namespace.non_interactive,
        timeout_seconds=namespace.timeout,
    )
    handler: CommandHandler = namespace.command_handler
    try:
        handler(context, writer, namespace.command_name)
    except CliError as error:
        writer.error(error)
        return error.exit_code
    except KeyboardInterrupt:
        writer.error(
            CliError("interrupted", "Command interrupted.", exit_code=ExitCode.FAILURE)
        )
        return ExitCode.FAILURE
    except Exception:
        writer.error(
            CliError("internal_error", "An internal error occurred.", ExitCode.FAILURE)
        )
        return ExitCode.FAILURE
    return ExitCode.SUCCESS


def _positive_timeout(value: str) -> float:
    """Reject zero, negative, and non-finite timeout values at the boundary."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from error
    if parsed <= 0 or not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return parsed
