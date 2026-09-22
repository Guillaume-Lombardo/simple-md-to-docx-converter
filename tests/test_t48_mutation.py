"""Contracts for the reviewed critical mutation campaign."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from scripts.ci import run_mutation_campaign as campaign
from scripts.ci.run_mutation_campaign import (
    FAILURE_STATUSES,
    changed_paths,
    load_manifest,
    main,
    select_domains,
    verify_stats,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "mutation/domains.json"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"


def _write_manifest(tmp_path: Path, raw: object) -> Path:
    path = tmp_path / "domains.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.mark.unit
def test_manifest_is_risk_ranked_exact_and_reviews_every_critical_domain() -> None:
    manifest = load_manifest(MANIFEST)
    assert [domain.name for domain in manifest.domains] == [
        "observability",
        "auth-session",
        "archive-svg",
        "job-integrity",
        "retention-storage",
        "reverse-result-security",
    ]
    assert [domain.priority for domain in manifest.domains] == [1, 2, 3, 4, 5, 6]
    assert sum(len(domain.mutants) for domain in manifest.domains) == 28
    assert (
        len({mutant for domain in manifest.domains for mutant in domain.mutants}) == 28
    )
    assert all(domain.review_notes for domain in manifest.domains)
    assert manifest.failure_statuses == FAILURE_STATUSES


@pytest.mark.unit
def test_mutmut_generation_is_bounded_to_reviewed_manifest_modules() -> None:
    configuration = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["mutmut"]
    configured_paths = set(configuration["only_mutate"])
    manifest = load_manifest(MANIFEST)
    reviewed_modules = {
        "src/" + mutant.split(".x", maxsplit=1)[0].replace(".", "/") + ".py"
        for domain in manifest.domains
        for mutant in domain.mutants
    }
    assert configured_paths == reviewed_modules


@pytest.mark.unit
def test_mutmut_uses_project_import_configuration_without_coverage_gates() -> None:
    configuration = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["tool"]["mutmut"]

    assert "--no-cov" in configuration["pytest_add_cli_args"]
    assert "-c" not in configuration["pytest_add_cli_args"]
    assert "/dev/null" not in configuration["pytest_add_cli_args"]
    assert configuration["also_copy"] == ["scripts"]


@pytest.mark.unit
def test_observability_domain_preserves_the_preexisting_bounded_target() -> None:
    manifest = load_manifest(MANIFEST)
    observability = manifest.domains[0]
    assert observability.name == "observability"
    assert observability.paths == (
        "src/markweave/observability.py",
        "tests/unit/test_observability.py",
    )
    assert observability.mutants == tuple(
        f"markweave.observability.x__normalize_method__mutmut_{index}"
        for index in range(1, 5)
    )
    assert (
        "markweave.observability.x__normalize_method__mutmut_*"
        in observability.review_notes[0]
    )


@pytest.mark.unit
def test_reverse_result_domain_is_exact_and_risk_reviewed() -> None:
    manifest = load_manifest(MANIFEST)
    reverse = manifest.domains[-1]
    assert reverse.name == "reverse-result-security"
    assert reverse.priority == 6
    assert reverse.paths == (
        "src/markweave/reversion_jobs/service.py",
        "tests/unit/reversions/test_reversion_service.py",
    )
    assert reverse.mutants == (
        "markweave.reversion_jobs.service.xǁReversionServiceǁget__mutmut_3",
        "markweave.reversion_jobs.service.xǁReversionServiceǁdownload__mutmut_33",
        "markweave.reversion_jobs.service.xǁReversionServiceǁdownload__mutmut_36",
    )
    assert "owner scoping" in reverse.review_notes[0]
    assert "same-size wrong content" in reverse.review_notes[0]


@pytest.mark.unit
def test_required_ci_gate_runs_affected_domains_and_retains_a_report() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "name: CI / mutation" in workflow
    assert (
        "needs: [detect, light, python-tests, python-coverage, domain-plan, heavy, mutation]"
        in workflow
    )
    assert (
        "MUTATION_MODE: ${{ (github.event_name == 'schedule' || github.event_name == 'release' || github.event_name == 'workflow_dispatch') && 'all' || 'changed' }}"
        in workflow
    )
    assert (
        '--mode "$MUTATION_MODE" --base-sha "$BASE_SHA" --head-sha "$HEAD_SHA"'
        in workflow
    )
    assert "path: mutation-results/report.json" in workflow
    assert '[[ "$MUTATION_RESULT" == "success" ]]' in workflow


@pytest.mark.unit
def test_load_manifest_rejects_unknown_top_level_fields(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["unreviewed"] = True
    with pytest.raises(ValueError, match="unexpected top-level fields"):
        load_manifest(_write_manifest(tmp_path, raw))


@pytest.mark.unit
def test_load_manifest_rejects_an_unsupported_schema(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported mutation manifest schema"):
        load_manifest(_write_manifest(tmp_path, raw))


@pytest.mark.unit
def test_load_manifest_rejects_duplicate_mutants_across_domains(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["domains"][1]["mutants"][0] = raw["domains"][0]["mutants"][0]
    with pytest.raises(ValueError, match="mutants must be unique"):
        load_manifest(_write_manifest(tmp_path, raw))


@pytest.mark.unit
def test_load_manifest_rejects_domains_out_of_reviewed_order(tmp_path: Path) -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["domains"][0]["priority"] = 2
    with pytest.raises(ValueError, match="domains must be ordered"):
        load_manifest(_write_manifest(tmp_path, raw))


@pytest.mark.unit
def test_mutmut_refuses_a_symlinked_generated_directory(tmp_path: Path, mocker) -> None:
    (tmp_path / "mutants").symlink_to(tmp_path / "outside")
    mocker.patch.object(campaign.shutil, "which", return_value="/bin/mutmut")
    with pytest.raises(
        RuntimeError, match="refusing to remove an unexpected mutants path"
    ):
        campaign._run_mutmut((), target_root=tmp_path)


@pytest.mark.unit
def test_changed_paths_select_only_affected_domains_and_global_files_select_all() -> (
    None
):
    manifest = load_manifest(MANIFEST)
    selected = select_domains(
        manifest,
        mode="changed",
        changed_paths=("src/markweave/auth/service.py", "docs/unrelated.md"),
    )
    assert [domain.name for domain in selected] == ["auth-session"]
    selected = select_domains(
        manifest,
        mode="changed",
        changed_paths=("tests/unit/test_observability.py",),
    )
    assert [domain.name for domain in selected] == ["observability"]
    selected = select_domains(
        manifest,
        mode="changed",
        changed_paths=("tests/unit/reversions/test_reversion_service.py",),
    )
    assert [domain.name for domain in selected] == ["reverse-result-security"]
    assert (
        select_domains(manifest, mode="changed", changed_paths=("docs/unrelated.md",))
        == ()
    )
    assert (
        select_domains(manifest, mode="changed", changed_paths=("pyproject.toml",))
        == manifest.domains
    )


@pytest.mark.unit
def test_changed_mode_returns_a_successful_not_affected_report(
    tmp_path: Path, mocker
) -> None:
    artifact = tmp_path / "report.json"
    mocker.patch.object(campaign, "changed_paths", return_value=("docs/unrelated.md",))

    assert (
        main(
            [
                "--mode",
                "changed",
                "--base-sha",
                "base",
                "--head-sha",
                "head",
                "--artifact",
                str(artifact),
            ]
        )
        == 0
    )

    report = json.loads(artifact.read_text(encoding="utf-8"))
    assert report["status"] == "not-affected"
    assert report["selected"] == 0
    assert report["killed"] == 0


@pytest.mark.unit
def test_changed_paths_includes_deletions_and_both_rename_endpoints(
    tmp_path: Path, mocker
) -> None:
    mocker.patch.object(
        campaign.subprocess,
        "run",
        return_value=mocker.Mock(
            stdout=(
                "D\0src/markweave/auth/service.py\0"
                "R100\0src/markweave/jobs/worker.py\0docs/worker.md\0"
                "C100\0src/markweave/storage.py\0docs/storage-copy.md\0"
            )
        ),
    )
    paths = changed_paths("base", "head", target_root=tmp_path)
    assert paths == (
        "docs/storage-copy.md",
        "docs/worker.md",
        "src/markweave/auth/service.py",
        "src/markweave/jobs/worker.py",
        "src/markweave/storage.py",
    )
    manifest = load_manifest(MANIFEST)
    assert [
        domain.name
        for domain in select_domains(manifest, mode="changed", changed_paths=paths)
    ] == [
        "auth-session",
        "job-integrity",
        "retention-storage",
    ]


@pytest.mark.unit
def test_changed_paths_rejects_malformed_or_unsafe_name_status(
    tmp_path: Path, mocker
) -> None:
    mocker.patch.object(
        campaign.subprocess, "run", return_value=mocker.Mock(stdout="D\0../secret\0")
    )
    with pytest.raises(RuntimeError, match="unsafe repository path"):
        changed_paths("base", "head", target_root=tmp_path)


@pytest.mark.unit
def test_stats_require_exact_killed_selection_and_reject_every_failure_status() -> None:
    passing = {"killed": 3, **dict.fromkeys(FAILURE_STATUSES, 0)}
    assert verify_stats(passing, selected=3)["killed"] == 3
    with pytest.raises(ValueError, match="killed 2 of 3"):
        verify_stats({**passing, "killed": 2}, selected=3)
    for status in FAILURE_STATUSES:
        with pytest.raises(ValueError, match="non-killed"):
            verify_stats({**passing, status: 1}, selected=3)


@pytest.mark.integration
def test_plan_cli_writes_a_machine_readable_bounded_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "report.json"
    assert (
        main(["--mode", "auth-session", "--plan-only", "--artifact", str(artifact)])
        == 0
    )
    report = json.loads(artifact.read_text(encoding="utf-8"))
    assert report["status"] == "planned"
    assert report["selected"] == 5
    assert report["domains"][0]["name"] == "auth-session"
    assert report["domains"][0]["command"][:2] == ["mutmut", "run"]
