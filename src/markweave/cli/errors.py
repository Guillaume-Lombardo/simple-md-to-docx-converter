"""Safe, typed failures for command-line behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from markweave.cli.types import ExitCode


@dataclass(frozen=True)
class CliError(Exception):
    """An expected error whose message is safe for terminal output."""

    code: str
    message: str
    exit_code: ExitCode = ExitCode.FAILURE
    details: dict[str, Any] | None = None


def unavailable(command: str) -> CliError:
    """Create the stable failure emitted by an unimplemented command."""

    return CliError(
        code="command_unavailable",
        message=f"The '{command}' command is not available in this release.",
        exit_code=ExitCode.UNAVAILABLE,
    )
