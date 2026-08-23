"""Tests for shell-free execution of activated CI domains."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.ci.run_domain import main, run_domain
from scripts.ci.select_domains import DOMAIN_PATTERNS


def _write_registry(path: Path, *, command: list[str] | None = None) -> None:
    registry: dict[str, dict[str, str | list[str]]] = {
        domain: {"status": "planned", "activation_ticket": "T99"}
        for domain in DOMAIN_PATTERNS
    }
    if command is not None:
        registry["functional"] = {
            "status": "active",
            "activation_ticket": "T06",
            "command": command,
        }
    path.write_text(json.dumps(registry), encoding="utf-8")


@pytest.mark.unit
def test_runner_passes_reviewed_argument_vector_without_shell(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Active commands are passed directly to subprocess without shell parsing."""
    registry = tmp_path / "domains.json"
    _write_registry(registry, command=["uv", "run", "pytest", "-m", "functional"])
    completed = mocker.Mock(returncode=0)
    run = mocker.patch("scripts.ci.run_domain.subprocess.run", return_value=completed)
    assert main(["functional", "--registry", str(registry)]) == 0
    run.assert_called_once_with(
        ["uv", "run", "pytest", "-m", "functional"],
        check=False,
    )


@pytest.mark.unit
def test_runner_refuses_planned_domain(tmp_path: Path) -> None:
    """A planned suite cannot be mislabeled as executed."""
    registry = tmp_path / "domains.json"
    _write_registry(registry)
    with pytest.raises(ValueError, match="not active"):
        run_domain("functional", registry)


@pytest.mark.unit
def test_runner_refuses_unknown_domain(tmp_path: Path) -> None:
    """The workflow matrix cannot introduce an unregistered domain."""
    registry = tmp_path / "domains.json"
    _write_registry(registry)
    with pytest.raises(ValueError, match="unknown CI domain"):
        run_domain("unknown", registry)


@pytest.mark.integration
@pytest.mark.parametrize("expected", [0, 7])
def test_runner_propagates_real_process_status(tmp_path: Path, expected: int) -> None:
    """The real subprocess boundary preserves success and failure statuses."""
    registry = tmp_path / "domains.json"
    _write_registry(
        registry, command=[sys.executable, "-c", f"raise SystemExit({expected})"]
    )
    assert main(["functional", "--registry", str(registry)]) == expected
