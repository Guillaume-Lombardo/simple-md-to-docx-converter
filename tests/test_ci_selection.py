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
@pytest.mark.parametrize(
    "path",
    [
        ".gitignore",
        "README.md",
        "compose.simple.yaml",
        "compose.podman.yaml",
        "compose.yaml",
        "docs/local-development.md",
        "examples/quickstart-template.docx.base64",
        "examples/quickstart-source.md",
        "scripts/quickstart-simple.sh",
        "scripts/quickstart.sh",
        "scripts/e2e/run-compose-all.sh",
        "scripts/e2e/run-compose-simple.sh",
        "tests/test_quickstart_compose.py",
    ],
)
def test_quickstart_inputs_select_active_compose_e2e(path: str) -> None:
    """Every casual quickstart input executes the real pinned Compose workflow."""
    assert "compose" in select_domains([path])


@pytest.mark.unit
def test_compose_domain_is_active_and_uses_the_isolated_runner() -> None:
    registry = load_registry(Path(".github/ci/domains.json"))
    assert registry["compose"] == {
        "activation_ticket": "T23",
        "command": ["bash", "scripts/e2e/run-compose-all.sh"],
        "status": "active",
    }


@pytest.mark.unit
def test_source_change_selects_all_application_domains_except_container() -> None:
    """Shared Python behavior affects functional, storage, engine, and E2E suites."""
    selected = select_domains(["src/markweave/service.py"])
    assert selected == sorted(
        set(DOMAIN_PATTERNS) - {"ci-infrastructure", "compose", "container"}
    )


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
    """Final-image changes run container validation and both image E2E profiles."""
    assert select_domains(["Containerfile"]) == [
        "container",
        "e2e-distributed",
        "e2e-standalone",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".containerignore",
        "container/entrypoint.sh",
        "scripts/container/smoke.sh",
        "tests/container/test_container_assets.py",
    ],
)
def test_t20_asset_changes_select_container_domain(path: str) -> None:
    selected = select_domains([path])
    assert "container" in selected
    if path != "tests/container/test_container_assets.py":
        assert "e2e-distributed" in selected
        assert "e2e-standalone" in selected


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
def test_t06_functional_domain_is_active_and_runnable() -> None:
    """Hosted CI executes the T06 ASGI and real Argon2 integration suite."""
    registry = load_registry(Path(".github/ci/domains.json"))
    selected = select_domains(["src/markweave/app.py"])
    planned, runnable = classify_domains(selected, registry)
    assert "functional" not in planned
    assert "functional" in runnable
    assert registry["functional"]["command"] == [
        "uv",
        "run",
        "pytest",
        "tests/functional",
        "tests/integration/auth",
        "-m",
        "functional or integration",
        "--no-cov",
    ]


@pytest.mark.unit
def test_auth_integration_change_selects_functional_domain() -> None:
    """Real authentication boundary tests cannot bypass their hosted domain."""
    assert "functional" in select_domains(
        ["tests/integration/auth/test_http_session.py"]
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "tests/corpus/manifest.json",
        "scripts/generate_t11_pdf_golden.py",
        "tests/conftest.py",
        "tests/golden/openxml.py",
        "tests/unit/test_golden_raster.py",
        "tests/integration/document_engines/test_reference_corpus.py",
    ],
)
def test_golden_infrastructure_selects_active_document_engine_domain(
    path: str,
) -> None:
    selected = select_domains([path])
    assert "document-engines" in selected
    if path.startswith("tests/corpus/"):
        assert "e2e-distributed" in selected
        assert "e2e-standalone" in selected


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "package.json",
        "tests/browser/conversion.browser.test.mjs",
        "tests/browser/server.py",
    ],
)
def test_browser_workflow_changes_select_document_engine_domain(path: str) -> None:
    """Browser scripts and tests cannot skip the Chrome-provisioned heavy job."""
    selected = select_domains([path])
    assert "document-engines" in selected
    if path == "package.json":
        assert "e2e-distributed" in selected
        assert "e2e-standalone" in selected


@pytest.mark.unit
def test_t04_document_engine_domain_runs_current_integration_boundaries() -> None:
    registry = load_registry(Path(".github/ci/domains.json"))
    planned, runnable = classify_domains(["document-engines"], registry)
    assert planned == []
    assert runnable == ["document-engines"]
    assert registry["document-engines"] == {
        "activation_ticket": "T04",
        "command": [
            "uv",
            "run",
            "pytest",
            "tests/integration/document_engines",
            "-m",
            "integration",
            "--no-cov",
        ],
        "status": "active",
    }


@pytest.mark.unit
def test_t21_e2e_domains_are_active_and_profile_specific() -> None:
    """Each final-image profile runs through its own reviewed matrix command."""
    registry = load_registry(Path(".github/ci/domains.json"))
    assert registry["e2e-standalone"] == {
        "activation_ticket": "T21",
        "command": ["bash", "scripts/e2e/run.sh", "standalone"],
        "status": "active",
    }
    assert registry["e2e-distributed"] == {
        "activation_ticket": "T21",
        "command": ["bash", "scripts/e2e/run.sh", "distributed"],
        "status": "active",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".containerignore",
        "Containerfile",
        "package.json",
        "package-lock.json",
        "playwright.config.mjs",
        "container/entrypoint.sh",
        "scripts/container/build.sh",
        "scripts/e2e/run.sh",
        "spikes/toolchain/package-lock.json",
        "tests/e2e/rootless.spec.mjs",
        "tests/corpus/mermaid/diagram.md",
        "deploy/standalone.yaml.example",
    ],
)
def test_final_image_inputs_select_both_t21_profiles(path: str) -> None:
    """No final-image, driver, deployment, or E2E change can skip one profile."""
    selected = select_domains([path])
    assert "e2e-standalone" in selected
    assert "e2e-distributed" in selected


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
    paths.write_bytes(b"src/markweave/service.py\0")
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
        set(DOMAIN_PATTERNS) - {"ci-infrastructure", "compose", "container"}
    )
    assert json.loads(values["runnable-domains"]) == []
