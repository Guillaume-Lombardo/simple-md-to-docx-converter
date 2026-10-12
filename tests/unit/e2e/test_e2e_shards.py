"""Every final-image E2E scenario runs in exactly one shard."""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from scripts.ci import e2e_shards
from scripts.ci.select_domains import E2E_DOMAINS, E2E_SHARD_COUNT

pytestmark = pytest.mark.unit

RUNNER = Path("scripts/e2e/run.sh")


def _runner() -> str:
    return RUNNER.read_text(encoding="utf-8")


def test_committed_harness_assigns_every_scenario_to_exactly_one_shard() -> None:
    assert e2e_shards.validate_harness(_runner(), expected_count=E2E_SHARD_COUNT) == []
    assert e2e_shards.validate_files([RUNNER], expected_count=E2E_SHARD_COUNT) == []


def test_ci_runs_every_shard_of_both_profiles() -> None:
    harness, errors = e2e_shards.parse_harness(_runner())
    assert errors == []
    assert sorted(E2E_DOMAINS.values()) == sorted(
        (profile, shard)
        for profile in ("distributed", "standalone")
        for shard in harness.shards
    )


def test_shard_map_covers_the_complete_suite_once() -> None:
    text = _runner()
    harness, _errors = e2e_shards.parse_harness(text)
    mapped = [
        label for entries in e2e_shards.shard_map(text).values() for _, label in entries
    ]
    qualifying = [
        scenario.label
        for scenario in e2e_shards.scenarios(harness)
        if not str(scenario.function).startswith(
            (e2e_shards.SEED_PREFIX, e2e_shards.DIAGNOSTIC_PREFIX)
        )
    ]
    assert Counter(mapped) == Counter(qualifying)
    shard_map = e2e_shards.shard_map(text)
    assert {phase for phase, _label in shard_map[1]} == {
        "reverse-api",
        "service",
        "browser-session",
    }
    assert {phase for phase, _label in shard_map[2]} == {
        "reverse-lifecycle",
        "browser-workspaces",
        "browser-runtime",
    }
    first = [label for _phase, label in shard_map[1]]
    second = [label for _phase, label in shard_map[2]]
    # State-coupled chains stay inside one shard.
    assert "browser-next-composer-real" in first
    assert "browser-next-composer-resilience restart" in first
    assert "browser-recovery-checkpoint" in first
    assert "browser-recovery" in first
    assert "browser-next-conversion" in second
    assert "browser-next-conversion-restart-prepare" in second
    assert "reverse_cli_workflow structured-pptx" in second
    assert "browser-next-reversion structured" in second
    assert "reverse lifecycle broker-restart" in second
    assert "browser-next-reversion recovered" in second


def test_scenario_outside_every_phase_is_rejected() -> None:
    text = _runner().replace(
        '\nfor phase in "${selected_phases[@]}"; do\n',
        '\nrun_browser_test "$application_name" /e2e/browser-next-auth.test.mjs\n'
        'for phase in "${selected_phases[@]}"; do\n',
    )
    errors = e2e_shards.validate_harness(text)
    assert any("(browser-next-auth) runs outside the shard phases" in e for e in errors)


def test_scenario_reachable_from_two_shards_is_rejected() -> None:
    text = _runner().replace(
        "  use_api_runtime\n  run_reverse_lifecycle worker-restart\n",
        "  use_api_runtime\n  run_reverse_lifecycle worker-restart\n"
        "  prove_composer_key_loss_continuity\n",
    )
    errors = e2e_shards.validate_harness(text)
    assert any("runs in shards [1, 2], not exactly one" in error for error in errors)


