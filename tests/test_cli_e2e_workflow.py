"""CI-policy checks for the final-image CLI pseudo-terminal driver."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _driver_module():
    path = Path("tests/e2e/cli_workflow.py")
    specification = importlib.util.spec_from_file_location("cli_e2e_workflow", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_cli_e2e_uses_a_tty_only_for_the_password_prompt() -> None:
    """Captured commands cannot request a terminal from a pipe-based subprocess."""
    driver = _driver_module()

    prompted = driver._exec_prefix("markweave", tty=True)
    captured = driver._exec_prefix("markweave", tty=False)

    assert prompted[:4] == ["podman", "exec", "--interactive", "--tty"]
    assert "--tty" not in captured
    assert "--interactive" not in captured
    assert (
        prompted[-2:]
        == captured[-2:]
        == [
            "XDG_STATE_HOME=/tmp/markweave-cli-state",
            "/opt/md-converter/venv/bin/markweave",
        ]
    )


@pytest.mark.parametrize("profile", ("standalone", "distributed"))
def test_cli_e2e_uses_the_profile_provisioned_credential(profile: str) -> None:
    """The inspected login password is not the application's environment secret."""
    username, password = _driver_module()._provisioned_login(profile)

    assert username == f"e2e-provisioned-{profile}"
    assert password == f"Provisioned-{profile}-initial"
    assert password != "e2e-admin-password"  # noqa: S105 - fixture boundary check
