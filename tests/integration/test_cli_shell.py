"""Shell-level coverage for the installed Markweave console executable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from markweave.version import VERSION

pytestmark = pytest.mark.integration


def test_installed_console_script_reports_version_and_safe_unavailability() -> None:
    """The console wrapper uses the stable root registry, not package internals."""
    executable = Path(sys.executable).with_name("markweave")
    version = subprocess.run(
        (str(executable), "--version"), capture_output=True, check=False, text=True
    )
    assert version.returncode == 0
    assert version.stdout == f"markweave {VERSION}\n"
    assert version.stderr == ""

    unavailable = subprocess.run(
        (str(executable), "--json", "login"),
        capture_output=True,
        check=False,
        text=True,
    )
    assert unavailable.returncode == 3
    assert unavailable.stdout == ""
    assert unavailable.stderr == (
        '{"error":{"code":"command_unavailable","message":"The \'login\' command is not available in this release."}}\n'
    )
