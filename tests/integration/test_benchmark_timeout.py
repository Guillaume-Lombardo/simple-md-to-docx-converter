from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_benchmark_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    log = tmp_path / "timeout.log"
    descendant_pid_file = tmp_path / "descendant.pid"
    program = (
        'trap "" TERM; '
        f'sh -c \'trap "" TERM; echo $$ > {descendant_pid_file!s}; '
        "while :; do sleep 30; done' & "
        "wait"
    )

    completed = subprocess.run(
        [
            "scripts/javascript/run-bounded-benchmark-command.sh",
            "1",
            "1",
            str(log),
            "test/descendant",
            "--",
            "sh",
            "-c",
            program,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 124
    assert completed.stdout == ""
    assert completed.stderr == ""
    evidence = log.read_text(encoding="utf-8")
    assert "boundary_start label=test/descendant timeout_seconds=1" in evidence
    assert "boundary_timeout label=test/descendant" in evidence
    assert "normalized_status=124" in evidence

    descendant_pid = int(descendant_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(descendant_pid, signal.SIGKILL)
        pytest.fail("the timed-out command left its descendant running")
