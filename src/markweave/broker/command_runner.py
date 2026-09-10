"""Bounded local command execution shared by isolation inspectors."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

_INSPECT_MAX_BYTES = 64 * 1024
_MAX_COMMAND_OUTPUT_BYTES = 128 * 1024
_MIN_OUTPUT_BYTES = 1024


class PodmanRuntimeError(RuntimeError):
    """Content-free Podman lifecycle failure."""


@dataclass(frozen=True, slots=True)
class PodmanCommandLimits:
    """Broker-owned ceilings for every fixed Podman CLI operation."""

    operation_seconds: float
    output_bytes: int = _INSPECT_MAX_BYTES

    def __post_init__(self) -> None:
        if (
            type(self.operation_seconds) not in {int, float}
            or not math.isfinite(self.operation_seconds)
            or self.operation_seconds <= 0
            or type(self.output_bytes) is not int
            or not _MIN_OUTPUT_BYTES <= self.output_bytes <= _MAX_COMMAND_OUTPUT_BYTES
        ):
            raise ValueError("Podman command limits are invalid")


class BoundedCommandRunner:
    """Run fixed argv without a shell under absolute time and output ceilings."""

    def __init__(
        self,
        executable: Path,
        limits: PodmanCommandLimits,
        *,
        environment: Mapping[str, str],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(executable, Path)
            or not executable.is_absolute()
            or type(limits) is not PodmanCommandLimits
            or not isinstance(environment, Mapping)
            or any(
                type(key) is not str
                or type(value) is not str
                or not key
                or "=" in key
                or "\x00" in key
                or "\x00" in value
                for key, value in environment.items()
            )
            or "CONTAINER_HOST" in environment
        ):
            raise ValueError("Podman command runner configuration is invalid")
        self._executable = executable
        self._limits = limits
        self._environment = dict(environment)
        self._monotonic = monotonic

    def __call__(  # noqa: PLR0912,PLR0915 - cleanup branches enforce process bounds
        self,
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        input_bytes: bytes | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if (
            not arguments
            or any(type(argument) is not str or not argument for argument in arguments)
            or not accepted_exit_codes
            or any(type(code) is not int for code in accepted_exit_codes)
            or (input_bytes is not None and type(input_bytes) is not bytes)
        ):
            raise PodmanRuntimeError("Podman command contract failed")
        ceiling = (
            self._limits.output_bytes if max_output_bytes is None else max_output_bytes
        )
        if type(ceiling) is not int or ceiling < 0:
            raise PodmanRuntimeError("Podman command contract failed")
        deadline = self._monotonic() + self._limits.operation_seconds
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is broker-authored
                (str(self._executable), *arguments),
                stdin=subprocess.PIPE
                if input_bytes is not None
                else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=self._environment,
            )
        except OSError as error:
            raise PodmanRuntimeError("Podman command failed") from error
        if (
            process.stdout is None
            or process.stderr is None
            or (input_bytes is not None and process.stdin is None)
        ):
            self._terminate(process)
            raise PodmanRuntimeError("Podman command failed")
        output = bytearray()
        selector: selectors.BaseSelector | None = None
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            input_view = memoryview(input_bytes) if input_bytes is not None else None
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, None)
            while selector.get_map():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise TimeoutError
                ready = selector.select(remaining)
                if not ready:
                    raise TimeoutError
                for key, _ in ready:
                    if key.data is None:
                        if input_view:
                            try:
                                written = os.write(key.fd, input_view[:8192])
                            except BlockingIOError:
                                continue
                            if written <= 0:
                                raise OSError
                            input_view = input_view[written:]
                        if not input_view:
                            selector.unregister(key.fileobj)
                            if process.stdin is not None:
                                process.stdin.close()
                        continue
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data:
                        output.extend(chunk)
                        if len(output) > ceiling:
                            raise OverflowError
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError
            return_code = process.wait(timeout=remaining)
        except BaseException as error:
            self._terminate(process)
            if isinstance(
                error, (OverflowError, subprocess.TimeoutExpired, TimeoutError)
            ):
                raise PodmanRuntimeError(
                    "Podman command exceeded its bounds"
                ) from error
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise PodmanRuntimeError("Podman command failed") from error
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()
            process.stderr.close()
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        if return_code not in accepted_exit_codes:
            raise PodmanRuntimeError("Podman command failed")
        return return_code, bytes(output)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise PodmanRuntimeError("Podman command cleanup failed") from error
