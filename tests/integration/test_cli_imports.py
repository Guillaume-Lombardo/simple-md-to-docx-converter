"""Import-boundary coverage for the lightweight command-line package."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_cli_help_and_version_do_not_import_server_or_optional_backends() -> None:
    """The lightweight console surface remains compatible with future T40 extras."""
    script = """
import sys
import markweave
from markweave.cli.main import main
for arguments in (("--version",), ("--help",)):
    try:
        main(arguments)
    except SystemExit as error:
        if error.code != 0:
            raise
for forbidden in (
    "markweave.app",
    "markweave.config",
    "markweave.recovery",
    "markweave.recovery_manifest",
    "boto3",
    "botocore",
    "pydantic",
    "psycopg",
    "sqlalchemy",
    "fastapi",
):
    if forbidden in sys.modules:
        raise SystemExit(f"unexpected eager import: {forbidden}")
"""
    result = subprocess.run(
        (sys.executable, "-I", "-c", script),
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
