"""Repository contracts for scheduled dependency and mutation maintenance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github/dependabot.yml"
MUTATION_WORKFLOW = ROOT / ".github/workflows/mutation.yml"
FULL_SHA_ACTION = re.compile(
    r"^\s*uses:\s*[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$", re.MULTILINE
)


def load_strings(path: Path) -> dict[str, Any]:
    """Load workflow keys without YAML 1.1 coercing GitHub's `on` key."""

    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    if True in loaded and "on" not in loaded:
        loaded["on"] = loaded.pop(True)
    return loaded


@pytest.mark.unit
def test_dependabot_covers_every_dependency_ecosystem_weekly_in_groups() -> None:
    config = load_strings(DEPENDABOT)
    updates = config["updates"]
    assert isinstance(updates, list)
    by_ecosystem = {entry["package-ecosystem"]: entry for entry in updates}
    assert set(by_ecosystem) == {"uv", "npm", "docker", "github-actions"}
    assert "pip" not in by_ecosystem
    assert by_ecosystem["uv"]["directory"] == "/"
    assert by_ecosystem["github-actions"]["directory"] == "/"
    assert by_ecosystem["npm"]["directories"] == ["/", "/spikes/toolchain"]
    assert by_ecosystem["docker"]["directories"] == ["/", "/spikes/toolchain"]
    for update in updates:
        assert update["schedule"]["interval"] == "weekly"
        assert update["schedule"]["timezone"] == "Etc/UTC"
        assert update["groups"]
        assert int(update["open-pull-requests-limit"]) <= 5


@pytest.mark.unit
def test_mutation_workflow_is_isolated_bounded_and_read_only() -> None:
    workflow = load_strings(MUTATION_WORKFLOW)
    triggers = workflow["on"]
    assert set(triggers) == {"pull_request", "schedule", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
    job = workflow["jobs"]["mutation"]
    assert int(job["timeout-minutes"]) == 30
    assert job["if"] == (
        "${{ github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
    )
    text = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    uses_lines = "\n".join(
        line for line in text.splitlines() if line.lstrip().startswith("uses:")
    )
    assert FULL_SHA_ACTION.findall(uses_lines) == uses_lines.splitlines()
    assert "secrets." not in text
    assert "github.token" not in text
    assert "id-token: write" not in text
    assert "contents: write" not in text


@pytest.mark.unit
def test_mutation_campaign_is_reproducible_nonempty_and_strict() -> None:
    workflow = load_strings(MUTATION_WORKFLOW)
    dispatch = workflow["on"]["workflow_dispatch"]["inputs"]["domain"]
    assert dispatch["type"] == "choice"
    assert dispatch["default"] == "all"
    assert dispatch["options"] == [
        "all",
        "observability",
        "auth-session",
        "archive-svg",
        "job-integrity",
        "retention-storage",
    ]
    text = MUTATION_WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/ci/run_mutation_campaign.py" in text
    assert "--mode changed" in text
    assert "--base-sha" in text
    assert "--head-sha" in text
    assert "actions/upload-artifact@043fb4608a7c11565688c193ef1d7a58c880d6f0" in text
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert '"mutmut==3.7.0"' in pyproject
    assert "[tool.mutmut]" in pyproject
    assert 'name = "mutmut"\nversion = "3.7.0"' in lock


@pytest.mark.unit
def test_mutation_output_is_ignored_but_configuration_is_tracked() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignored.count("mutants/") == 1
    assert ignored.count("mutation-results/") == 1
    assert ".github/" not in ignored
