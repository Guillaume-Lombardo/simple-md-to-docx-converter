"""Contracts for the reviewed critical mutation campaign."""

from __future__ import annotations

import json
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
    ]
    assert [domain.priority for domain in manifest.domains] == [1, 2, 3, 4, 5]
    assert sum(len(domain.mutants) for domain in manifest.domains) == 25
    assert (
        len({mutant for domain in manifest.domains for mutant in domain.mutants}) == 25
    )
    assert all(domain.review_notes for domain in manifest.domains)
    assert manifest.failure_statuses == FAILURE_STATUSES


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
        in (observability.review_notes[0])
    )


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
    assert (
        select_domains(manifest, mode="changed", changed_paths=("docs/unrelated.md",))
        == ()
    )
    assert (
        select_domains(manifest, mode="changed", changed_paths=("pyproject.toml",))
        == manifest.domains
    )


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
