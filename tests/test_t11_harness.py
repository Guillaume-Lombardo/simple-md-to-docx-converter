"""Contract checks for the reproducible T11 rootless test harness."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_t11_harness_is_valid_shell_and_preserves_security_contract() -> None:
    path = Path("spikes/toolchain/run-t11-tests.sh")
    subprocess.run(["bash", "-n", str(path)], check=True)
    script = path.read_text(encoding="utf-8")
    for required in (
        '--user "${RUNTIME_UID}:0"',
        "--read-only",
        "--network none",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--memory 2g",
        "--cpus 2",
        "--pids-limit 512",
        "--locked",
        "test_libreoffice_pdf.py",
        "test_pdf_rasterizer.py",
        "podman volume rm --force",
    ):
        assert required in script
