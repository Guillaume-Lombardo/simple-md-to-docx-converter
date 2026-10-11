"""Bounded, isolated document-engine version probes against the approved pins."""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from markweave.conversion.engine_launcher import isolated_engine_command
from markweave.conversion.libreoffice import (
    _terminate_group as terminate_process_group,
)
from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS

_MAX_VERSION_OUTPUT_BYTES = 4096
_VERSION_PATTERNS = {
    "pandoc": re.compile(r"pandoc(?:\.exe)? ([0-9][0-9A-Za-z.+-]*)"),
    "libreoffice": re.compile(r"LibreOffice ([0-9][0-9.]*)(?: .*)?"),
    "mermaid-cli": re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+[0-9A-Za-z.+-]*)"),
    "chromium": re.compile(
        r"(?:Google Chrome|Chromium)(?: for Testing)? ([0-9][0-9.]*)(?: .*)?"
    ),
}
_DISPLAY_NAMES = {
    "pandoc": "Pandoc",
    "libreoffice": "LibreOffice",
    "mermaid-cli": "Mermaid CLI",
    "chromium": "Chromium",
}
ENGINE_NAMES = tuple(_VERSION_PATTERNS)


def approved_engine_versions() -> dict[str, str]:
    """Return the qualified engine pins shared with service admission and workers."""

    approved = dict(COMPONENT_VERSIONS)
    return {name: approved[name] for name in ENGINE_NAMES}


def engine_display_name(name: str) -> str:
    """Return the stable human name of one known engine."""

    return _DISPLAY_NAMES[name]


class EngineVersionStatus(StrEnum):
    """Comparison of one observed engine with its approved pin."""

    PINNED = "pinned"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EngineObservation:
    """One required engine's approved and actually observed version."""

    name: str
    approved_version: str
    actual_version: str | None
    status: EngineVersionStatus

    @property
    def pinned(self) -> bool:
        return self.status is EngineVersionStatus.PINNED

    def as_manifest(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "approved_version": self.approved_version,
            "actual_version": self.actual_version,
            "status": self.status.value,
        }


class EngineProbeError(RuntimeError):
    """A required engine is missing, cannot run, timed out, or was cancelled."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(reason)
        self.name = name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class EngineProbeConfig:
    """Explicit probe bounds reused from the configured engine deadlines."""

    timeout_seconds: float
    termination_grace_seconds: float
    poll_seconds: float
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        for value in (
            self.timeout_seconds,
            self.termination_grace_seconds,
            self.poll_seconds,
        ):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Engine probe deadlines must be finite and positive")


def parse_engine_version(name: str, output: str) -> str | None:
    """Extract one version from the first non-empty ``--version`` output line."""

    line = next((value.strip() for value in output.splitlines() if value.strip()), "")
    match = _VERSION_PATTERNS[name].fullmatch(" ".join(line.split()))
    return match.group(1) if match is not None else None


class EngineVersionProbe:
    """Run ``ENGINE --version`` with fixed arguments inside the engine isolation."""

    def __init__(
        self, config: EngineProbeConfig, host_environment: Mapping[str, str]
    ) -> None:
        self._config = config
        self._host_environment = dict(host_environment)

    def observe(
        self,
        name: str,
        executable: str,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> EngineObservation:
        """Return the observed version or raise ``EngineProbeError``."""

        approved = approved_engine_versions()[name]
        output = self._run(
            name,
            executable,
            deadline_monotonic=deadline_monotonic,
            cancellation_requested=cancellation_requested or (lambda: False),
        )
        actual = parse_engine_version(name, output)
        if actual is None:
            status = EngineVersionStatus.UNKNOWN
        elif actual == approved:
            status = EngineVersionStatus.PINNED
        else:
            status = EngineVersionStatus.DIFFERENT
        return EngineObservation(name, approved, actual, status)

    def _run(
        self,
        name: str,
        executable: str,
        *,
        deadline_monotonic: float | None,
        cancellation_requested: Callable[[], bool],
    ) -> str:
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="md-converter-probe-", dir=self._config.workspace_root
            )
        except OSError:
            raise EngineProbeError(name, "unavailable") from None
        with temporary:
            workspace = Path(temporary.name)
            try:
                for directory in ("home", "tmp", "cache", "config", "data", "runtime"):
                    (workspace / directory).mkdir(mode=0o700)
                output_path = workspace / "version.txt"
                with output_path.open("xb") as output:
                    process = subprocess.Popen(  # noqa: S603 - fixed shell-free argv
                        isolated_engine_command([executable, "--version"]),
                        cwd=workspace,
                        env=self._environment(workspace),
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        start_new_session=True,
                        close_fds=True,
                    )
            except OSError:
                raise EngineProbeError(name, "unavailable") from None
            return_code = self._wait(
                name,
                process,
                deadline_monotonic=deadline_monotonic,
                cancellation_requested=cancellation_requested,
            )
            if return_code != 0:
                raise EngineProbeError(name, "unavailable")
            try:
                with output_path.open("rb") as stream:
                    content = stream.read(_MAX_VERSION_OUTPUT_BYTES)
            except OSError:
                raise EngineProbeError(name, "unavailable") from None
        return content.decode("utf-8", errors="replace")

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
        if "PATH" in self._host_environment:
            environment["PATH"] = self._host_environment["PATH"]
        environment.update(
            {
                "HOME": str(workspace / "home"),
                "TMPDIR": str(workspace / "tmp"),
                "XDG_CACHE_HOME": str(workspace / "cache"),
                "XDG_CONFIG_HOME": str(workspace / "config"),
                "XDG_DATA_HOME": str(workspace / "data"),
                "XDG_RUNTIME_DIR": str(workspace / "runtime"),
            }
        )
        return environment

    def _wait(
        self,
        name: str,
        process: subprocess.Popen[bytes],
        *,
        deadline_monotonic: float | None,
        cancellation_requested: Callable[[], bool],
    ) -> int:
        deadline = time.monotonic() + self._config.timeout_seconds
        if deadline_monotonic is not None:
            deadline = min(deadline, deadline_monotonic)
        while True:
            try:
                cancelled = cancellation_requested()
            except BaseException:
                self._terminate_group(process)
                raise
            if cancelled:
                self._terminate_group(process)
                raise EngineProbeError(name, "cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate_group(process)
                raise EngineProbeError(name, "timeout")
            try:
                return_code = process.wait(
                    timeout=min(self._config.poll_seconds, remaining)
                )
            except subprocess.TimeoutExpired:
                continue
            except BaseException:
                self._terminate_group(process)
                raise
            # A probe that exits must not leave descendants behind either.
            self._terminate_group(process)
            return return_code

    def _terminate_group(self, process: subprocess.Popen[bytes]) -> None:
        # Shared with the LibreOffice adapter: wait for the whole process group,
        # not just its leader, and escalate any survivor to SIGKILL.
        terminate_process_group(process, self._config.termination_grace_seconds)
