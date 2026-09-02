"""Deterministic coverage for final-image E2E host-artifact containment."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HARNESS = Path("scripts/e2e/harness.sh").resolve()
RUNNER = Path("scripts/e2e/run.sh").resolve()
ADMIN_BROWSER = Path("tests/e2e/browser-next-admin.test.mjs").resolve()


@pytest.mark.unit
def test_admin_browser_disambiguates_concurrent_account_fields() -> None:
    source = ADMIN_BROWSER.read_text(encoding="utf-8")

    assert source.count('getByRole("textbox", { name: "Username", exact: true })') == 3
    assert (
        source.count(
            'getByRole("textbox", { name: "Search by username", exact: true })'
        )
        == 3
    )
    assert source.count('getByLabel("Temporary password", { exact: true })') == 2
    assert source.count('getByLabel("New temporary password", { exact: true })') == 1
    assert 'getByRole("textbox", { name: "Username" })' not in source
    assert 'getByRole("textbox", { name: "Search by username" })' not in source
    assert 'getByLabel("Temporary password")' not in source
    assert 'getByLabel("New temporary password")' not in source
    assert ".first()" not in source
    assert ".nth(" not in source


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

    assert len(podman_runs) == 13
    assert all(
        '"$temporary_directory" "$temporary_directory_identity"' in lines[index - 1]
        for index in podman_runs
    )
    assert 'rm -f -- "$repository/oom"' not in runner
    removal_index = runner.index("if ! e2e_remove_harness_directory")
    worktree_index = runner.index("if ! e2e_require_worktree_state_unchanged")
    assert removal_index < worktree_index


@pytest.mark.unit
def test_runner_invokes_next_conversion_browser_in_both_profile_matrix() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    main_index = runner.index("/e2e/browser-next-conversion.test.mjs")
    failure_index = runner.index(
        "/e2e/browser-next-conversion-failure.test.mjs", main_index
    )
    admission_index = runner.index(
        "/e2e/browser-next-conversion-admission.test.mjs", failure_index
    )
    restart_index = runner.index(
        'podman restart --time 15 "$application_name"', admission_index
    )
    recovery_index = runner.index(
        "/e2e/browser-next-conversion-restart.test.mjs", restart_index
    )
    short_lifetime_index = runner.index("MARKWEAVE_SESSION_ABSOLUTE_SECONDS=2")
    expiry_index = runner.index(
        "/e2e/browser-next-conversion-expiry.test.mjs", short_lifetime_index
    )

    assert runner.count("/e2e/browser-next-conversion.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-failure.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-admission.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-restart.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-expiry.test.mjs") == 1
    assert runner.count("MARKWEAVE_E2E_CONVERSION_STATE=") == 2
    assert (
        runner.index("/e2e/browser-next-auth.test.mjs")
        < main_index
        < failure_index
        < admission_index
        < restart_index
        < recovery_index
        < short_lifetime_index
        < expiry_index
    )


@pytest.mark.unit
def test_runner_invokes_next_administration_with_restored_policy_evidence(
    tmp_path: Path,
) -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    restore_index = runner.index("# Prove that an isolated snapshot restores")
    checkpoint_verify_index = runner.index(
        "tests.e2e.service_workflow verify-checkpoint", restore_index
    )
    policy_values_index = runner.index(
        '"policy_user_idle_minutes"', checkpoint_verify_index
    )
    auth_index = runner.index("/e2e/browser-next-auth.test.mjs", policy_values_index)
    conversion_failure_index = runner.index(
        "/e2e/browser-next-conversion-failure.test.mjs", auth_index
    )
    admin_cookie_index = runner.index(
        "/e2e/browser-next-admin-cookie.test.mjs", conversion_failure_index
    )
    admin_index = runner.index("/e2e/browser-next-admin.test.mjs", admin_cookie_index)
    admission_index = runner.index(
        "/e2e/browser-next-conversion-admission.test.mjs", admin_index
    )

    assert runner.count("/e2e/browser-next-admin-cookie.test.mjs") == 1
    assert runner.count("/e2e/browser-next-admin.test.mjs") == 1
    assert (
        checkpoint_verify_index
        < policy_values_index
        < auth_index
        < conversion_failure_index
        < admin_cookie_index
        < admin_index
        < admission_index
    )
    assert '"policy_admin_idle_minutes"' in runner[policy_values_index:auth_index]
    assert '"policy_revision"' in runner[policy_values_index:auth_index]
    assert (
        "value.isascii() and value.isdecimal()"
        in runner[policy_values_index:auth_index]
    )
    cookie_invocation = runner[conversion_failure_index:admin_index]
    assert (
        'podman exec \\\n  "$application_name" node --test '
        "/e2e/browser-next-admin-cookie.test.mjs" in cookie_invocation
    )
    invocation = runner[conversion_failure_index:admission_index]
    assert (
        "--env MARKWEAVE_E2E_CHECKPOINT_USER_IDLE_MINUTES="
        '"$checkpoint_user_idle_minutes"' in invocation
    )
    assert (
        "--env MARKWEAVE_E2E_CHECKPOINT_ADMIN_IDLE_MINUTES="
        '"$checkpoint_admin_idle_minutes"' in invocation
    )
    assert (
        "--env MARKWEAVE_E2E_CHECKPOINT_POLICY_REVISION="
        '"$checkpoint_policy_revision"' in invocation
    )
    assert "MARKWEAVE_E2E_CHECKPOINT_USER_IDLE_MINUTES=25" not in runner
    assert "MARKWEAVE_E2E_CHECKPOINT_ADMIN_IDLE_MINUTES=10" not in runner

    extraction_start = runner.index("import json", checkpoint_verify_index)
    extraction_end = runner.index('\n\' "$state_file"', extraction_start)
    extraction = runner[extraction_start:extraction_end]
    compile(extraction, str(RUNNER), "exec")
    state_file = tmp_path / "state.json"
    state_file.write_text(
        '{"policy_user_idle_minutes":"26",'
        '"policy_admin_idle_minutes":"11","policy_revision":"7"}\n',
        encoding="utf-8",
    )
    valid = subprocess.run(
        [sys.executable, "-c", extraction, str(state_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0
    assert valid.stdout == "26\t11\t7\n"
    state_file.write_text(
        '{"policy_user_idle_minutes":"-1",'
        '"policy_admin_idle_minutes":"11","policy_revision":"7"}\n',
        encoding="utf-8",
    )
    invalid = subprocess.run(
        [sys.executable, "-c", extraction, str(state_file)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
    assert "checkpoint policy evidence is invalid" in invalid.stderr


@pytest.mark.unit
def test_next_conversion_admission_phase_holds_workers_and_restores_runtime() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    probe_function = runner.index("wait_for_embedded_worker_idle()")
    probe_start = runner.index("from pathlib import Path", probe_function)
    probe_end = runner.index("\n'\n}", probe_start)
    compile(runner[probe_start:probe_end], str(RUNNER), "exec")
    main_index = runner.index("/e2e/browser-next-conversion.test.mjs")
    admission_index = runner.index(
        "/e2e/browser-next-conversion-admission.test.mjs", main_index
    )
    phase = runner[main_index:admission_index]

    assert 'podman stop --time 15 "$worker_one_name" "$worker_two_name"' in phase
    assert "MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER=2" in phase
    assert "MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY=3" in phase
    assert "MARKWEAVE_WORKER_IDLE_POLL_SECONDS=600" in phase
    assert 'wait_for_embedded_worker_idle "$application_name"' in phase
    assert 'expected_name = "md-converter-embedded-worker"' in runner
    assert 'Path("/proc").glob("[0-9]*/task/[0-9]*")' in runner
    assert 'and "futex" in wait_channel' in runner
    assert "stable_samples >= 5" in runner
    assert "deadline = monotonic() + 15" in runner
    assert "sleep 1" not in phase
    assert phase.index('"${application_settings[@]}"') < phase.index(
        "MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER=2"
    )
    restore = runner[
        admission_index : runner.index(
            'podman restart --time 15 "$application_name"', admission_index
        )
    ]
    assert 'podman start "$worker_one_name" "$worker_two_name"' in restore
    assert restore.count('"${application_settings[@]}"') == 1
