"""Tests for selective CI domain detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.select_domains import (
    DOMAIN_PATTERNS,
    classify_domains,
    load_registry,
    main,
    select_domains,
)


@pytest.mark.unit
def test_documentation_only_change_has_no_heavy_domain() -> None:
    """Documentation still runs light CI without selecting a heavy domain."""
    assert select_domains(["docs/architecture.md"]) == []


@pytest.mark.unit
def test_source_change_selects_all_application_domains_except_container() -> None:
    """Shared Python behavior affects functional, storage, engine, and E2E suites."""
    selected = select_domains(["src/md_converter/service.py"])
    assert selected == sorted(set(DOMAIN_PATTERNS) - {"ci-infrastructure", "container"})


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", [".github/workflows/ci.yml", "uv.lock", "scripts/ci/check.py"]
)
def test_global_ci_change_selects_every_domain(path: str) -> None:
    """CI and dependency changes conservatively select every domain."""
    assert select_domains([path]) == sorted(DOMAIN_PATTERNS)


@pytest.mark.unit
def test_complete_suite_selects_every_domain_without_paths() -> None:
    """Scheduled, release, and requested complete runs select both profiles."""
    assert select_domains([], full=True) == sorted(DOMAIN_PATTERNS)


@pytest.mark.unit
def test_root_containerfile_selects_container_domain() -> None:
    """The future final image definition activates container validation."""
    assert select_domains(["Containerfile"]) == ["container"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".github/ci/domains.json",
        ".github/workflows/ci.yml",
        "scripts/ci/run_domain.py",
        "scripts/ci/select_domains.py",
        "tests/test_ci_runner.py",
        "tests/test_python_quality.py",
        "tests/integration/ci/test_gate.py",
    ],
)
def test_ci_implementation_change_selects_active_infrastructure_domain(
    path: str,
) -> None:
    """Every runner, detector, registry, workflow, and integration path enforces real CI tests."""
    assert "ci-infrastructure" in select_domains([path])


@pytest.mark.unit
def test_committed_ci_infrastructure_domain_is_active_and_runnable() -> None:
    """The review-required subprocess tests cannot be reported as merely planned."""
    registry = load_registry(Path(".github/ci/domains.json"))
    selected = select_domains(["tests/test_ci_runner.py"])
    planned, runnable = classify_domains(selected, registry)
    assert planned == []
    assert runnable == ["ci-infrastructure"]
    assert registry["ci-infrastructure"]["command"] == [
        "uv",
        "run",
        "pytest",
        "tests/test_ci_runner.py",
        "tests/integration/ci",
        "-m",
        "integration",
        "--no-cov",
    ]


@pytest.mark.unit
def test_draft_suppresses_only_runnable_heavy_domains() -> None:
    """Drafts retain visible planned gaps but do not run active heavy suites."""
    registry = {
        "functional": {"status": "active"},
        "container": {"status": "planned"},
    }
    planned, runnable = classify_domains(
        ["functional", "container"], registry, draft=True
    )
    assert planned == ["container"]
    assert runnable == []


@pytest.mark.unit
def test_registry_requires_exact_known_domain_set(tmp_path: Path) -> None:
    """An omitted domain cannot silently disappear from selective CI."""
    registry = tmp_path / "domains.json"
    registry.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="every known domain"):
        load_registry(registry)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "error_type", "message"),
    [
        ([], TypeError, "object entry"),
        (
            {"status": "unknown", "activation_ticket": "T99"},
            ValueError,
            "invalid status",
        ),
        (
            {"status": "planned", "activation_ticket": "issue"},
            ValueError,
            "activation ticket",
        ),
        (
            {"status": "planned", "activation_ticket": "T99", "command": ["pytest"]},
            ValueError,
            "must not declare",
        ),
        (
            {"status": "active", "activation_ticket": "T99", "command": []},
            ValueError,
            "non-empty command",
        ),
    ],
)
def test_registry_rejects_invalid_domain_entry(
    tmp_path: Path,
    entry: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """Lifecycle metadata cannot make a planned or malformed suite runnable."""
    data: dict[str, object] = {
        domain: {"status": "planned", "activation_ticket": "T99"}
        for domain in DOMAIN_PATTERNS
    }
    data["functional"] = entry
    registry = tmp_path / "domains.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(error_type, match=message):
        load_registry(registry)


@pytest.mark.unit
def test_cli_writes_compact_github_outputs(tmp_path: Path) -> None:
    """The workflow receives deterministic JSON arrays through GITHUB_OUTPUT."""
    paths = tmp_path / "paths"
    paths.write_bytes(b"src/md_converter/service.py\0")
    registry = tmp_path / "domains.json"
    registry.write_text(
        json.dumps(
            {
                domain: {"status": "planned", "activation_ticket": "T99"}
                for domain in DOMAIN_PATTERNS
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    assert (
        main(
            [
                "--paths-file",
                str(paths),
                "--registry",
                str(registry),
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    values = dict(
        line.split("=", maxsplit=1) for line in output.read_text().splitlines()
    )
    assert json.loads(values["selected-domains"]) == sorted(
        set(DOMAIN_PATTERNS) - {"ci-infrastructure", "container"}
    )
    assert json.loads(values["runnable-domains"]) == []
