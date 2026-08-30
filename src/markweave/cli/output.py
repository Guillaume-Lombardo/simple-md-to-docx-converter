"""Output rendering with a strict stdout/stderr split."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any, TextIO

from markweave.cli.errors import CliError
from markweave.cli.types import OutputFormat


class OutputWriter:
    """Write successful results to stdout and failures to stderr."""

    def __init__(
        self,
        output_format: OutputFormat,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self._output_format = output_format
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr

    def success(self, human: str, payload: Mapping[str, Any] | None = None) -> None:
        """Write one complete successful response."""
        if self._output_format is OutputFormat.JSON:
            self._write_json(self._stdout, payload if payload is not None else {})
        else:
            self._stdout.write(f"{human}\n")

    def error(self, error: CliError) -> None:
        """Write one safe error envelope without a traceback."""
        if self._output_format is OutputFormat.JSON:
            payload: dict[str, dict[str, Any]] = {
                "error": {"code": error.code, "message": error.message}
            }
            if error.details is not None:
                payload["error"]["details"] = error.details
            self._write_json(self._stderr, payload)
        else:
            self._stderr.write(f"error: {error.message}\n")

    @staticmethod
    def _write_json(stream: TextIO, payload: Mapping[str, Any]) -> None:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
