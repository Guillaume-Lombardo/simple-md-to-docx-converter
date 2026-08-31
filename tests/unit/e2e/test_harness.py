"""Deterministic coverage for final-image E2E host-artifact containment."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

HARNESS = Path("scripts/e2e/harness.sh").resolve()
RUNNER = Path("scripts/e2e/run.sh").resolve()


@pytest.mark.unit
def test_relative_runtime_marker_is_contained_by_owned_directory() -> None:
    with tempfile.TemporaryDirectory(prefix="markweave-e2e.") as directory:
        harness_directory = Path(directory)
        identity = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; e2e_harness_directory_identity "$2"',
                "bash",
                str(HARNESS),
                str(harness_directory),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; e2e_run_in_harness_directory "$2" "$3" '
                "bash -c 'printf runtime-marker > oom'",
                "bash",
                str(HARNESS),
                str(harness_directory),
                identity,
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
def test_alternate_tmpdir_launches_and_removes_only_owned_tree(
    tmp_path: Path,
) -> None:
    alternate_tmpdir = tmp_path / "alternate temporary root"
    alternate_tmpdir.mkdir()
    sibling = alternate_tmpdir / "preserve-me"
    sibling.mkdir()
    environment = {"TMPDIR": str(alternate_tmpdir)}
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; directory=""; identity=""; '
            "e2e_initialize_harness_directory directory identity; "
            'e2e_run_in_harness_directory "$directory" "$identity" '
            "bash -c 'printf runtime-marker > oom'; "
            'e2e_remove_harness_directory "$directory" "$identity"; '
            'printf "%s\\n" "$directory"',
            "bash",
            str(HARNESS),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    owned_directory = Path(result.stdout.strip())
    assert result.returncode == 0, result.stderr
    assert owned_directory.parent == alternate_tmpdir
    assert not owned_directory.exists()
    assert sibling.is_dir()


@pytest.mark.unit
@pytest.mark.parametrize("temporary_root_kind", ["relative", "symlink"])
def test_runner_rejects_unsafe_tmpdir_before_worktree_setup(
    tmp_path: Path,
    temporary_root_kind: str,
) -> None:
    if temporary_root_kind == "relative":
        temporary_root = "relative-temporary-root"
    else:
        real_temporary_root = tmp_path / "real-temporary-root"
        real_temporary_root.mkdir()
        symlink = tmp_path / "temporary-root-link"
        symlink.symlink_to(real_temporary_root, target_is_directory=True)
        temporary_root = str(symlink)
    environment = os.environ | {"TMPDIR": temporary_root}
    result = subprocess.run(
        ["bash", str(RUNNER), "standalone"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "unsafe temporary root" in result.stderr
    assert "worktree-before" not in result.stderr
    if temporary_root_kind == "symlink":
        assert list(real_temporary_root.iterdir()) == []


@pytest.mark.unit
def test_identity_failure_removes_new_tree_before_continuation(
    tmp_path: Path,
) -> None:
    alternate_tmpdir = tmp_path / "alternate temporary root"
    alternate_tmpdir.mkdir()
    sibling = alternate_tmpdir / "preserve-me"
    sibling.mkdir()
    continuation = alternate_tmpdir / "continued-to-worktree-setup"
    environment = os.environ | {"TMPDIR": str(alternate_tmpdir)}
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; '
            "e2e_harness_directory_identity() { return 42; }; "
            'directory=""; identity=""; '
            "e2e_initialize_harness_directory directory identity; "
            'printf continued > "$TMPDIR/continued-to-worktree-setup"',
            "bash",
            str(HARNESS),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert not continuation.exists()
    assert list(alternate_tmpdir.glob("markweave-e2e.*")) == []
    assert sibling.is_dir()


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
def test_repository_local_tmpdir_is_removed_before_worktree_check(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(repository)],
        check=True,
    )
    local_tmpdir = repository / "unignored-temporary-root"
    local_tmpdir.mkdir()
    sibling = local_tmpdir / "preserve-me"
    sibling.write_text("sibling", encoding="utf-8")
    unexpected = repository / "unexpected"
    environment = os.environ | {"TMPDIR": str(local_tmpdir)}
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -e; source "$1"; '
            'baseline="$(e2e_get_worktree_state "$2")"; '
            'directory=""; identity=""; '
            "e2e_initialize_harness_directory directory identity; "
            'printf runtime-marker > "$directory/oom"; '
            'printf unexpected > "$2/unexpected"; '
            'printf "%s\\n" "$directory"; '
            'e2e_remove_harness_directory "$directory" "$identity"; '
            'e2e_require_worktree_state_unchanged "$2" "$baseline"',
            "bash",
            str(HARNESS),
            str(repository),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    owned_directory = Path(result.stdout.strip())
    assert result.returncode == 1
    assert not owned_directory.exists()
    assert sibling.read_text(encoding="utf-8") == "sibling"
    assert unexpected.read_text(encoding="utf-8") == "unexpected"
    assert "?? unexpected" in result.stderr
    assert "markweave-e2e" not in result.stderr
    assert "oom" not in result.stderr


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
        '"$temporary_directory" "$temporary_directory_identity"' in lines[index - 1]
        for index in podman_runs
    )
    assert 'rm -f -- "$repository/oom"' not in runner
    removal_index = runner.index("if ! e2e_remove_harness_directory")
    worktree_index = runner.index("if ! e2e_require_worktree_state_unchanged")
    assert removal_index < worktree_index
