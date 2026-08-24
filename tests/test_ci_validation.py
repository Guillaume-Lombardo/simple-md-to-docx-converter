"""Tests for repository-local CI security validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.validate_ci import (
    main,
    validate_python_imports,
    validate_registry_text,
    validate_workflow_text,
)


@pytest.mark.unit
def test_committed_workflow_satisfies_local_security_policy() -> None:
    """The real workflow is covered by the same validator used in CI."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert validate_workflow_text(workflow) == []


@pytest.mark.unit
def test_document_engine_job_installs_checksum_locked_document_engines() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "if: ${{ matrix.domain == 'document-engines' }}" in workflow
    assert (
        "https://github.com/jgm/pandoc/releases/download/3.10.2/"
        "pandoc-3.10.2-linux-amd64.tar.gz"
    ) in workflow
    assert (
        "c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a" in workflow
    )
    assert "sha256sum --check --strict" in workflow
    assert (
        "878e5ab495b8a694980fca61bc09b37e651ccedce2291c73434d16e48a2646fd" in workflow
    )
    assert (
        "6fc7bf6f32bd3f3108c0955e8994c019c04cd9964b9c50472aa28474e9d7e73f" in workflow
    )
    assert 'PUPPETEER_SKIP_DOWNLOAD: "true"' in workflow
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020" in workflow
    assert "node-version: 22.23.1" in workflow
    assert 'test "$(node --version)" = "v22.23.1"' in workflow
    assert "npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts" in workflow
    assert "mmdc --version" in workflow
    assert "google-chrome-stable --version" in workflow


@pytest.mark.unit
def test_validator_rejects_unpinned_action_and_secret_access() -> None:
    """Mutable action tags and secret reads are rejected together."""
    workflow = "uses: actions/checkout@v7\nsecrets.DEPLOY_TOKEN\n"
    errors = validate_workflow_text(workflow)
    assert "every action reference must be pinned" in " ".join(errors)
    assert "forbidden workflow fragment: 'secrets.'" in errors


@pytest.mark.unit
def test_validator_rejects_short_hex_revision_and_missing_timeout() -> None:
    """Hex-looking abbreviations and unbounded jobs remain invalid pins."""
    workflow = "jobs:\n  check:\n    uses: owner/action@abc\n"
    errors = validate_workflow_text(workflow)
    assert "action revision is not a full commit SHA: 'abc'" in errors
    assert "every job must define a bounded timeout" in errors


@pytest.mark.unit
def test_validator_rejects_any_write_permission() -> None:
    """The validation-only workflow cannot acquire repository write access."""
    assert "write permission is forbidden in the CI workflow" in validate_workflow_text(
        "permissions:\n  checks: write\n"
    )


@pytest.mark.unit
def test_validator_rejects_broad_cache_writes() -> None:
    """Pull requests, merge groups, forks, releases, and manual runs remain restore-only."""
    errors = validate_workflow_text(
        "save-cache: ${{ github.event_name != 'pull_request' }}\n"
    )
    assert "cache writes must be limited to trusted pushes on main" in errors


@pytest.mark.unit
def test_gate_rejects_skipped_active_domain() -> None:
    """A selected active domain cannot be accepted as planned or skipped by the gate."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        'if [[ "$RUNNABLE_DOMAINS" == "[]" ]]; then',
        'if [[ "$HEAVY_RESULT" == "skipped" ]]; then',
    )
    assert any(
        "RUNNABLE_DOMAINS" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_removed_changed_line_coverage() -> None:
    """Pull requests cannot bypass the changed application line threshold."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "python -m scripts.ci.check_changed_coverage",
        "python -m scripts.ci.validate_ci",
    )
    assert any(
        "check_changed_coverage" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_removed_branch_only_coverage() -> None:
    """Combined coverage cannot replace the independent branch ratio gate."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "python -m scripts.ci.check_branch_coverage",
        "python -m scripts.ci.validate_ci",
    )
    assert any(
        "check_branch_coverage" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_duplicate_required_gate_name() -> None:
    """Branch protection must observe one unambiguous required check context."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    errors = validate_workflow_text(f"{workflow}\nname: CI / gate\n")
    assert "workflow must define exactly one CI / gate check" in errors


@pytest.mark.unit
def test_validator_rejects_direct_unittest_mock_import(tmp_path: Path) -> None:
    """Tests must use pytest-mock instead of importing unittest.mock."""
    source = tmp_path / "unsafe.py"
    source.write_text("from unittest.mock import patch\n", encoding="utf-8")
    assert validate_python_imports([source]) == [
        f"direct unittest.mock import in {source}"
    ]


@pytest.mark.unit
def test_registry_validator_rejects_invalid_json() -> None:
    """A malformed lifecycle registry fails cheap security validation."""
    assert validate_registry_text("{")[0].startswith("invalid domain registry JSON:")


@pytest.mark.unit
def test_registry_validator_rejects_partial_domain_set() -> None:
    """Cheap validation prevents a selector/registry mismatch."""
    assert validate_registry_text("{}") == [
        "domain registry does not match the selector's known domains"
    ]


@pytest.mark.unit
def test_committed_ci_validation_entrypoint_succeeds() -> None:
    """The cheap security command used by the workflow succeeds end to end."""
    assert main() == 0
