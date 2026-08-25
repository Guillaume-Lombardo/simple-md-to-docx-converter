"""Real-process tests for changed-line coverage enforcement."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.ci.check_changed_coverage import main


def _file_coverage(*, covered: int) -> dict[str, object]:
    executed = list(range(1, covered + 1))
    missing = list(range(covered + 1, 11))
    return {
        "executed_lines": executed,
        "missing_lines": missing,
        "excluded_lines": [],
        "executed_branches": [],
        "missing_branches": [],
        "functions": {},
        "classes": {},
        "summary": {
            "covered_lines": len(executed),
            "missing_lines": len(missing),
            "excluded_lines": 0,
            "num_statements": 10,
            "covered_branches": 0,
            "missing_branches": 0,
            "num_branches": 0,
            "num_partial_branches": 0,
        },
    }


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository_with_changed_source(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    source = repository / "src/markweave/service.py"
    source.parent.mkdir(parents=True)
    _git(repository.parent, "init", "--initial-branch=main", str(repository))
    _git(repository, "config", "user.email", "quality@example.invalid")
    _git(repository, "config", "user.name", "Quality Test")
    source.write_text("value = 0\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "base")
    base = _git(repository, "rev-parse", "HEAD")
    source.write_text(
        "\n".join(f"value_{line} = {line}" for line in range(1, 11)) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change")
    return repository, base, _git(repository, "rev-parse", "HEAD")


@pytest.mark.integration
@pytest.mark.parametrize(("covered", "expected"), [(9, 0), (8, 1)])
def test_real_git_diff_enforces_success_and_failure_boundary(
    tmp_path: Path, covered: int, expected: int
) -> None:
    """The CLI measures committed Python additions through a real Git process."""
    repository, base, head = _repository_with_changed_source(tmp_path)
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {"files": {"src/markweave/service.py": _file_coverage(covered=covered)}}
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--base",
                base,
                "--head",
                head,
                "--coverage",
                str(report),
                "--repository",
                str(repository),
            ]
        )
        == expected
    )


@pytest.mark.integration
def test_real_git_failure_is_reported_as_policy_error(tmp_path: Path) -> None:
    """An unavailable comparison ref fails closed instead of reporting success."""
    repository, _, head = _repository_with_changed_source(tmp_path)
    report = tmp_path / "coverage.json"
    report.write_text('{"files": {}}', encoding="utf-8")
    assert (
        main(
            [
                "--base",
                "missing-ref",
                "--head",
                head,
                "--coverage",
                str(report),
                "--repository",
                str(repository),
            ]
        )
        == 2
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from pathlib import Path\n\nvalue = Path('.')\n", 0),
        ("from unittest.mock import patch\n\nvalue = patch\n", 1),
    ],
)
def test_real_ruff_enforces_pytest_mock_import_policy(
    tmp_path: Path, source: str, expected: int
) -> None:
    """The committed Ruff policy accepts normal imports and rejects unittest.mock."""
    candidate = tmp_path / "candidate.py"
    candidate.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        ["uv", "run", "ruff", "check", str(candidate)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == expected
