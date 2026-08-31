"""Shell-level coverage for the installed Markweave console executable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from markweave.version import VERSION

pytestmark = pytest.mark.integration


def test_installed_console_script_reports_version_and_login_argument_errors() -> None:
    """The console wrapper exposes the implemented authentication parser."""
    executable = Path(sys.executable).with_name("markweave")
    version = subprocess.run(
        (str(executable), "--version"), capture_output=True, check=False, text=True
    )
    assert version.returncode == 0
    assert version.stdout == f"markweave {VERSION}\n"
    assert version.stderr == ""

    invalid_login = subprocess.run(
        (str(executable), "--json", "login"),
        capture_output=True,
        check=False,
        text=True,
    )
    assert invalid_login.returncode == 2
    assert invalid_login.stdout == ""
    assert "--url" in invalid_login.stderr
