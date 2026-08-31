"""Deterministic coverage for final-image E2E host-artifact containment."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

HARNESS = Path("scripts/e2e/harness.sh").resolve()
RUNNER = Path("scripts/e2e/run.sh").resolve()


@pytest.mark.unit
def test_relative_runtime_marker_is_contained_by_owned_directory() -> None:
    with tempfile.TemporaryDirectory(prefix="tmp.") as directory:
        harness_directory = Path(directory)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; e2e_run_in_harness_directory "$2" '
                "bash -c 'printf runtime-marker > oom'",
                "bash",
                str(HARNESS),
                str(harness_directory),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert (harness_directory / "oom").read_text(encoding="utf-8") == (
            "runtime-marker"
        )
        assert not Path("oom").exists()


@pytest.mark.unit
def test_worktree_guard_reports_and_preserves_unexpected_change(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(repository)],
        check=True,
    )

    with tempfile.TemporaryDirectory(prefix="tmp.") as directory:
        baseline = Path(directory) / "worktree-before"
        subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; e2e_capture_worktree_state "$2" "$3"',
                "bash",
                str(HARNESS),
                str(repository),
                str(baseline),
            ],
            check=True,
        )
        unexpected = repository / "unexpected"
        unexpected.touch()
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; e2e_require_worktree_unchanged "$2" "$3"',
                "bash",
                str(HARNESS),
                str(repository),
                str(baseline),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    assert result.returncode == 1
    assert "?? unexpected" in result.stderr
    assert unexpected.exists()


@pytest.mark.unit
def test_every_final_image_container_monitor_inherits_owned_directory() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    lines = runner.splitlines()
    podman_runs = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("podman run")
    ]

    assert len(podman_runs) == 8
    assert all(
        'e2e_run_in_harness_directory "$temporary_directory"' in lines[index - 1]
        for index in podman_runs
    )
    assert 'rm -f -- "$repository/oom"' not in runner
    assert 'e2e_require_worktree_unchanged "$repository" "$worktree_baseline"' in (
        runner
    )
