"""Real process-group cleanup coverage for bounded release commands."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from scripts.release.artifacts import ArtifactError
from scripts.release.process import run_command

pytestmark = pytest.mark.integration

SPAWN_DESCENDANT = """\
import subprocess
import sys
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="ascii")
child.wait()
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
