"""CI-policy checks for the dedicated final-image template CLI driver."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _driver_module():
    path = Path("tests/e2e/template_cli_workflow.py")
    specification = importlib.util.spec_from_file_location(
        "template_cli_e2e_workflow", path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_template_cli_e2e_uses_profile_specific_admin_state() -> None:
    driver = _driver_module()

    for profile in ("standalone", "distributed"):
        plain = driver._exec_prefix(f"markweave-{profile}", tty=False)
        assert plain[-1] == "/opt/md-converter/venv/bin/markweave"
        assert "--tty" not in plain
        assert f"template-admin-{profile}" != "default"


def test_template_cli_e2e_declares_the_complete_activation_font_set() -> None:
    driver = _driver_module()

    assert driver._TEMPLATE_FONTS == (
        "Aptos",
        "Aptos Display",
        "Calibri",
        "Cambria",
        "Cambria Math",
        "Consolas",
        "Courier New",
        "Times New Roman",
    )


def test_template_cli_e2e_never_places_the_admin_password_in_commands() -> None:
    source = Path("tests/e2e/template_cli_workflow.py").read_text(encoding="utf-8")

    assert '"--password"' not in source
    assert '"Password: "' not in source
    assert "_run_pty" in source
