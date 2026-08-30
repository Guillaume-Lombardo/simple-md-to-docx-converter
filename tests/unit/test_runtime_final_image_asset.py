"""Static wiring checks for the T36 final-image runtime CLI smoke."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_runtime_cli_final_image_smoke_is_wired_and_hardened() -> None:
    script_path = Path("tests/e2e/runtime-operations-final-image.sh")
    subprocess.run(("bash", "-n", str(script_path)), check=True)
    script = script_path.read_text(encoding="utf-8")
    runner = Path("scripts/container/run-ci.sh").read_text(encoding="utf-8")

    assert 'bash tests/e2e/runtime-operations-final-image.sh "$final_image"' in runner
    for contract in (
        "--user",
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--pids-limit=256",
        "--tmpfs /work:",
        "--tmpfs /data:",
    ):
        assert contract in script
    assert "markweave --json migrate" in script
    assert "markweave --json --timeout 15 doctor" in script
    assert "markweave worker" in script
