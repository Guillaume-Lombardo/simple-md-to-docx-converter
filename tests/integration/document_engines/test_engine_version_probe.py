"""Real-process termination coverage for isolated engine version probes (T106)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from markweave.conversion.engine_versions import (
    EngineProbeConfig,
    EngineProbeError,
    EngineVersionProbe,
)

pytestmark = pytest.mark.integration


def _resistant_engine(tmp_path: Path) -> tuple[Path, Path]:
    """A leader that dies on SIGTERM with a forked descendant that ignores it."""
    pid_file = tmp_path / "descendant.pid"
    engine = tmp_path / "forking-engine"
    engine.write_text(
        "#!/bin/sh\n"
        f'sh -c \'trap "" TERM; echo $$ > "{pid_file}"; exec sleep 120\' &\n'
        "exec sleep 120\n",
        encoding="utf-8",
    )
    engine.chmod(0o700)
    return engine, pid_file


def _probe(tmp_path: Path, timeout: float) -> EngineVersionProbe:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return EngineVersionProbe(
        EngineProbeConfig(timeout, 0.5, 0.05, workspace),
        {"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )


def _descendant(pid_file: Path) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if pid_file.is_file() and (content := pid_file.read_text().strip()):
            return int(content)
        time.sleep(0.02)
    raise AssertionError("The resistant descendant did not start.")


def _running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError, ProcessLookupError:
        # A process reaped while its entry is read yields ESRCH.
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"


def _survives(pid: int) -> bool:
    """Allow asynchronous SIGKILL delivery; a killed, unreaped zombie is dead."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _running(pid):
            return False
        time.sleep(0.02)
    return True


def test_timeout_kills_a_sigterm_resistant_descendant(tmp_path: Path) -> None:
    engine, pid_file = _resistant_engine(tmp_path)

    with pytest.raises(EngineProbeError) as captured:
        _probe(tmp_path, 3.0).observe("pandoc", str(engine))

    assert captured.value.reason == "timeout"
    assert not _survives(_descendant(pid_file))
    assert list((tmp_path / "workspace").iterdir()) == []


def test_cancellation_kills_a_sigterm_resistant_descendant(tmp_path: Path) -> None:
    engine, pid_file = _resistant_engine(tmp_path)

    with pytest.raises(EngineProbeError) as captured:
        _probe(tmp_path, 30.0).observe(
            "pandoc", str(engine), cancellation_requested=pid_file.is_file
        )

    assert captured.value.reason == "cancelled"
    assert not _survives(_descendant(pid_file))
    assert list((tmp_path / "workspace").iterdir()) == []
