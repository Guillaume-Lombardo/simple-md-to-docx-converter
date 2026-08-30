"""Static safety checks for the final-image conversion CLI workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _driver_module():
    path = Path("tests/e2e/conversion_cli_workflow.py")
    specification = importlib.util.spec_from_file_location(
        "conversion_cli_e2e_workflow", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_conversion_cli_e2e_keeps_source_and_state_inside_container() -> None:
    driver = _driver_module()
    prefix = driver._exec_prefix("markweave", tty=False)

    assert prefix[:4] == [
        "podman",
        "exec",
        "--env",
        "XDG_STATE_HOME=/tmp/markweave-t33-cli-state",
    ]
    assert "--interactive" not in prefix and "--tty" not in prefix
    assert prefix[-1] == "/opt/md-converter/venv/bin/markweave"


def test_conversion_cli_e2e_uses_tty_only_for_login() -> None:
    driver = _driver_module()

    assert driver._exec_prefix("markweave", tty=True)[2:4] == [
        "--interactive",
        "--tty",
    ]


def test_conversion_cli_e2e_json_parser_fails_closed() -> None:
    driver = _driver_module()
    completed = driver.subprocess.CompletedProcess(
        [], 0, stdout='{"state":"ok"}', stderr=""
    )
    invalid = driver.subprocess.CompletedProcess([], 0, stdout="[]", stderr="")

    assert driver._json_result(completed) == {"state": "ok"}
    assert driver._json_result(invalid) is None
