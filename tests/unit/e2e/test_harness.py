"""Deterministic coverage for final-image E2E host-artifact containment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HARNESS = Path("scripts/e2e/harness.sh").resolve()
RUNNER = Path("scripts/e2e/run.sh").resolve()
ADMIN_BROWSER = Path("tests/e2e/browser-next-admin.test.mjs").resolve()
RESTART_PREPARATION_BROWSER = Path(
    "tests/e2e/browser-next-conversion-restart-prepare.test.mjs"
).resolve()
RESTART_BROWSER = Path("tests/e2e/browser-next-conversion-restart.test.mjs").resolve()
ADMISSION_FIXTURE = Path("tests/e2e/frontend-admission-fixture.mjs").resolve()
RUNTIME_FAILURE_BROWSER = Path(
    "tests/e2e/browser-next-runtime-failures.test.mjs"
).resolve()
BROWSER_RUNNER_SMOKE = Path("tests/e2e/browser-runner-smoke.mjs").resolve()


def _runner_text() -> str:
    """Return the harness with shard phase and diagnostic bodies dedented.

    Scenarios live in `e2e_phase_*` and `e2e_diagnostic_*` functions. Removing
    their two-space body indentation keeps these structural assertions about
    command order and arguments independent of that grouping.
    """
    lines = RUNNER.read_text(encoding="utf-8").splitlines(keepends=True)
    inside = False
    result = []
    for line in lines:
        if line.startswith(
            ("e2e_phase_", "e2e_diagnostic_")
        ) and line.rstrip().endswith("() {"):
            inside = True
        elif inside and line.rstrip("\n") == "}":
            inside = False
        elif inside and line.startswith("  "):
            result.append(line[2:])
            continue
        result.append(line)
    return "".join(result)


def _function(runner: str, name: str) -> str:
    """Return one top-level function body from the (dedented) harness text."""
    start = runner.index(f"\n{name}() {{\n") + len(f"\n{name}() {{\n")
    return runner[start : runner.index("\n}\n", start)]


@pytest.mark.unit
def test_final_frontend_binds_and_probes_loopback_independently_of_runtime_hostname() -> (
    None
):
    runner = _runner_text()
    start_frontend = runner.split("start_frontend() {", 1)[1].split(
        "restart_backend_and_router() {", 1
    )[0]

    assert "--env HOSTNAME=0.0.0.0" in start_frontend
    assert 'fetch("http://127.0.0.1:3001/_frontend/health/ready"' in start_frontend
    assert "node:os" not in start_frontend
    assert 'e2e_podman logs "$frontend_name"' in start_frontend


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function; no runtime starts"
)
@pytest.mark.parametrize(
    ("inspected_address", "expected_code", "expected_origin"),
    [
        ("10.89.2.17", 0, "http://10.89.2.17:3000\n"),
        ("", 1, ""),
        ("10.89.2.17\n10.89.2.18", 1, ""),
        ("10.89.2.999", 1, ""),
    ],
)
def test_admission_frontend_origin_requires_one_valid_named_network_address(
    inspected_address: str,
    expected_code: int,
    expected_origin: str,
) -> None:
    runner = _runner_text()
    helper_body = runner.split("admission_frontend_origin() {", 1)[1].split(
        "\nrestart_backend_and_router() {", 1
    )[0]
    helper = f"admission_frontend_origin() {{{helper_body}"
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'network_name="e2e-network"\n'
            'frontend_name="e2e-frontend"\n'
            "podman() {\n"
            "  printf '%s\\n' \"$INSPECTED_ADDRESS\"\n"
            "}\n"
            f"{helper}\n"
            "admission_frontend_origin",
            "bash",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | {"INSPECTED_ADDRESS": inspected_address},
    )

    assert result.returncode == expected_code
    assert result.stdout == expected_origin


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
    runner = _runner_text()
    lines = runner.splitlines()
    podman_runs = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("podman run")
    ]

    assert len(podman_runs) == 17
    assert all(
        any(
            '"$temporary_directory" "$temporary_directory_identity"' in line
            for line in lines[max(0, index - 3) : index]
        )
        for index in podman_runs
    )
    assert 'rm -f -- "$repository/oom"' not in runner
    removal_index = runner.index("if ! e2e_remove_harness_directory")
    worktree_index = runner.index("if ! e2e_require_worktree_state_unchanged")
    assert removal_index < worktree_index


@pytest.mark.unit
def test_browser_driver_has_its_own_bounded_cgroup_and_current_backend_network() -> (
    None
):
    runner = _runner_text()
    browser = runner.split("run_browser_test() {", 1)[1].split(
        "\nstart_production_router() {", 1
    )[0]
    shared = runner.split("application_volumes=(", 1)[1].split(
        "\napplication_mode=serve", 1
    )[0]
    assert (
        "--memory=768m"
        in runner.split("hardened_runtime=(", 1)[1].split("\nrun_browser_test() {", 1)[
            0
        ]
    )
    assert 'app_memory_limit="$(podman inspect "$backend_container"' in browser
    assert 'podman exec "$backend_container" cat /sys/fs/cgroup/memory.max' in browser
    assert '"$app_memory_limit" != 805306368' in browser
    assert '--network "container:$backend_container"' in browser
    assert "--memory=2g --cpus=2 --pids-limit=512 --shm-size=256m" in browser
    assert "timeout --signal=TERM --kill-after=15s 25m" in browser
    assert '--entrypoint /bin/sh "$image"' in browser
    assert "umask 0077" in browser
    assert 'for directory in "$HOME" "$TMPDIR" "$XDG_CACHE_HOME"' in browser
    assert '"$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR"' in browser
    assert 'mkdir -p -- "$directory"' in browser
    assert 'chmod 0700 -- "$directory"' in browser
    assert (
        browser.index('chmod 0700 -- "$directory"')
        < browser.index('memory_limit="$(cat /sys/fs/cgroup/memory.max)"')
        < browser.index('node --test "$test_file"')
    )
    assert "--read-only --cap-drop=all --security-opt=no-new-privileges" in browser
    assert '--security-opt="seccomp=$seccomp_profile"' in browser
    assert '"$test_file" =~ ^/e2e/browser-[a-z0-9-]+\\.test\\.mjs$' in browser
    assert '"$test_file" == /e2e/browser-runner-smoke.mjs' in browser
    assert "runner_memory_max=%s" in browser
    assert "application_memory_max=%s" in browser
    assert "runner_memory_peak=%s" in browser
    assert "/sys/fs/cgroup/memory.events" in browser
    assert (
        'printf "runner_memory_peak=%s\\n" "$peak" >> "$evidence" || receipt_failed=1'
        in browser
    )
    assert (
        'cat /sys/fs/cgroup/memory.events >> "$evidence" || receipt_failed=1' in browser
    )
    assert 'if [ "$result" -eq 0 ]; then exit 1; fi' in browser
    assert 'exit "$result"' in browser
    assert 'podman rm --force "$browser_runner_name"' in browser
    assert (
        'podman rm --force "$browser_runner_name"'
        in runner.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    )
    assert (
        '"$browser_runner_name"'
        in runner.split("refuse_existing_resources() {", 1)[1].split(
            "\nwait_for_url() {", 1
        )[0]
    )
    for mount in (
        "/e2e:ro,z",
        "/node_modules:ro,z",
        "/evidence:rw,z",
        "/browser-artifacts:rw,z",
        "/browser-session:rw,z",
    ):
        assert mount in browser and mount in shared
    assert "/data:" not in browser
    assert "/run/secrets/" not in browser
    assert '"$test_file" == /e2e/browser-next-composer-real.test.mjs' in browser
    assert '"$test_file" == /e2e/browser-next-composer-resilience.test.mjs' in browser
    assert browser.count("/run/composer-e2e-client.key:ro,z") == 1
    assert 'run_browser_test "$expiry_application_name"' in runner
    assert runner.count('run_browser_test "$application_name"') == 35
    assert runner.count('run_browser_test "$expiry_application_name"') == 2
    assert runner.count("node --test") == 1
    assert "node --test /e2e/browser-" not in runner


@pytest.mark.unit
def test_opt_in_browser_runner_smoke_is_terminal_and_retains_exact_evidence() -> None:
    runner = _runner_text()
    fixture = BROWSER_RUNNER_SMOKE.read_text(encoding="utf-8")
    assert (
        'browser_runner_smoke_only="${MARKWEAVE_E2E_BROWSER_RUNNER_SMOKE_ONLY:-0}"'
        in runner
    )
    assert (
        '"$browser_runner_smoke_only" != 0 && "$browser_runner_smoke_only" != 1'
        in runner
    )
    initial_ready = runner.index(
        'wait_for_url "$base_url/health/ready"', runner.index("application_mode=serve")
    )
    smoke = runner.index('if [[ "$browser_runner_smoke_only" == 1 ]]', initial_ready)
    service = runner.index('if [[ "$composer_scenario_only" == 1 ]]', smoke)
    assert runner[smoke:service] == (
        'if [[ "$browser_runner_smoke_only" == 1 ]]; then\n'
        "  e2e_diagnostic_browser_runner_smoke\nfi\n"
    )
    phase = _function(runner, "e2e_diagnostic_browser_runner_smoke")
    assert (
        phase.index("start_frontend")
        < phase.index('start_production_router "$application_name"')
        < phase.index(
            'run_browser_test "$application_name" /e2e/browser-runner-smoke.mjs'
        )
    )
    assert (
        'test -s "$temporary_directory/browser-artifacts/browser-runner-smoke.png"'
        in phase
    )
    assert (
        'test -s "$temporary_directory/browser-artifacts/browser-runner-smoke-cgroup-001.txt"'
        in phase
    )
    assert '"$artifact_directory/"' in phase
    assert "browser_smoke_succeeded=true\nsucceeded=true" in phase
    assert phase.rstrip().endswith("exit 0")
    cleanup = runner.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    assert '"$succeeded" == true && "$browser_smoke_succeeded" != true' in cleanup
    assert "await page.goto(`${baseURL}/login`" in fixture
    assert "assert.equal(response?.status(), 200)" in fixture
    assert 'assert.equal(await page.title(), "Markweave")' in fixture
    assert '"content-security-policy"' in fixture
    assert "await page.screenshot({ path: screenshot, fullPage: true })" in fixture
    assert "assert.ok((await stat(screenshot)).size > 0)" in fixture


@pytest.mark.unit
def test_composer_scenario_replays_real_browser_with_canonical_runtime() -> None:
    runner = _runner_text()
    assert (
        'composer_scenario_only="${MARKWEAVE_E2E_COMPOSER_SCENARIO_ONLY:-0}"' in runner
    )
    assert '"$composer_scenario_only" != 0 && "$composer_scenario_only" != 1' in runner
    assert (
        '"$browser_runner_smoke_only" == 1 && "$composer_scenario_only" == 1' in runner
    )
    initial_ready = runner.index(
        'wait_for_url "$base_url/health/ready"', runner.index("application_mode=serve")
    )
    scenario = runner.index('if [[ "$composer_scenario_only" == 1 ]]', initial_ready)
    service = runner.index(
        'bash "$repository/scripts/container/wait-for-fake-clamav.sh"',
        runner.index("\nfi\n", scenario),
    )
    assert runner[scenario:service] == (
        'if [[ "$composer_scenario_only" == 1 ]]; then\n'
        "  e2e_diagnostic_composer_scenario\nfi\n"
    )
    phase = _function(runner, "e2e_diagnostic_composer_scenario")
    assert phase.index('podman rm --force "$application_name"') < phase.index(
        "start_frontend"
    )
    assert phase.index(
        '"${hardened_runtime[@]}" "${application_volumes[@]}" "${application_settings[@]}"'
    ) < phase.index("--env MARKWEAVE_PUBLIC_ORIGIN=http://localhost:3100")
    assert phase.index('wait_for_url "http://127.0.0.1:$(podman port') < phase.index(
        'start_production_router "$application_name"'
    )
    assert (
        phase.index('start_production_router "$application_name"')
        < phase.index(
            'run_browser_test "$application_name" /e2e/browser-next-composer-connections.test.mjs'
        )
        < phase.index(
            'run_browser_test "$application_name" /e2e/browser-next-composer-real.test.mjs'
        )
        < phase.index(
            'run_browser_test "$application_name" /e2e/browser-next-composer-pairing.test.mjs'
        )
    )
    for environment in (
        "--env MARKWEAVE_E2E_BASE_URL=http://localhost:3100",
        '--env MARKWEAVE_E2E_PROFILE="$profile"',
        '--env "MARKWEAVE_E2E_COMPOSER_PROVIDER_ADDRESS=$composer_provider_address"',
        '--env "MARKWEAVE_E2E_COMPOSER_STATE=/browser-session/composer-$profile.json"',
    ):
        assert environment in phase
    assert (
        'test -s "$temporary_directory/browser-artifacts/browser-next-composer-real-cgroup-001.txt"'
        in phase
    )
    for browser in ("connections", "pairing"):
        assert (
            'test -s "$temporary_directory/browser-artifacts/browser-next-composer-'
            f'{browser}-cgroup-001.txt"' in phase
        )
    assert 'test -s "$browser_session_directory/composer-$profile.json"' in phase
    assert (
        'cp -a -- "$temporary_directory/browser-artifacts/." "$artifact_directory/"'
        in phase
    )
    assert (
        'cp -a -- "$browser_session_directory/composer-$profile.json" "$artifact_directory/"'
        in phase
    )
    assert 'podman logs "$resource" >"$artifact_directory/$resource.log"' in phase
    assert "composer_scenario_succeeded=true\nsucceeded=true" in phase
    assert phase.rstrip().endswith("exit 0")
    cleanup = runner.split("cleanup() {", 1)[1].split("\ntrap cleanup EXIT", 1)[0]
    assert '"$composer_scenario_succeeded" != true' in cleanup
    assert (
        runner.count(
            'run_browser_test "$application_name" /e2e/browser-next-composer-real.test.mjs'
        )
        == 2
    )
    canonical = runner.rindex(
        'run_browser_test "$application_name" /e2e/browser-next-composer-real.test.mjs'
    )
    assert scenario < service < canonical


@pytest.mark.unit
@pytest.mark.parametrize(
    ("scenario_mode", "smoke_mode", "message"),
    [
        ("yes", "0", "MARKWEAVE_E2E_COMPOSER_SCENARIO_ONLY must be 0 or 1."),
        (
            "1",
            "1",
            "Browser runner smoke and Composer scenario modes are mutually exclusive.",
        ),
    ],
)
def test_composer_scenario_rejects_invalid_modes_before_runtime_setup(
    scenario_mode: str, smoke_mode: str, message: str
) -> None:
    result = subprocess.run(
        [str(RUNNER), "standalone"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_E2E_COMPOSER_SCENARIO_ONLY": scenario_mode,
            "MARKWEAVE_E2E_BROWSER_RUNNER_SMOKE_ONLY": smoke_mode,
        },
    )
    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.unit
def test_composer_browser_mounts_only_checksum_verified_small_corpus(
    tmp_path: Path,
) -> None:
    runner = _runner_text()
    stage = runner.split("for corpus_file in docx/text.docx pdf/text.pdf; do", 1)[
        1
    ].split("\ndone", 1)[0]
    stage = "for corpus_file in docx/text.docx pdf/text.pdf; do" + stage + "\ndone"
    repository = tmp_path / "repository"
    corpus = repository / "spikes" / "anydoc" / "corpus"
    for filename in ("docx/text.docx", "pdf/text.pdf"):
        source = corpus / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(filename.encode())
    temporary_directory = tmp_path / "harness"
    temporary_directory.mkdir()
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'repository="$1"\n'
            'temporary_directory="$2"\n'
            'composer_corpus_directory="$temporary_directory/composer-corpus"\n'
            + stage,
            "composer-corpus-stage",
            str(repository),
            str(temporary_directory),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    staged = temporary_directory / "composer-corpus"
    assert {
        path.relative_to(staged).as_posix()
        for path in staged.rglob("*")
        if path.is_file()
    } == {
        "docx/text.docx",
        "pdf/text.pdf",
    }
    for filename in ("docx/text.docx", "pdf/text.pdf"):
        assert (staged / filename).read_bytes() == (corpus / filename).read_bytes()
    browser = runner.split("run_browser_test() {", 1)[1].split(
        "\nstart_production_router() {", 1
    )[0]
    real = browser.split(
        'if [[ "$test_file" == /e2e/browser-next-composer-real.test.mjs ]]; then', 1
    )[1].split("\n  elif", 1)[0]
    typed = browser.split(
        'elif [[ "$test_file" == /e2e/browser-next-composer-typed.test.mjs ]]; then', 1
    )[1].split("\n  elif", 1)[0]
    assert '--volume "$composer_corpus_directory:/spikes/anydoc/corpus:ro,z"' in real
    assert '--volume "$composer_corpus_directory:/spikes/anydoc/corpus:ro,z"' in typed
    assert "/run/composer-e2e-client.key" not in typed
    assert browser.count("/spikes/anydoc/corpus:ro,z") == 2
    assert "$repository/spikes/anydoc/corpus:/spikes/anydoc/corpus" not in browser
    shared = runner.split("application_volumes=(", 1)[1].split(
        "\napplication_mode=serve", 1
    )[0]
    assert "/spikes/anydoc/corpus" not in shared


@pytest.mark.unit
def test_repeated_browser_phases_reserve_distinct_cgroup_receipts(
    tmp_path: Path,
) -> None:
    runner = _runner_text()
    reservation = (
        "reserve_browser_cgroup_receipt() {"
        + runner.split("reserve_browser_cgroup_receipt() {", 1)[1].split(
            "\nrun_browser_test() {", 1
        )[0]
    )
    (tmp_path / "browser-artifacts").mkdir()
    script = (
        'temporary_directory="$1"\n'
        + reservation
        + "\nreserve_browser_cgroup_receipt browser-next-composer-resilience\n"
        + "reserve_browser_cgroup_receipt browser-next-composer-resilience\n"
        + "reserve_browser_cgroup_receipt browser-next-reversion\n"
    )
    result = subprocess.run(
        ["bash", "-c", script, "browser-receipts", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "/browser-artifacts/browser-next-composer-resilience-cgroup-001.txt",
        "/browser-artifacts/browser-next-composer-resilience-cgroup-002.txt",
        "/browser-artifacts/browser-next-reversion-cgroup-001.txt",
    ]
    assert len(list((tmp_path / "browser-artifacts").glob("*.reserved"))) == 3


@pytest.mark.unit
def test_smoke_receipt_stem_matches_the_branch_evidence_path(tmp_path: Path) -> None:
    runner = _runner_text()
    reservation = (
        "reserve_browser_cgroup_receipt() {"
        + runner.split("reserve_browser_cgroup_receipt() {", 1)[1].split(
            "\nrun_browser_test() {", 1
        )[0]
    )
    start = runner.index('  test_stem="${test_stem%.mjs}"')
    end = runner.index("  if ! receipt_path=", start)
    normalization = runner[start:end]
    (tmp_path / "browser-artifacts").mkdir()
    script = (
        'temporary_directory="$1"\n'
        + reservation
        + "\ntest_file=/e2e/browser-runner-smoke.mjs\n"
        + 'test_stem="${test_file##*/}"\n'
        + normalization
        + 'reserve_browser_cgroup_receipt "$test_stem"\n'
        + "test_file=/e2e/browser-next-composer-real.test.mjs\n"
        + 'test_stem="${test_file##*/}"\n'
        + normalization
        + 'reserve_browser_cgroup_receipt "$test_stem"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "browser-receipts", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    smoke_receipt, normal_receipt = result.stdout.splitlines()
    assert smoke_receipt == "/browser-artifacts/browser-runner-smoke-cgroup-001.txt"
    assert normal_receipt == (
        "/browser-artifacts/browser-next-composer-real-cgroup-001.txt"
    )
    smoke_branch = _function(runner, "e2e_diagnostic_browser_runner_smoke")
    assert f'test -s "$temporary_directory{smoke_receipt}"' in smoke_branch


@pytest.mark.unit
def test_composer_final_image_provider_and_restore_preserve_harness_guards() -> None:
    runner = _runner_text()
    provider_start = runner.index(
        'podman run --detach --name "$composer_provider_name"'
    )
    provider_block = runner[
        runner.rfind("e2e_run_in_harness_directory", 0, provider_start) : runner.index(
            "composer_provider_ready=false", provider_start
        )
    ]
    assert '"$temporary_directory" "$temporary_directory_identity"' in provider_block
    assert '--network "$network_name"' in provider_block
    assert '--user "$runtime_uid:0" --read-only' in provider_block
    assert "--cap-drop=all --security-opt=no-new-privileges" in provider_block
    assert '--volume "$composer_provider_directory:/provider:ro,z"' in provider_block

    restore = runner.split("restore_composer_snapshot() {", 1)[1].split(
        "\nkill_backend_and_reconnect_router() {", 1
    )[0]
    assert restore.index('podman rm --force "$router_name"') < restore.index(
        'podman stop --time 15 "$application_name"'
    )
    assert restore.index(
        'podman unshare cmp -- "$temporary_directory/composer-key-backup"'
    ) < restore.index('podman start "$application_name"')
    assert restore.index('podman start "$application_name"') < restore.index(
        'start_production_router "$application_name"'
    )

    composer_browser = runner.index(
        'run_browser_test "$application_name" /e2e/browser-next-composer-real.test.mjs',
        provider_start,
    )
    pairing_browser = runner.index(
        "/e2e/browser-next-composer-pairing.test.mjs", composer_browser
    )
    restore_call = runner.index("\nrestore_composer_snapshot\n")
    restored_browser = runner.index(
        "MARKWEAVE_E2E_COMPOSER_PHASE=restored-backup", restore_call
    )
    assert (
        provider_start
        < composer_browser
        < pairing_browser
        < restore_call
        < restored_browser
    )
    assert runner.count("/e2e/browser-next-composer-pairing.test.mjs") == 2


@pytest.mark.unit
def test_next_browser_matrix_uses_the_paired_production_router_image() -> None:
    runner = _runner_text()

    assert "frontend-auth-router.mjs" not in runner
    assert "routing-fixture.mjs" not in runner
    assert (
        'readonly published_frontend_image="${MARKWEAVE_E2E_FRONTEND_IMAGE:-}"'
        in runner
    )
    assert (
        "MARKWEAVE_E2E_IMAGE and MARKWEAVE_E2E_FRONTEND_IMAGE must be supplied together"
        in runner
    )
    assert (
        "MARKWEAVE_E2E_LOCAL_IMAGE and MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE must be supplied together"
        in runner
    )
    assert "Published and local E2E image pairs are mutually exclusive" in runner
    assert (
        "Published backend, frontend and reverse-attempt E2E image versions must match"
        in runner
    )
    assert (
        "Local backend, frontend and reverse-attempt E2E image versions must match"
        in runner
    )
    assert 'podman image exists "$image"' in runner
    assert 'podman image exists "$frontend_image"' in runner
    assert '"$frontend_image" node router.mjs' in runner
    assert 'local backend_origin="${2:-http://127.0.0.1:8080}"' in runner
    assert 'local frontend_origin="${3:-}"' in runner
    assert 'local expected_api_status="${4:-401}"' in runner
    assert 'local probe_page="${5:-true}"' in runner
    assert "AbortSignal.timeout(1000)" in runner
    assert "--env PUBLIC_HOSTS=localhost:3100" in runner
    assert "--env ROUTER_UPSTREAM_TIMEOUT_MS=30000" in runner
    assert runner.count('start_production_router "$application_name"') == 11
    assert runner.count('restart_backend_and_router "$application_name"') == 1
    assert runner.count('kill_backend_and_reconnect_router "$application_name"') == 1
    assert runner.count('start_production_router "$expiry_application_name"') == 1
    provisioning = runner.index("/e2e/browser-provisioning-restart.test.mjs")
    provisioning_end = runner.index("/e2e/browser-recovery-checkpoint.test.mjs")
    first_router = runner.index("\ne2e_phase_browser_session() {\n")
    browser_runtime = _function(runner, "use_browser_runtime")
    assert browser_runtime.index("start_application") < browser_runtime.index(
        'start_production_router "$application_name"'
    )
    assert (
        first_router
        < runner.index("\nuse_browser_runtime\n", first_router)
        < provisioning
    )
    assert (
        "MARKWEAVE_E2E_BASE_URL=http://localhost:3100"
        in runner[provisioning:provisioning_end]
    )
    assert (
        'start_production_router "$application_name" http://127.0.0.1:1 \\\n'
        '  "$(admission_frontend_origin)" 502' in runner
    )
    assert "MARKWEAVE_E2E_RUNTIME_FAILURE=frontend-outage" in runner
    assert "MARKWEAVE_E2E_RUNTIME_FAILURE=backend-outage" in runner
    assert "MARKWEAVE_E2E_RUNTIME_FAILURE=admission" in runner
    assert 'podman kill --signal TERM "$frontend_name"' in runner
    assert "/e2e/frontend-admission-fixture.mjs" in runner
    fixture_started = runner.index("/e2e/frontend-admission-fixture.mjs")
    fixture_ready = runner.index(
        '[[ -f "$evidence_directory/frontend-admission-ready" ]] && break'
    )
    admission_router = runner.index(
        'start_production_router "$application_name" http://127.0.0.1:8080 \\\n'
        '  "$admission_origin" 401 false'
    )
    assert fixture_started < fixture_ready < admission_router
    assert 'echo "Timed out waiting for the admission frontend." >&2' in runner
    assert (
        'e2e_podman logs "$frontend_name" >&2 || true'
        in runner[fixture_ready:admission_router]
    )
    admission_started = runner.index("admission_test_pid=$!")
    admission_finished = runner.index('wait "$admission_test_pid"')
    admission_supervision = runner[admission_started:admission_finished]
    assert "for _ in $(seq 1 1200); do" in admission_supervision
    assert "for _ in $(seq 1 200); do" not in admission_supervision
    assert 'podman logs "$router_name" >&2 || true' in runner
    assert 'podman logs "$frontend_name" >&2 || true' in runner
    assert 'podman restart --time 15 "$application_name"' not in runner[first_router:]


@pytest.mark.unit
def test_router_uses_current_numeric_frontend_address_during_scanner_outage() -> None:
    runner = _runner_text()
    router = runner.split("start_production_router() {", 1)[1].split(
        "\nstart_frontend() {", 1
    )[0]
    assert router.index('frontend_origin="$(admission_frontend_origin)"') < (
        router.index('podman run --detach --name "$router_name"')
    )
    assert '--env "FRONTEND_ORIGIN=$frontend_origin"' in router
    assert "http://frontend:3000" not in router
    assert (
        'start_production_router "$application_name" http://127.0.0.1:1 \\\n'
        '  "$(admission_frontend_origin)" 502' in runner
    )

    outage_probe = runner.split("probe_scanner_outage_routes() {", 1)[1].split(
        "\nrequire_reserved_address() {", 1
    )[0]
    assert '["frontend_alias", "http://frontend:3000/login"]' in outage_probe
    assert '["frontend_numeric", `${process.env.E2E_FRONTEND_ORIGIN}/login`]' in (
        outage_probe
    )
    assert (
        'const required = new Set(["router_login", "frontend_numeric", '
        '"backend_ready"]);'
    ) in outage_probe
    assert 'required.has(name) && status !== "200"' in outage_probe


@pytest.mark.unit
def test_admission_phase_uses_only_the_revalidated_numeric_frontend_origin() -> None:
    runner = _runner_text()
    fixture_ready = runner.index(
        '[[ -f "$evidence_directory/frontend-admission-ready" ]] && break'
    )
    origin = runner.index('admission_origin="$(admission_frontend_origin)"')
    router = runner.index(
        'start_production_router "$application_name" http://127.0.0.1:8080 \\\n'
        '  "$admission_origin" 401 false'
    )
    helper = runner.split("admission_frontend_origin() {", 1)[1].split(
        "\nrestart_backend_and_router() {", 1
    )[0]

    assert fixture_ready < origin < router
    assert 'podman inspect "$frontend_name"' in helper
    assert 'index .NetworkSettings.Networks \\"$network_name\\"' in helper
    assert "podman network inspect" not in helper
    assert "10#$octet > 255" in helper
    assert "printf 'http://%s:3000\\n'" in helper
    assert runner.count("http://frontend:3000 401 false") == 0


@pytest.mark.unit
def test_admission_fixture_reports_only_listening_readiness() -> None:
    source = ADMISSION_FIXTURE.read_text(encoding="utf-8")

    listen = source.index('page.server.listen(3000, "0.0.0.0", () => {')
    ready = source.index("frontend-admission-ready")
    assert listen < ready
    assert 'page.server.listen(3000, "0.0.0.0");' not in source


@pytest.mark.unit
def test_admission_browser_waits_for_every_frontend_admission() -> None:
    source = RUNTIME_FAILURE_BROWSER.read_text(encoding="utf-8")
    fixture = ADMISSION_FIXTURE.read_text(encoding="utf-8")

    assert 'import http from "node:http";' in source
    assert "const request = http.request(" in source
    assert "`${baseURL}/hold`" in source
    assert "{ agent: false }" in source
    assert "const admissionTimeoutMs = 25_000;" in source
    assert "const deadline = Date.now() + admissionTimeoutMs;" in source
    assert "while (Date.now() < deadline)" in source
    assert "const failed = requests.find(" in source
    assert "if (failed) throw failed.admissionError;" in source
    assert 'existsSync("/evidence/frontend-saturated")' in source
    assert "Timed out waiting for frontend saturation" in source
    assert (
        "Frontend hold request ${admissionId} returned HTTP "
        "${response.statusCode} before saturation"
    ) in source
    assert "Frontend hold request ${admissionId} failed before saturation" in source
    assert "await waitForSaturation(held);" in source
    assert "socket.once" not in source
    assert "if (page.admission.inFlight === 128)" in fixture
    assert "if (page.admission.inFlight > admissionHighWater)" in fixture
    assert "admissionHighWater = page.admission.inFlight;" in fixture
    assert "frontend-admission-high-water" in fixture
    assert 'frontend-admission-high-water`, "0\\n"' in fixture
    assert 'writeFileSync(`${evidence}/frontend-saturated`, "128\\n"' in fixture
    assert "response.writeHead(200);" not in fixture
    assert "response.flushHeaders();" not in fixture
    assert 'response.write("admitted\\n");' not in fixture
    assert fixture.index("frontend-saturated") < fixture.index(
        "frontend-admission-ready"
    )
    assert source.index("await waitForSaturation(held);") < source.index(
        'assert.equal(existsSync("/evidence/frontend-saturated"), true);'
    )
    assert "} finally {" in source
    assert "held.forEach(({ request }) => request.destroy());" in source
    assert "new AbortController()" not in source


@pytest.mark.unit
def test_failure_artifacts_retain_bounded_frontend_admission_evidence(
    tmp_path: Path,
) -> None:
    runner = _runner_text()
    collector = runner.split("collect_failure_artifacts() {", 1)[1].split(
        "\ncleanup() {", 1
    )[0]
    retention_body = runner.split("retain_frontend_admission_evidence() {", 1)[1].split(
        "\ncollect_failure_artifacts() {", 1
    )[0]
    retention = f"retain_frontend_admission_evidence() {{{retention_body}"

    assert "retain_frontend_admission_evidence" in collector
    assert collector.index("cp -a --") < collector.index(
        "retain_frontend_admission_evidence"
    )
    assert "frontend-admission-ready" in retention
    assert "frontend-admission-high-water" in retention
    assert "frontend-saturated" in retention
    assert 'head -c 16 -- "$evidence_directory/$evidence_name"' in retention
    assert '[[ "$evidence_value" =~ ^[0-9]{1,3}$ ]]' in retention
    assert "10#$evidence_value >= 1" not in retention
    assert "10#$evidence_value <= 128" in retention
    assert '[[ "$evidence_value" == 128 ]]' in retention
    assert "printf '%s\\n' \"$evidence_value\"" in retention

    evidence = tmp_path / "evidence"
    artifacts = tmp_path / "artifacts"
    evidence.mkdir()
    artifacts.mkdir()
    (evidence / "frontend-admission-ready").write_text("true\n", encoding="utf-8")
    (evidence / "frontend-admission-high-water").write_text("127\n", encoding="utf-8")
    (evidence / "frontend-saturated").write_text("129\n", encoding="utf-8")
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'evidence_directory="$1"\n'
            'artifact_directory="$2"\n'
            f"{retention}\n"
            "retain_frontend_admission_evidence",
            "bash",
            str(evidence),
            str(artifacts),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (artifacts / "frontend-admission-ready").read_text(encoding="utf-8") == (
        "true\n"
    )
    assert (artifacts / "frontend-admission-high-water").read_text(
        encoding="utf-8"
    ) == "127\n"
    assert not (artifacts / "frontend-saturated").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("backend", "frontend", "message"),
    [
        (
            "ghcr.io/guillaume-lombardo/md-converter:0.6.0@sha256:" + "a" * 64,
            "ghcr.io/guillaume-lombardo/md-converter-web:0.6.1@sha256:" + "b" * 64,
            "Published backend, frontend and reverse-attempt E2E image versions must match.",
        ),
        (
            "localhost/md-converter:0.6.0",
            "localhost/md-converter-web:0.6.1",
            "Local backend, frontend and reverse-attempt E2E image versions must match.",
        ),
    ],
)
def test_e2e_runner_rejects_mismatched_pair_versions(
    backend: str,
    frontend: str,
    message: str,
) -> None:
    prefix = "MARKWEAVE_E2E" if backend.startswith("ghcr.io") else "MARKWEAVE_E2E_LOCAL"
    result = subprocess.run(
        [str(RUNNER), "standalone"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            f"{prefix}_IMAGE": backend,
            f"{prefix}_FRONTEND_IMAGE": frontend,
            f"{prefix}_REVERSE_ATTEMPT_IMAGE": backend.replace(
                "md-converter:", "md-converter-reverse-attempt:"
            ),
        },
    )

    assert result.returncode == 2
    assert message in result.stderr


@pytest.mark.unit
def test_router_is_removed_before_every_backend_network_parent() -> None:
    runner = _runner_text()

    assert 'podman rm --force "$router_name" "$application_name"' not in runner
    assert 'podman rm --force "$router_name" "$expiry_application_name"' not in runner
    assert runner.count('podman rm --force "$router_name" >/dev/null') == 10
    cleanup = runner[runner.index("cleanup() {") : runner.index("trap cleanup EXIT")]
    assert cleanup.index('podman rm --force "$router_name"') < cleanup.index(
        'for resource in "${created[@]}"'
    )
    for helper in ("use_api_runtime", "use_browser_runtime", "use_held_runtime"):
        assert (
            'podman rm --force --ignore "$router_name" >/dev/null\n'
            '  podman rm --force --ignore "$application_name" >/dev/null'
        ) in _function(runner, helper)
    assert runner.count('podman rm --force --ignore "$application_name"') == 3
    assert (
        'podman rm --force "$router_name" >/dev/null\n'
        'podman rm --force "$expiry_application_name" "$clamav_name" >/dev/null'
        in runner
    )
    helper_start = runner.index("restart_backend_and_router() {")
    restart_helper = runner[
        helper_start : runner.index("\nremove_artifacts\n", helper_start)
    ]
    remove_index = restart_helper.index('podman rm --force "$router_name"')
    restart_index = restart_helper.index(
        'podman restart --time 15 "$backend_container"'
    )
    ready_index = restart_helper.index("wait_for_url")
    start_index = restart_helper.index('start_production_router "$backend_container"')
    assert remove_index < restart_index < ready_index < start_index

    kill_helper_start = runner.index("kill_backend_and_reconnect_router() {")
    kill_helper = runner[
        kill_helper_start : runner.index("\nremove_artifacts\n", kill_helper_start)
    ]
    remove_index = kill_helper.index('podman rm --force "$router_name"')
    kill_index = kill_helper.index('podman kill --signal KILL "$backend_container"')
    assert_exit_index = kill_helper.index(".State.ExitCode")
    backend_start_index = kill_helper.index('podman start "$backend_container"')
    ready_index = kill_helper.index("wait_for_url")
    router_start_index = kill_helper.index(
        'start_production_router "$backend_container"'
    )
    assert (
        remove_index
        < kill_index
        < assert_exit_index
        < backend_start_index
        < ready_index
        < router_start_index
    )


@pytest.mark.unit
def test_browser_recovery_uses_final_router_and_exact_forced_restart() -> None:
    runner = _runner_text()
    router_index = runner.index('start_production_router "$application_name"')
    checkpoint_index = runner.index("/e2e/browser-recovery-checkpoint.test.mjs")
    kill_index = runner.index(
        'kill_backend_and_reconnect_router "$application_name"', checkpoint_index
    )
    verify_index = runner.index("/e2e/browser-recovery.test.mjs", kill_index)

    assert router_index < checkpoint_index < kill_index < verify_index
    phase = runner[router_index:verify_index]
    assert phase.count("MARKWEAVE_E2E_BASE_URL=http://localhost:3100") >= 2
    assert "MARKWEAVE_E2E_BASE_URL=http://127.0.0.1:8080" not in runner


@pytest.mark.unit
def test_runner_invokes_next_conversion_browser_in_both_profile_matrix() -> None:
    runner = _runner_text()
    main_index = runner.index("/e2e/browser-next-conversion.test.mjs")
    failure_index = runner.index(
        "/e2e/browser-next-conversion-failure.test.mjs", main_index
    )
    admission_index = runner.index(
        "/e2e/browser-next-conversion-admission.test.mjs", failure_index
    )
    preparation_index = runner.index(
        "/e2e/browser-next-conversion-restart-prepare.test.mjs", admission_index
    )
    preparation_command_index = runner.rindex(
        'run_browser_test "$application_name"', admission_index, preparation_index
    )
    restarted_router_index = runner.index(
        'restart_backend_and_router "$application_name"', preparation_index
    )
    recovery_index = runner.index(
        "/e2e/browser-next-conversion-restart.test.mjs", restarted_router_index
    )
    short_lifetime_index = runner.index("MARKWEAVE_SESSION_ABSOLUTE_SECONDS=2")
    expiry_index = runner.index(
        "/e2e/browser-next-conversion-expiry.test.mjs", short_lifetime_index
    )

    assert runner.count("/e2e/browser-next-conversion.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-failure.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-admission.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-restart-prepare.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-restart.test.mjs") == 1
    assert runner.count("/e2e/browser-next-conversion-expiry.test.mjs") == 1
    assert runner.count("MARKWEAVE_E2E_CONVERSION_STATE=") == 3
    assert preparation_index < restarted_router_index < recovery_index
    preparation = runner[preparation_command_index:restarted_router_index]
    assert (
        'run_browser_test "$application_name" '
        "/e2e/browser-next-conversion-restart-prepare.test.mjs" in preparation
    )
    assert (
        runner.index("/e2e/browser-next-auth.test.mjs")
        < main_index
        < failure_index
        < admission_index
        < preparation_index
        < restarted_router_index
        < recovery_index
        < short_lifetime_index
        < expiry_index
    )


@pytest.mark.unit
def test_restart_checkpoint_is_fresh_and_authoritative() -> None:
    preparation = RESTART_PREPARATION_BROWSER.read_text(encoding="utf-8")
    recovery = RESTART_BROWSER.read_text(encoding="utf-8")

    assert 'assert.equal(authoritative.body.state, "succeeded")' in preparation
    assert "Date.parse(authoritative.body.expires_at) > Date.now()" in preparation
    assert "expires_at: authoritative.body.expires_at" in preparation
    assert "job_id: job.id" in preparation
    assert "Date.parse(state.expires_at) > Date.now()" in recovery
    assert "fresh recovery checkpoint expired before application restart" in recovery


@pytest.mark.unit
def test_runner_invokes_next_administration_with_restored_policy_evidence(
    tmp_path: Path,
) -> None:
    runner = _runner_text()
    service = _function(runner, "e2e_phase_service")
    restore_index = service.index("# Prove that an isolated snapshot restores")
    assert (
        service.index("tests.e2e.service_workflow checkpoint")
        < restore_index
        < service.index("tests.e2e.service_workflow verify-checkpoint", restore_index)
    )
    assert "/e2e/browser-next-auth.test.mjs" in _function(
        runner, "e2e_phase_browser_session"
    )
    assert "/e2e/browser-next-conversion-failure.test.mjs" in _function(
        runner, "e2e_phase_browser_workspaces"
    )
    # Without the service phase, the browser shard seeds the same checkpoint.
    workspaces = _function(runner, "e2e_phase_browser_workspaces")
    assert workspaces.startswith(
        "if ! e2e_selects_phase service; then\n  e2e_seed_browser_prerequisites\nfi\n"
    )
    assert "seed-browser-prerequisites" in _function(
        runner, "e2e_seed_browser_prerequisites"
    )
    runtime = _function(runner, "e2e_phase_browser_runtime")
    admission_index = runtime.index("/e2e/browser-next-conversion-admission.test.mjs")
    recovery_index = runtime.index(
        "/e2e/browser-next-conversion-restart.test.mjs", admission_index
    )
    policy_values_index = runtime.index("read_checkpoint_policy_values", recovery_index)
    admin_cookie_index = runtime.index(
        "/e2e/browser-next-admin-cookie.test.mjs", recovery_index
    )
    admin_index = runtime.index("/e2e/browser-next-admin.test.mjs", recovery_index)
    expiry_index = runtime.index("/e2e/browser-next-auth-expiry.test.mjs", admin_index)

    assert runner.count("/e2e/browser-next-admin-cookie.test.mjs") == 1
    assert runner.count("/e2e/browser-next-admin.test.mjs") == 1
    assert (
        admission_index
        < recovery_index
        < policy_values_index
        < admin_cookie_index
        < admin_index
        < expiry_index
    )
    reader = _function(runner, "read_checkpoint_policy_values")
    assert '"policy_user_idle_minutes"' in reader
    assert '"policy_admin_idle_minutes"' in reader
    assert '"policy_revision"' in reader
    assert "value.isascii() and value.isdecimal()" in reader
    invocation = runtime[recovery_index:expiry_index]
    assert (
        'run_browser_test "$application_name" '
        "/e2e/browser-next-admin-cookie.test.mjs\n"
        'run_browser_test "$application_name" '
        "/e2e/browser-next-admin.test.mjs \\\n"
        "  --env MARKWEAVE_E2E_PROFILE=" in invocation
    )
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

    extraction_start = reader.index("import json")
    extraction_end = reader.index('\n\' "$state_file"', extraction_start)
    extraction = reader[extraction_start:extraction_end]
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
    runner = _runner_text()
    probe_function = runner.index("wait_for_embedded_worker_idle()")
    probe_start = runner.index("from pathlib import Path", probe_function)
    probe_end = runner.index("\n'\n}", probe_start)
    compile(runner[probe_start:probe_end], str(RUNNER), "exec")
    runtime = _function(runner, "e2e_phase_browser_runtime")
    admission_index = runtime.index("/e2e/browser-next-conversion-admission.test.mjs")
    assert runtime.startswith("use_held_runtime\n")
    phase = _function(runner, "use_held_runtime")

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
    start = _function(runner, "start_application")
    assert start.index('"${application_settings[@]}"') < start.index('"$@"')
    assert phase.index("start_application") < phase.index(
        "MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER=2"
    )
    assert phase.index('podman stop --time 15 "$worker_one_name"') < phase.index(
        "start_application"
    )
    restore = runtime[
        admission_index : runtime.index(
            'restart_backend_and_router "$application_name"', admission_index
        )
    ]
    assert "\nuse_browser_runtime\n" in restore
    browser_runtime = _function(runner, "use_browser_runtime")
    assert 'podman start "$worker_one_name" "$worker_two_name"' in browser_runtime
    assert browser_runtime.count("start_application") == 1
    assert start.count('"${application_settings[@]}"') == 1


@pytest.mark.unit
@pytest.mark.parametrize("suffix", ["IMAGE", "FRONTEND_IMAGE", "REVERSE_ATTEMPT_IMAGE"])
def test_e2e_runner_requires_complete_reverse_image_set(suffix: str) -> None:
    result = subprocess.run(
        [str(RUNNER), "standalone"],
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            f"MARKWEAVE_E2E_LOCAL_{suffix}": "localhost/"
            + {
                "IMAGE": "md-converter",
                "FRONTEND_IMAGE": "md-converter-web",
                "REVERSE_ATTEMPT_IMAGE": "md-converter-reverse-attempt",
            }[suffix]
            + ":test",
        },
    )
    assert result.returncode == 2
    assert "supplied together" in result.stderr


@pytest.mark.unit
def test_reverse_broker_stays_host_native_and_pinned() -> None:
    runner = RUNNER.read_text()
    assert (
        'python -m markweave.broker.process "$broker_directory/broker.json"' in runner
    )
    assert 'scripts.e2e.reverse_broker sweep --root "$broker_directory"' in runner
    assert '"$broker_directory/worker:/run/reverse-client:ro,z"' in runner
    assert (
        'podman unshare chown -R "$runtime_uid:0" "$broker_directory/worker"' in runner
    )
    assert (
        'scripts/container/build-reverse-attempt.sh "$reverse_attempt_image"' in runner
    )
    assert "tests.e2e.reverse_cli_workflow" in runner
    assert "/run/podman/podman.sock" not in runner


@pytest.mark.unit
def test_reverse_cleanup_requires_worker_absence_before_inventory_sweep() -> None:
    runner = RUNNER.read_text()
    cleanup = runner[runner.index("cleanup() {") : runner.index("trap cleanup EXIT")]
    assert cleanup.index("require_reverse_workers_removed") < cleanup.index(
        "scripts.e2e.reverse_broker sweep"
    )
    assert 'if [[ "$broker_cleanup_proven" == true ]]' in cleanup
    assert '"$status" -ne 1' in runner


@pytest.mark.unit
def test_one_reverse_supervisor_owns_each_host_broker_principal() -> None:
    runner = RUNNER.read_text()
    assert 'if [[ "$worker" == "$worker_one_name" ]]' in runner
    assert "worker_reverse_runtime=()" in runner
    assert '"${worker_reverse_runtime[@]}"' in runner
    volumes = runner[
        runner.index("application_volumes=(") : runner.index("application_mode=serve")
    ]
    assert volumes.index('if [[ "$profile" == standalone ]]') < volumes.index(
        '"${reverse_worker_runtime[@]}"'
    )


@pytest.mark.unit
def test_reverse_full_matrix_runs_outside_primary_diagnostic() -> None:
    runner = _runner_text()
    diagnostic = runner.index(
        'if [[ "${MARKWEAVE_E2E_REVERSE_PRIMARY_ONLY:-false}" == true ]]; then',
        runner.index("\ne2e_phase_reverse_api() {\n"),
    )
    corpus = runner.index("tests.e2e.reverse_corpus_workflow")
    structured = runner.index("tests.e2e.structured_pptx_workflow")
    structured_cli = runner.index("--phase structured-pptx")
    structured_browser = runner.index("MARKWEAVE_E2E_REVERSE_PHASE=structured")
    assert diagnostic < runner.index("exit 0", diagnostic) < corpus < structured
    assert structured < structured_cli < structured_browser
    assert runner.count("/e2e/browser-next-reversion.test.mjs") == 4
    assert "--env MARKWEAVE_E2E_REVERSE_PHASE=primary" in _function(
        runner, "e2e_phase_browser_workspaces"
    )
    runtime = _function(runner, "e2e_phase_browser_runtime")
    held = runtime.index("use_held_runtime")
    admission = runtime.index("--env MARKWEAVE_E2E_REVERSE_PHASE=admission")
    assert held < runtime.index("--reverse-held-queue") < admission
    assert "--env MARKWEAVE_WORKER_IDLE_POLL_SECONDS=600" in _function(
        runner, "use_held_runtime"
    )
    assert "--env MARKWEAVE_TEST_CLAMAV_REJECT_EICAR=true" in runner


@pytest.mark.unit
def test_reverse_fault_injection_waits_for_bound_pause_and_joins_observers() -> None:
    runner = RUNNER.read_text()
    lifecycle = runner[
        runner.index("run_reverse_lifecycle() {") : runner.index(
            "\nremove_artifacts\nmkdir -p"
        )
    ]
    assert lifecycle.index('wait_reverse_marker "$barrier"') < lifecycle.index(
        'podman kill --signal KILL "$runtime"'
    )
    assert 'chmod 0644 "/browser-session/$stem-binding.json"' not in lifecycle
    assert 'touch "${binding%.json}.ready"' not in lifecycle
    assert '--output-ready-marker "/browser-session/$stem-binding.ready"' in lifecycle
    assert (
        '--diagnostics "$temporary_directory/browser-artifacts/'
        '$stem-pause-state.json"' in lifecycle
    )
    verification = lifecycle[lifecycle.index("reverse_lifecycle_workflow verify") :]
    assert verification.index('chmod 0644 "$state"') < verification.index(
        'podman exec "$application_name"'
    )
    assert 'runtime="$worker_one_name"' in lifecycle
    assert lifecycle.index('podman pause "$runtime"') < lifecycle.index(
        "reverse_lifecycle_workflow prepare"
    )
    assert (
        lifecycle.index('wait_reverse_marker "$diagnostics_watching"')
        < lifecycle.index('wait_reverse_marker "${barrier%.json}.ready"')
        < lifecycle.index('podman unpause "$runtime"')
    )
    assert (
        lifecycle.index('wait_reverse_marker "${barrier%.json}.ready"')
        < lifecycle.index('podman unpause "$runtime"')
        < lifecycle.index('wait_reverse_marker "$barrier"')
    )
    assert '--ready-marker "/browser-session/$stem-binding-watching.json"' in lifecycle
    assert 'podman stop --time 15 "$runtime"' not in lifecycle
    assert "md_converter_reversion_broker_ready 0" in lifecycle
    cleanup = runner[runner.index("cleanup() {") : runner.index("trap cleanup EXIT")]
    assert '"$reverse_pause_pid" "$reverse_diagnostics_pid"' in cleanup
    assert 'if [[ -f "$broker_directory/broker.json" ]]' in cleanup


@pytest.mark.unit
def test_reverse_frontend_outage_reuses_bound_broker_recovery() -> None:
    runner = RUNNER.read_text()
    lifecycle = runner[
        runner.index("run_reverse_lifecycle() {") : runner.index(
            "\nremove_artifacts\nmkdir -p"
        )
    ]
    assert (
        'local lifecycle_url="http://127.0.0.1:$(podman port "$application_name"'
        in lifecycle
    )
    assert (
        lifecycle.index('wait_reverse_marker "$barrier"')
        < lifecycle.index('kill --signal KILL "$frontend_name"')
        < lifecycle.index('--parent-pid "$broker_pid" --signal KILL')
    )
    outage = runner.index("run_reverse_lifecycle broker-restart true")
    unavailable = runner.index(
        "--env MARKWEAVE_E2E_RUNTIME_FAILURE=frontend-outage", outage
    )
    restored = runner.index("start_frontend", unavailable)
    browser = runner.index("--env MARKWEAVE_E2E_REVERSE_PHASE=recovered", restored)
    assert outage < unavailable < restored < browser
    assert runner.count("run_reverse_lifecycle broker-restart") == 1
    assert '--diagnostics-file "$diagnostics" --result-receipt "$receipt"' in lifecycle
    assert 'chmod 0644 "$receipt"' in lifecycle
    assert (
        "--env MARKWEAVE_E2E_REVERSE_RESULT_RECEIPT=/browser-session/reverse-broker-restart-result.json"
        in runner
    )


@pytest.mark.unit
def test_reverse_broker_crash_signals_verified_child_not_uv_wrapper() -> None:
    runner = RUNNER.read_text()
    assert 'kill -KILL "$broker_pid"' not in runner
    assert '--parent-pid "$broker_pid" --signal KILL' in runner
    assert 'wait "$broker_pid" || broker_exit=$?' in runner
    assert 'test "$broker_exit" = 137' in runner
    assert 'wait "$broker_pid" || test "$?" = 137' not in runner


@pytest.mark.unit
def test_runner_invokes_local_image_packaging_in_both_profiles() -> None:
    """T104 browser and CLI packaging run once per final-image profile."""
    runner = _runner_text()
    browser = Path("tests/e2e/browser-next-local-images.test.mjs").read_text(
        encoding="utf-8"
    )
    workflow = Path("tests/e2e/conversion_cli_workflow.py").read_text(encoding="utf-8")
    index = runner.index("/e2e/browser-next-local-images.test.mjs")

    assert runner.count("/e2e/browser-next-local-images.test.mjs") == 1
    assert runner.index("/e2e/browser-next-presentations.test.mjs") < index
    assert 'MARKWEAVE_E2E_PROFILE="$profile"' in runner[index : index + 200]
    for scenario in (
        "Markdown folder",
        "2pptx",
        "Markdown image is missing or unapproved.",
        "Markdown image path escapes the archive.",
        "The selected file exceeds the configured upload limit.",
    ):
        assert scenario in browser
    for scenario in (
        "packaged_images",
        "bypass-missing.zip",
        "bypass-traversal.zip",
        "conversion_request_invalid",
        "Document image exceeds configured limits.",
    ):
        assert scenario in workflow


def _runner_function(runner: str, name: str, following: str) -> str:
    body = runner.split(f"{name}() {{", 1)[1].split(f"\n{following}() {{", 1)[0]
    return f"{name}() {{{body}"


@pytest.mark.unit
def test_scanner_outage_probe_is_a_documented_bounded_poll() -> None:
    runner = _runner_text()
    probe = _runner_function(
        runner, "probe_scanner_outage_routes", "require_reserved_address"
    )
    rationale = runner.split("probe_scanner_outage_routes() {", 1)[0][-900:]

    assert "const pollWindowMs = 20_000;" in probe
    assert "const intervalMs = 250;" in probe
    assert "AbortSignal.timeout(2000)" in probe
    assert "if (Date.now() + intervalMs >= deadline) break;" in probe
    assert "scanner-outage attempt ${attempt}" in probe
    assert "scanner-outage route ${name} status=${status}" in probe
    assert "scanner-outage attempts=${attempt} elapsed_ms=" in probe
    assert '!required.has(name) || status === "200"' in probe
    assert 'record_name_resolution "$application_name" >&2 || true' in probe
    assert "A persistent failure of any required route still" in rationale
    assert "real readiness or routing regression is not masked" in rationale
    stop = runner.index('podman stop --time 5 "$clamav_name" >/dev/null')
    assert runner.index("probe_scanner_outage_routes\n", stop) > stop


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function; no runtime starts"
)
@pytest.mark.parametrize(("probe_status", "expected_code"), [(0, 0), (1, 1)])
def test_failed_scanner_outage_probe_records_name_resolution(
    probe_status: int, expected_code: int
) -> None:
    runner = _runner_text()
    probe = _runner_function(
        runner, "probe_scanner_outage_routes", "require_reserved_address"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'application_name="e2e-api"\n'
            "admission_frontend_origin() { printf 'http://10.89.0.252:3000\\n'; }\n"
            "record_name_resolution() {\n"
            "  printf 'name-resolution for %s\\n' \"$1\"\n"
            "}\n"
            "podman() {\n"
            "  printf 'probed %s\\n' \"$*\" | head -c 80; echo\n"
            '  return "$PROBE_STATUS"\n'
            "}\n"
            f"{probe}\n"
            "probe_scanner_outage_routes",
            "bash",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | {"PROBE_STATUS": str(probe_status)},
    )

    assert result.returncode == expected_code
    assert "probed exec --env E2E_FRONTEND_ORIGIN=http://10.89.0.252:3000" in (
        result.stdout
    )
    assert ("name-resolution for e2e-api" in result.stderr) is bool(probe_status)


@pytest.mark.unit
def test_name_resolution_record_covers_pinned_and_resolver_paths() -> None:
    runner = _runner_text()
    record = _runner_function(
        runner, "record_name_resolution", "probe_scanner_outage_routes"
    )
    collector = runner.split("collect_failure_artifacts() {", 1)[1].split(
        "\ncleanup() {", 1
    )[0]

    assert 'for name in ("postgres", "rustfs", "frontend", "e2e-clamav"):' in record
    assert 'for query in (name, f"{name}.dns.podman"):' in record
    assert '("getent", "ahosts", query)' in record
    assert "timeout=5," in record
    assert "elapsed_ms={elapsed}" in record
    assert (
        'for resource in "$application_name" "$worker_one_name" "$worker_two_name"; do'
    ) in collector
    assert 'record_name_resolution "$resource" \\' in collector
    assert '"$artifact_directory/$resource.name-resolution.txt" 2>&1 || true' in (
        collector
    )


def _reservation_program(runner: str) -> str:
    return runner.split(
        'reserved_addresses="$(podman network inspect "$network_name" '
        "| uv run python -c '",
        1,
    )[1].split("\n')\"", 1)[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("subnet", "gateway", "expected"),
    [
        ("10.89.0.0/24", "10.89.0.1", "10.89.0.2 10.89.0.254 10.89.0.253"),
        ("10.89.4.0/23", "10.89.4.1", "10.89.4.2 10.89.5.254 10.89.5.253"),
        ("10.89.0.0/29", "10.89.0.1", "10.89.0.2 10.89.0.6 10.89.0.5"),
        ("10.89.0.0/30", "10.89.0.1", None),
        ("10.89.0.0/29", "10.89.0.5", None),
    ],
)
def test_e2e_network_reserves_distinct_usable_peer_addresses(
    subnet: str, gateway: str, expected: str | None
) -> None:
    program = _reservation_program(_runner_text())
    inspected = f'[{{"subnets": [{{"subnet": "{subnet}", "gateway": "{gateway}"}}]}}]'
    result = subprocess.run(
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        input=inspected,
        text=True,
    )

    if expected is None:
        assert result.returncode != 0
        assert "no usable reserved IPv4 addresses" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{expected}\n"


@pytest.mark.unit
def test_storage_peers_use_reserved_addresses_and_host_entries() -> None:
    runner = _runner_text()
    start_frontend = _runner_function(
        runner, "start_frontend", "record_name_resolution"
    )
    mapping = runner.split("# Pin the storage peers the same way.", 1)[1].split(
        "\nreverse_digest=", 1
    )[0]

    assert (
        "read -r scanner_static_address postgres_static_address rustfs_static_address \\\n"
        '  <<<"$reserved_addresses"'
    ) in runner
    assert '--network-alias postgres --ip "$postgres_static_address"' in runner
    assert '--network-alias rustfs --ip "$rustfs_static_address"' in runner
    # Replaced frontend containers keep dynamic addresses: reusing one address
    # for a new container left a stale neighbor entry on Podman 4.9.
    assert "frontend_static_address" not in runner
    assert '--add-host "frontend:' not in runner
    assert runner.count("--network-alias frontend --user") == 2
    assert "require_reserved_address" not in start_frontend
    for name in ("postgres", "rustfs"):
        check = f'require_reserved_address "${name}_name" "${name}_static_address"'
        assert runner.count(check) == 2
        start = runner.index(f'podman start "${name}_name" >/dev/null')
        assert runner.index(check, start) > start
    assert "service_host_mapping=()" in mapping
    assert '--add-host "postgres:$postgres_static_address"' in mapping
    assert '--add-host "rustfs:$rustfs_static_address"' in mapping
    assert 'scanner_host_mapping+=("${service_host_mapping[@]}")' in mapping
    application_runs = runner.count(
        "--network-alias application --publish 127.0.0.1::8080 \\\n"
    )
    assert application_runs == runner.count(
        "--network-alias application --publish 127.0.0.1::8080 \\\n"
        '  "${scanner_host_mapping[@]}" \\\n'
    ) + runner.count(
        "--network-alias application --publish 127.0.0.1::8080 \\\n"
        '    "${scanner_host_mapping[@]}" \\\n'
    ) + runner.count(
        "--network-alias application --publish 127.0.0.1::8080 \\\n"
        '  "${service_host_mapping[@]}" \\\n'
    )
    assert runner.count('      "${scanner_host_mapping[@]}" \\\n') == 1


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function; no runtime starts"
)
@pytest.mark.parametrize(
    ("inspected", "expected_code"), [("10.89.0.253", 0), ("10.89.0.7", 1)]
)
def test_reserved_address_check_rejects_a_moved_peer(
    inspected: str, expected_code: int
) -> None:
    runner = _runner_text()
    check = _runner_function(
        runner, "require_reserved_address", "admission_frontend_origin"
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'network_name="e2e-network"\n'
            "podman() { printf '%s\\n' \"$INSPECTED_ADDRESS\"; }\n"
            f"{check}\n"
            "require_reserved_address e2e-rustfs 10.89.0.253",
            "bash",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | {"INSPECTED_ADDRESS": inspected},
    )

    assert result.returncode == expected_code
    if expected_code:
        assert "did not keep its reserved E2E network address" in result.stderr


# A Node preload that replaces fetch, Date.now and setTimeout for the extracted
# scanner-outage poll. Each route follows a scripted list of outcomes (the last
# one repeats); "timeout" advances the virtual clock by the 2-second request
# bound, and the 250 ms pause advances it by its interval, so the 20-second
# window is exercised deterministically in milliseconds of wall time.
SCANNER_OUTAGE_RUNTIME = r"""
const plan = JSON.parse(process.env.SCANNER_OUTAGE_PLAN);
const origin = process.env.E2E_FRONTEND_ORIGIN;
const realNow = Date.now.bind(Date);
const base = realNow();
let virtualMs = 0;
Date.now = () => base + virtualMs;
globalThis.setTimeout = (callback, delay = 0, ...args) => {
  virtualMs += delay;
  return setImmediate(callback, ...args);
};
const calls = new Map();
const route = (url) => {
  if (url === "http://localhost:3100/login") return "router_login";
  if (url === "http://frontend:3000/login") return "frontend_alias";
  if (url === `${origin}/login`) return "frontend_numeric";
  if (url === "http://127.0.0.1:8080/health/ready") return "backend_ready";
  throw new Error(`unexpected probe URL ${url}`);
};
globalThis.fetch = async (url, options) => {
  if (!(options?.signal instanceof AbortSignal)) throw new Error("unbounded probe");
  const name = route(String(url));
  const index = calls.get(name) ?? 0;
  calls.set(name, index + 1);
  const outcomes = plan[name] ?? ["200"];
  const outcome = outcomes[Math.min(index, outcomes.length - 1)];
  if (outcome === "timeout") {
    virtualMs += 2000;
    throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
  }
  if (outcome === "reject") {
    throw new TypeError("fetch failed", { cause: { code: "ECONNREFUSED" } });
  }
  return { status: Number(outcome) };
};
"""

REQUIRED_OUTAGE_ROUTES = ("router_login", "frontend_numeric", "backend_ready")


def _run_scanner_outage_probe(
    tmp_path: Path, plan: dict[str, list[str]]
) -> subprocess.CompletedProcess[str]:
    runner = _runner_text()
    probe = _runner_function(
        runner, "probe_scanner_outage_routes", "require_reserved_address"
    )
    preload = tmp_path / "scanner-outage-runtime.mjs"
    preload.write_text(SCANNER_OUTAGE_RUNTIME, encoding="utf-8")
    return subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'application_name="e2e-api"\n'
            "admission_frontend_origin() { printf 'http://10.89.0.252:3000\\n'; }\n"
            "record_name_resolution() { printf 'name-resolution for %s\\n' \"$1\"; }\n"
            "podman() {\n"
            '  [[ "$1" == exec ]] || return 97\n'
            "  shift\n"
            "  local -a environment=()\n"
            '  while [[ "$1" == --env ]]; do environment+=("$2"); shift 2; done\n'
            '  [[ "$1" == "$application_name" && "$2" == node ]] || return 98\n'
            "  shift 2\n"
            '  env "${environment[@]}" node --import "$SCANNER_OUTAGE_PRELOAD" "$@"\n'
            "}\n"
            f"{probe}\n"
            "probe_scanner_outage_routes",
            "bash",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ
        | {
            "SCANNER_OUTAGE_PLAN": json.dumps(plan),
            "SCANNER_OUTAGE_PRELOAD": preload.as_uri(),
        },
    )


def _outage_summary(stdout: str) -> tuple[dict[str, str], int, int]:
    statuses = {}
    attempts = elapsed = -1
    for line in stdout.splitlines():
        if line.startswith("scanner-outage route "):
            name, status = line.removeprefix("scanner-outage route ").split(
                " status=", 1
            )
            statuses[name] = status
        elif line.startswith("scanner-outage attempts="):
            fields = dict(field.split("=", 1) for field in line.split()[1:])
            attempts = int(fields["attempts"])
            elapsed = int(fields["elapsed_ms"])
    return statuses, attempts, elapsed


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function that runs Node; no runtime starts"
)
@pytest.mark.parametrize("route", REQUIRED_OUTAGE_ROUTES)
@pytest.mark.parametrize(
    ("outcome", "reported"),
    [
        ("503", "503"),
        ("reject", "error:ECONNREFUSED"),
        ("timeout", "error:TimeoutError"),
    ],
)
def test_scanner_outage_poll_fails_on_any_persistent_required_route_failure(
    tmp_path: Path, route: str, outcome: str, reported: str
) -> None:
    result = _run_scanner_outage_probe(tmp_path, {route: [outcome]})
    statuses, attempts, elapsed = _outage_summary(result.stdout)

    assert result.returncode == 1, result.stderr
    assert "name-resolution for e2e-api" in result.stderr
    assert statuses[route] == reported
    assert all(
        statuses[name] == "200" for name in REQUIRED_OUTAGE_ROUTES if name != route
    )
    assert 1 < attempts <= 80
    assert 20_000 - 250 <= elapsed <= 20_000 + 2_000
    assert result.stdout.count("scanner-outage attempt ") == attempts


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function that runs Node; no runtime starts"
)
def test_scanner_outage_poll_recovers_from_a_transient_stall(tmp_path: Path) -> None:
    result = _run_scanner_outage_probe(
        tmp_path,
        {
            "frontend_alias": ["timeout", "timeout", "200"],
            "backend_ready": ["timeout", "503", "200"],
        },
    )
    statuses, attempts, elapsed = _outage_summary(result.stdout)

    assert result.returncode == 0, result.stderr
    assert "name-resolution" not in result.stderr
    assert statuses == dict.fromkeys(
        ("router_login", "frontend_alias", "frontend_numeric", "backend_ready"), "200"
    )
    assert attempts == 3
    assert elapsed < 20_000
    assert "scanner-outage attempt 1 " in result.stdout
    assert "backend_ready=error:TimeoutError" in result.stdout
    assert "scanner-outage attempt 2 " in result.stdout
    assert "scanner-outage attempt 3 " not in result.stdout


@pytest.mark.unit
@pytest.mark.container_runtime_guard_exempt(
    reason="the bash script stubs podman with a shell function that runs Node; no runtime starts"
)
@pytest.mark.parametrize("outcome", ["503", "reject", "timeout"])
def test_scanner_outage_alias_failure_alone_stays_diagnostic(
    tmp_path: Path, outcome: str
) -> None:
    result = _run_scanner_outage_probe(tmp_path, {"frontend_alias": [outcome]})
    statuses, attempts, _elapsed = _outage_summary(result.stdout)

    assert result.returncode == 0, result.stderr
    assert "name-resolution" not in result.stderr
    assert statuses["frontend_alias"] != "200"
    assert all(statuses[name] == "200" for name in REQUIRED_OUTAGE_ROUTES)
    assert attempts == 1
