"""Typed contracts shared by all Markweave command families."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class ExitCode(IntEnum):
    """Process statuses reserved by the public CLI contract."""

    SUCCESS = 0
    FAILURE = 1
    USAGE = 2
    UNAVAILABLE = 3


class OutputFormat(StrEnum):
    """Supported output encodings."""

    HUMAN = "human"
    JSON = "json"


@dataclass(frozen=True)
class CommandContext:
    """Options supplied consistently to every CLI command."""

    output_format: OutputFormat
    non_interactive: bool
    timeout_seconds: float | None


@dataclass(frozen=True)
class ConnectionProfile:
    """Bounded remote connection state; persistence is owned by T32."""

    name: str
    service_url: str
    session_state: str | None = field(default=None, repr=False)
    csrf_state: str | None = field(default=None, repr=False)
