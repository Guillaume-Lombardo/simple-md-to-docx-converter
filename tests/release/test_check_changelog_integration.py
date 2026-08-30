"""Real Git-boundary coverage for material-version changelog validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.release.check_changelog import ChangelogError, check_repository_transition

pytestmark = pytest.mark.integration


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Release Test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD")


def _repository_with_transition(tmp_path: Path) -> tuple[Path, str, str]:
    project = tmp_path / "pyproject.toml"
    changelog = tmp_path / "CHANGELOG.md"
    project.write_text('[project]\nversion = "0.4.0"\n', encoding="utf-8")
    changelog.write_text("# Changelog\n", encoding="utf-8")
    _git(tmp_path, "init", "--initial-branch=main")
    before = _commit(tmp_path, "release source")
    project.write_text('[project]\nversion = "0.5.0"\n', encoding="utf-8")
    changelog.write_text("## [0.5.0] - 2026-08-30\n", encoding="utf-8")
    head = _commit(tmp_path, "release target")
    return tmp_path, before, head


def test_real_git_history_accepts_an_exact_material_transition(tmp_path: Path) -> None:
    repository, before, head = _repository_with_transition(tmp_path)

    check_repository_transition(repository=repository, before=before, head=head)


def test_real_git_history_rejects_a_divergent_head(tmp_path: Path) -> None:
    repository, before, _ = _repository_with_transition(tmp_path)

    with pytest.raises(ChangelogError, match="checked-out HEAD"):
        check_repository_transition(repository=repository, before=before, head=before)


def test_real_git_history_rejects_a_missing_previous_commit(tmp_path: Path) -> None:
    repository, _, head = _repository_with_transition(tmp_path)

    with pytest.raises(ChangelogError, match="cannot inspect"):
        check_repository_transition(repository=repository, before="a" * 40, head=head)


def test_real_git_history_rejects_a_previous_commit_without_pyproject(
    tmp_path: Path,
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n", encoding="utf-8")
    _git(tmp_path, "init", "--initial-branch=main")
    before = _commit(tmp_path, "incomplete source")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "0.5.0"\n', encoding="utf-8"
    )
    changelog.write_text("## [0.5.0] - 2026-08-30\n", encoding="utf-8")
    head = _commit(tmp_path, "release target")

    with pytest.raises(ChangelogError, match="cannot inspect"):
        check_repository_transition(repository=tmp_path, before=before, head=head)
