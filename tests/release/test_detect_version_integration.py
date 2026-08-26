"""Real Git-boundary coverage for protected release retry detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.release.detect_version import (
    VersionTransition,
    detect_repository_transition,
)

pytestmark = pytest.mark.integration


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["/usr/bin/git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_real_git_history_detects_same_version_release_attempt(tmp_path: Path) -> None:
    """An exact protected Git transition can retry an unchanged final version."""
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "0.3.1"\n', encoding="utf-8")
    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "add", "pyproject.toml")
    _git(
        tmp_path,
        "-c",
        "user.name=Release Test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        "release source",
    )
    before = _git(tmp_path, "rev-parse", "HEAD")

    project.write_text(
        '[project]\nversion = "0.3.1"\n\n[tool.markweave.release]\nattempt = 2\n',
        encoding="utf-8",
    )
    _git(tmp_path, "add", "pyproject.toml")
    _git(
        tmp_path,
        "-c",
        "user.name=Release Test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        "retry release",
    )
    head = _git(tmp_path, "rev-parse", "HEAD")

    assert detect_repository_transition(
        repository=tmp_path, before=before, head=head
    ) == VersionTransition("0.3.1", "v0.3.1")
