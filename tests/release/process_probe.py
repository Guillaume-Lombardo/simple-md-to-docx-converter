"""Portable release-tool acceptance probe, also executable in the final Linux image."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from scripts.release import process as runner
from scripts.release.artifacts import ArtifactError

SCRIPT = """
import os, signal, sys, time
from pathlib import Path
pid = os.fork()
if pid == 0:
    if sys.argv[2] == 'zombie':
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
    os._exit(0)
Path(sys.argv[1]).write_text(str(pid))
if sys.argv[2] == 'zombie':
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
if sys.argv[2] in ('zombie', 'leftover'):
    os._exit(0)
time.sleep(60)
"""


def _leave_zombies(process_group: int) -> None:
    """Inject a reaping failure without preventing real group termination."""


def main() -> None:
    """Exercise success, timeout, live leftovers, zombies, and cleanup failure."""
    runner.TERMINATION_GRACE_SECONDS = 0.2
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        runner.run_command(
            (sys.executable, "-c", "pass"), cwd=cwd, label="ok", timeout=5
        )
        for mode, expected in (
            ("timeout", "probe timed out"),
            ("leftover", "probe left descendant processes running"),
            ("zombie", None),
            ("cleanup-failure", "probe process group could not be reaped"),
        ):
            pid_file = cwd / f"{mode}.pid"
            original = runner._reap_group
            if mode == "cleanup-failure":
                runner._reap_group = _leave_zombies
            try:
                try:
                    runner.run_command(
                        (sys.executable, "-c", SCRIPT, str(pid_file), mode),
                        cwd=cwd,
                        label="probe",
                        timeout=0.5,
                    )
                except ArtifactError as error:
                    assert str(error) == expected, (mode, str(error))
                else:
                    assert expected is None, mode
                pid = int(pid_file.read_text())
                if mode == "cleanup-failure":
                    assert (
                        Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] == "Z"
                    )
                    os.waitpid(pid, 0)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError(f"{mode}: child was not reaped")
            finally:
                runner._reap_group = original
    print(
        "Release process boundary: success, timeout, leftovers, zombies, cleanup failure passed"
    )


if __name__ == "__main__":
    main()