def test_helper_called_from_top_level_cannot_hide_a_scenario() -> None:
    text = _runner().replace(
        '\nfor phase in "${selected_phases[@]}"; do\n',
        "\nprove_composer_key_loss_continuity\n"
        'for phase in "${selected_phases[@]}"; do\n',
    )
    errors = e2e_shards.validate_harness(text)
    assert any("key-unavailable) runs outside the shard phases" in e for e in errors)


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        (
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
            "browser-runtime)",
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces)",
            "phase browser-runtime is assigned to no shard",
        ),
        (
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
            "browser-runtime)",
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
            "browser-runtime service)",
            "phase service is assigned to more than one shard",
        ),
        (
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
            "browser-runtime)",
            "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
            "browser-runtime missing)",
            "shard phase missing has no e2e_phase function",
        ),
        (
            '  2) selected_phases=("${e2e_shard_2_phases[@]}") ;;',
            '  2) selected_phases=("${e2e_shard_1_phases[@]}") ;;',
            "shard 2 is not selectable by MARKWEAVE_E2E_SHARD",
        ),
        (
            '  all) selected_phases=("${e2e_shard_1_phases[@]}" '
            '"${e2e_shard_2_phases[@]}") ;;',
            '  all) selected_phases=("${e2e_shard_1_phases[@]}") ;;',
            "the complete run must execute every shard in order",
        ),
        (
            '  "e2e_phase_${phase//-/_}"\n',
            "  e2e_phase_service\n",
            "phases must run only through the selected-phase dispatcher",
        ),
        (
            "  if ! e2e_selects_phase service; then\n",
            "  e2e_phase_reverse_api\n  if ! e2e_selects_phase service; then\n",
            "phase function e2e_phase_reverse_api is called outside the dispatcher",
        ),
        (
            "  uv run python -m tests.e2e.service_workflow seed-browser-prerequisites",
            "  uv run python -m tests.e2e.service_workflow checkpoint",
            "seed e2e_seed_browser_prerequisites runs a scenario",
        ),
        (
            '\nfor phase in "${selected_phases[@]}"; do\n',
            "\ne2e_seed_browser_prerequisites\n"
            'for phase in "${selected_phases[@]}"; do\n',
            "seed e2e_seed_browser_prerequisites runs outside the shard phases",
        ),
    ],
)
def test_shard_assignment_defects_are_rejected(
    original: str, replacement: str, message: str
) -> None:
    text = _runner()
    assert text.count(original) == 1
    errors = e2e_shards.validate_harness(text.replace(original, replacement))
    assert any(message in error for error in errors), errors


def test_shard_declarations_must_be_contiguous_and_match_ci() -> None:
    text = _runner()
    assert e2e_shards.validate_harness(text, expected_count=3) == [
        "the harness declares 2 shards but CI runs 3"
    ]
    single = text.replace(
        "readonly -a e2e_shard_2_phases=(reverse-lifecycle browser-workspaces "
        "browser-runtime)\n",
        "",
    )
    assert "the harness must declare at least two contiguous shards" in (
        e2e_shards.validate_harness(single)
    )
    duplicate = text.replace(
        "readonly -a e2e_shard_1_phases=(reverse-api service browser-session)\n",
        "readonly -a e2e_shard_1_phases=(reverse-api service browser-session)\n" * 2,
    )
    assert "shard 1 is declared more than once" in e2e_shards.validate_harness(
        duplicate
    )


def test_parser_reports_structural_defects() -> None:
    _harness, errors = e2e_shards.parse_harness("one() {\n}\none() {\n}\ntwo() {\n")
    assert errors == [
        "function one is defined more than once",
        "function two is not terminated",
    ]


def test_scenario_labels_name_workflows_browser_variants_and_lifecycles() -> None:
    harness, _errors = e2e_shards.parse_harness(_runner())
    labels = {scenario.label for scenario in e2e_shards.scenarios(harness)}
    assert {
        "service_workflow verify-checkpoint",
        "reverse_cli_workflow primary",
        "browser-next-composer-resilience scanner-unavailable",
        "browser-next-runtime-failures admission",
        "reverse lifecycle worker-restart",
        "browser-runner-smoke",
        "cli_workflow",
    } <= labels


def test_validate_files_reports_unreadable_harness(tmp_path: Path) -> None:
    missing = tmp_path / "run.sh"
    assert e2e_shards.validate_files([missing], expected_count=2)[0].startswith(
        f"{missing}: cannot read the E2E harness"
    )


def test_command_line_prints_the_shard_map(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert e2e_shards.main([]) == 0
    output = capsys.readouterr().out
    assert "shard 1 | browser-session | browser-next-composer-real\n" in output
    assert "shard 2 | browser-runtime | browser-next-admin\n" in output
    broken = tmp_path / "run.sh"
    broken.write_text(_runner().replace("browser-runtime)", ")"), encoding="utf-8")
    assert e2e_shards.main(["--script", str(broken)]) == 1
    assert "error: phase browser-runtime is assigned to no shard" in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"MARKWEAVE_E2E_SHARD": "3"}, "MARKWEAVE_E2E_SHARD must be all, 1, or 2."),
        (
            {"MARKWEAVE_E2E_SHARD": "1", "MARKWEAVE_E2E_COMPOSER_SCENARIO_ONLY": "1"},
            "Diagnostic browser modes run only without an E2E shard selection.",
        ),
        (
            {"MARKWEAVE_E2E_SHARD": "2", "MARKWEAVE_E2E_REVERSE_PRIMARY_ONLY": "true"},
            "The reverse primary-path diagnostic requires the reverse-api phase.",
        ),
    ],
)
def test_runner_rejects_invalid_shard_selection_before_runtime_setup(
    environment: dict[str, str], message: str
) -> None:
    result = subprocess.run(
        [str(RUNNER.resolve()), "standalone"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | environment,
    )
    assert result.returncode == 2
    assert message in result.stderr
