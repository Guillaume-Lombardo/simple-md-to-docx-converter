"""Real process-group cleanup coverage for bounded release commands."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import pytest

from scripts.release import process as runner
from scripts.release.artifacts import ArtifactError
from scripts.release.process import run_command

pytestmark = pytest.mark.integration

SPAWN_DESCENDANT = """\
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="ascii")
time.sleep(60)
"""


def test_timeout_terminates_and_reaps_real_descendant_group(tmp_path: Path) -> None:
    """A timed-out leader cannot leave its real child process running."""
    child_pid_file = tmp_path / "child.pid"
    with pytest.raises(ArtifactError, match="timed out"):
        run_command(
            (sys.executable, "-c", SPAWN_DESCENDANT, str(child_pid_file)),
            cwd=tmp_path,
            label="blocking descendant fixture",
            timeout=0.5,
        )

    child_pid = int(child_pid_file.read_text(encoding="ascii"))
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


@pytest.mark.parametrize("ignore_term", [False, True])
def test_timeout_adopts_and_reaps_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ignore_term: bool
) -> None:
    """Reaping is independent of PID 1 and also handles SIGTERM-resistant children."""

    monkeypatch.setattr(runner, "TERMINATION_GRACE_SECONDS", 0.2)
    libc = ctypes.CDLL(None)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    # Retain orphan zombies in this process instead of relying on the host's init.
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    child_pid_file = tmp_path / "child.pid"
    script = SPAWN_DESCENDANT
    if ignore_term:
        script = script.replace(
            "import time; time.sleep(60)",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        )
    try:
        with pytest.raises(ArtifactError, match="timed out"):
            run_command(
                (sys.executable, "-c", script, str(child_pid_file)),
                cwd=tmp_path,
                label="orphan fixture",
                timeout=0.5,
            )
        child_pid = int(child_pid_file.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        current = ctypes.c_int()
        assert libc.prctl(37, ctypes.byref(current), 0, 0, 0) == 0
        assert current.value == 1
    finally:
        if child_pid_file.exists():
            with suppress(ChildProcessError):
                os.waitpid(int(child_pid_file.read_text()), os.WNOHANG)
        assert libc.prctl(36, previous.value, 0, 0, 0) == 0


@pytest.mark.parametrize("child_exited", [False, True])
def test_leader_exit_distinguishes_live_and_zombie_descendants(
    tmp_path: Path, child_exited: bool
) -> None:
    """Dead adopted children are reaped; a live leftover remains a release error."""
    child_pid_file = tmp_path / "child.pid"
    script = """
import os, sys, time
from pathlib import Path
pid = os.fork()
if pid == 0:
    if sys.argv[2] == 'dead':
        os._exit(0)
    time.sleep(60)
    os._exit(0)
Path(sys.argv[1]).write_text(str(pid))
if sys.argv[2] == 'dead':
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
os._exit(0)
"""
    command = (
        sys.executable,
        "-c",
        script,
        str(child_pid_file),
        "dead" if child_exited else "live",
    )
    if child_exited:
        run_command(command, cwd=tmp_path, label="exited child", timeout=5)
    else:
        with pytest.raises(ArtifactError, match="left descendant processes running"):
            run_command(command, cwd=tmp_path, label="live child", timeout=5)
    with pytest.raises(ProcessLookupError):
        os.kill(int(child_pid_file.read_text()), 0)


def test_real_zombie_cleanup_failure_is_not_reported_as_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual unreaped group stays a cleanup failure, even after SIGKILL."""

    monkeypatch.setattr(runner, "TERMINATION_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(runner, "_reap_group", lambda _group: None)
    child_pid_file = tmp_path / "child.pid"
    try:
        with pytest.raises(ArtifactError, match="process group could not be reaped"):
            run_command(
                (sys.executable, "-c", SPAWN_DESCENDANT, str(child_pid_file)),
                cwd=tmp_path,
                label="unreaped child",
                timeout=0.5,
            )
        child_pid = int(child_pid_file.read_text())
        assert Path(f"/proc/{child_pid}/stat").read_text().split(") ")[1][0] == "Z"
    finally:
        if child_pid_file.exists():
            with suppress(ChildProcessError):
                os.waitpid(int(child_pid_file.read_text()), 0)


def test_reaping_preserves_unrelated_child_exit_status(tmp_path: Path) -> None:
    """Group-specific waitpid must not consume another subprocess owner's result."""

    with subprocess.Popen((sys.executable, "-c", "raise SystemExit(17)")) as unrelated:
        os.waitid(os.P_PID, unrelated.pid, os.WEXITED | os.WNOWAIT)
        run_command(
            (sys.executable, "-c", "pass"), cwd=tmp_path, label="success", timeout=5
        )
        assert unrelated.wait(timeout=5) == 17


def test_portable_release_process_probe() -> None:
    """Keep the final-image acceptance probe executable on development hosts too."""
    subprocess.run(
        (sys.executable, "-m", "tests.release.process_probe"),
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        timeout=15,
    )


def test_concurrent_commands_restore_subreaper_state(tmp_path: Path) -> None:
    """Concurrent callers cannot restore process-wide adoption during another run."""
    before = ctypes.c_int()
    libc = ctypes.CDLL(None)
    assert libc.prctl(37, ctypes.byref(before), 0, 0, 0) == 0

    def execute(index: int) -> None:
        pid_file = tmp_path / f"thread-{index}.pid"
        with pytest.raises(ArtifactError, match="timed out"):
            run_command(
                (sys.executable, "-c", SPAWN_DESCENDANT, str(pid_file)),
                cwd=tmp_path,
                label="concurrent command",
                timeout=0.5,
            )
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(execute, range(2)))
    after = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(after), 0, 0, 0) == 0
    assert after.value == before.value
