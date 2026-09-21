"""Static safety checks for the final-image conversion CLI workflow."""

from __future__ import annotations

import importlib.util
import re
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


def test_conversion_cli_e2e_runs_after_shared_cli_and_before_template_cli() -> None:
    runner = Path("scripts/e2e/run.sh").read_text(encoding="utf-8")
    shared_cli = runner.index("tests.e2e.cli_workflow")
    invocation = "uv run python -m tests.e2e.conversion_cli_workflow"
    normal = runner.index(invocation)
    held = runner.index(invocation, normal + 1)
    templates = runner.index("tests.e2e.template_cli_workflow")
    normal_block = runner[normal : runner.index("\n\n", normal)]
    held_block = runner[held : runner.index("\n\n", held)]

    assert shared_cli < normal < templates
    assert runner.find(invocation, held + 1) == -1
    assert '--container "$application_name" --profile "$profile"' in normal_block
    assert "--reverse-held-queue" not in normal_block
    assert runner.index("MARKWEAVE_WORKER_IDLE_POLL_SECONDS=600") < held
    assert "--reverse-held-queue" in held_block
    assert "--reverse-upload-max-bytes 1024" in held_block


def test_conversion_cli_e2e_reads_authoritative_options() -> None:
    source = Path("tests/e2e/conversion_cli_workflow.py").read_text(encoding="utf-8")

    assert '"--json", "conversion-options"' in source
    assert 'options.get("conversion_upload_max_bytes") != 1_000_000' in source
    assert 'options.get("selection_source") != "system_fallback"' in source
    assert '[*prefix, "conversion-options"]' in source
    assert (
        '"Upload limit: 1000000 bytes; template selection: system fallback.\\n"'
        in source
    )


def test_conversion_cli_e2e_requires_exact_human_output() -> None:
    driver = _driver_module()
    expected = "Upload limit: 1000000 bytes; template selection: system fallback.\n"

    assert driver._has_exact_stdout(
        driver.subprocess.CompletedProcess([], 0, expected, ""), expected
    )
    assert not driver._has_exact_stdout(
        driver.subprocess.CompletedProcess([], 0, expected.rstrip(), ""), expected
    )
    assert not driver._has_exact_stdout(
        driver.subprocess.CompletedProcess([], 0, expected, "warning"), expected
    )


def test_conversion_cli_e2e_exercises_reverse_http_lifecycle() -> None:
    source = Path("tests/e2e/conversion_cli_workflow.py").read_text(encoding="utf-8")
    settings = Path("scripts/e2e/runtime-settings.sh").read_text(encoding="utf-8")

    for command in (
        "capabilities",
        "submit",
        "list",
        "show",
        "wait",
        "cancel",
        "download",
    ):
        assert re.search(rf'"reverse",\s*"{command}"', source)
    assert "reverse idempotent replay" in source
    assert "MARKWEAVE_REVERSION_UPLOAD_MAX_BYTES=1000000" in settings
    assert "MARKWEAVE_REVERSION_ACTIVE_LIMIT_PER_USER=8" in settings
