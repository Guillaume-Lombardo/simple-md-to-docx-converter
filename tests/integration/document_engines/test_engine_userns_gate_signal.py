"""Exercise runner-gate interruption without modifying host AppArmor policy."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_GATE_PROBE = """
import os
import signal
import sys
from pathlib import Path
from scripts.ci import engine_userns_gate as gate

gate._APPARMOR_USERNS_FILE = Path(sys.argv[1])
gate._run_preflight = lambda: 0
def record_policy(value):
    if value == "1":
        os.kill(os.getpid(), signal.SIGTERM)
    print(f"policy:{value}", flush=True)
    return True
gate._set_apparmor_userns_restriction = record_policy
gate._run_with_temporary_allowance([
    sys.executable,
    "-c",
    "import time; print('child-running', flush=True); time.sleep(30)",
])
"""


def _read_line(process: subprocess.Popen[bytes]) -> bytes:
    assert process.stdout is not None
    readable, _, _ = select.select([process.stdout], [], [], 10)
    assert readable, "runner gate did not report readiness"
    return process.stdout.readline().strip()


def test_interrupt_kills_running_test_child_and_restores_policy(tmp_path: Path) -> None:
    policy = tmp_path / "apparent-app-armor-setting"
    policy.write_text("1\n")
    process = subprocess.Popen(
        [sys.executable, "-c", _GATE_PROBE, str(policy)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        start_new_session=True,
    )
    try:
        assert _read_line(process) == b"policy:0"
        assert _read_line(process) == b"child-running"
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=10)
        assert process.returncode == 128 + signal.SIGTERM, error
        assert b"policy:1" in output.splitlines()
    finally:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
