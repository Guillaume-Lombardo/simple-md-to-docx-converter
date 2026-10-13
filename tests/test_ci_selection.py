"""Tests for selective CI domain detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts.ci.select_domains import (
    COMPOSE_DOMAINS,
    DOMAIN_PATTERNS,
    E2E_DOMAINS,
    IMAGE_DOMAIN_ROLES,
    IMAGE_ROLES,
    classify_domains,
    load_registry,
    main,
    partition_domains,
    select_domains,
)

E2E = sorted(E2E_DOMAINS)


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
        "compose.simple-composer.yaml",
        "compose.podman.yaml",
        "compose.podman-trusted-upstream.yaml",
        "compose.trusted-upstream.yaml",
        "compose.yaml",
        "docs/local-development.md",
        "examples/quickstart-template.docx.base64",
        "examples/quickstart-source.md",
        "scripts/quickstart-simple.sh",
        "scripts/quickstart.sh",
        "scripts/e2e/run-compose-all.sh",
        "tests/test_quickstart_compose.py",
    ],
)
def test_quickstart_inputs_select_active_compose_e2e(path: str) -> None:
    """Every casual quickstart input executes every real pinned Compose suite."""
    assert set(COMPOSE_DOMAINS) <= set(select_domains([path]))


@pytest.mark.unit
def test_compose_sub_suites_are_active_independent_domains() -> None:
    """The four Compose sub-suites run as separate jobs with their own runners."""
    registry = load_registry(Path(".github/ci/domains.json"))
    assert {domain: registry[domain] for domain in COMPOSE_DOMAINS} == {
        "compose-podman-insecure": {
            "activation_ticket": "T23",
            "command": ["bash", "scripts/e2e/run-compose-podman-insecure.sh"],
            "status": "active",
        },
        "compose-secure": {
            "activation_ticket": "T23",
            "command": ["bash", "scripts/e2e/run-compose.sh"],
            "status": "active",
        },
        "compose-simple-docker": {
            "activation_ticket": "T23",
            "command": [
                "env",
                "MARKWEAVE_SIMPLE_E2E_RUNTIME=docker",
                "bash",
                "scripts/e2e/run-compose-simple.sh",
            ],
            "status": "active",
        },
        "compose-simple-podman": {
            "activation_ticket": "T23",
            "command": [
                "env",
                "MARKWEAVE_SIMPLE_E2E_RUNTIME=podman",
                "bash",
                "scripts/e2e/run-compose-simple.sh",
            ],
            "status": "active",
        },
    }
    aggregate = Path("scripts/e2e/run-compose-all.sh").read_text(encoding="utf-8")
    for domain in COMPOSE_DOMAINS:
        command = registry[domain]["command"]
        assert isinstance(command, list)
        # The aggregate local runner executes exactly the same four commands.
        runner = " ".join(argument for argument in command if argument != "env")
        assert runner in aggregate


@pytest.mark.unit
def test_source_change_selects_all_application_domains_except_container() -> None:
    """Shared Python behavior affects engine and E2E suites."""
    selected = select_domains(["src/markweave/service.py"])
    assert selected == sorted(
        set(DOMAIN_PATTERNS)
        - {"ci-infrastructure", "container", "frontend", "supply-chain"}
        - set(COMPOSE_DOMAINS)
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
    assert select_domains(["Containerfile"]) == ["container", *E2E, "supply-chain"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".containerignore",
        "container/entrypoint.sh",
        "scripts/container/recovery-cli-smoke.sh",
        "scripts/container/smoke.sh",
        "tests/container/test_container_assets.py",
    ],
)
def test_t20_asset_changes_select_container_domain(path: str) -> None:
    selected = select_domains([path])
    assert "container" in selected
    assert "supply-chain" in selected
    if path != "tests/container/test_container_assets.py":
        assert set(E2E) <= set(selected)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "src/markweave/reversions/attempt_main.py",
        "src/markweave/reversions/package.py",
        "src/markweave/__init__.py",
        "src/markweave/version.py",
        "src/markweave/conversion/__init__.py",
        "src/markweave/conversion/errors.py",
        "src/markweave/conversion/images.py",
        "src/markweave/broker/podman_runtime.py",
        "src/markweave/broker_process.py",
        "src/markweave/broker/service.py",
        "src/markweave/broker/inventory.py",
        "src/markweave/broker/models.py",
        "src/markweave/broker/ports.py",
        "src/markweave/broker/errors.py",
        "src/markweave/broker/reconciliation.py",
        "tests/integration/broker/fixtures/Containerfile",
        "tests/integration/broker/fixtures/attempt_main.py",
        "tests/integration/broker/test_podman_runtime_integration.py",
        "tests/integration/broker/test_broker_process_integration.py",
        "tests/integration/broker/test_unix_broker_transport.py",
        "packaging/broker/broker-unix-v1.json.in",
        "packaging/broker/broker-mtls-v2.json.in",
        "packaging/systemd/user/markweave-broker.service",
        "docs/reverse-broker-deployment.md",
        "spikes/anydoc/LICENSE.anydoc",
        "spikes/anydoc/corpus/docx/text.docx",
        "LICENSE",
        "README.md",
    ],
)
def test_reverse_attempt_inputs_select_container_validation(path: str) -> None:
    """Every input baked into or exercised against the attempt image rebuilds it."""

    assert "container" in select_domains([path])


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "packaging/broker/broker-unix-v1.json.in",
        "packaging/broker/broker-mtls-v2.json.in",
        "packaging/systemd/user/markweave-broker.service",
        "docs/reverse-broker-deployment.md",
    ],
)
def test_broker_service_deployment_selects_only_container_domain(path: str) -> None:
    assert select_domains([path]) == ["container", "supply-chain"]


@pytest.mark.unit
def test_anydoc_corpus_selects_real_library_integration() -> None:
    """Changes to real anydoc inputs rerun the document-engine integration domain."""

    assert "document-engines" in select_domains(["spikes/anydoc/corpus/docx/text.docx"])


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "src/markweave/cli/commands/local_conversion.py",
        "src/markweave/cli/local_outputs.py",
        "src/markweave/conversion/engine_versions.py",
        "src/markweave/conversion/local.py",
        "src/markweave/conversion/local_settings.py",
        "examples/local-conversion.env",
        "tests/e2e/local-conversion-final-image.sh",
        "tests/e2e/fixtures/local-publication-sync/sitecustomize.py",
    ],
)
def test_local_conversion_changes_select_its_final_image_e2e(path: str) -> None:
    """T106 local mode reruns its final-image E2E in the container domain."""
    assert "container" in select_domains([path])


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "src/markweave/recovery.py",
        "src/markweave/recovery_service.py",
        "src/markweave/recovery_manifest.py",
        "src/markweave/cli/commands/recovery.py",
    ],
)
def test_recovery_behavior_changes_select_final_image_validation(path: str) -> None:
    """Recovery behavior always exercises both profiles in the final image."""
    selected = select_domains([path])
    assert "container" in selected
    assert set(E2E) <= set(selected)


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
def test_functional_and_storage_suites_run_in_always_run_python_jobs() -> None:
    """The light shards and distributed coverage own these suites on every run.

    They need no path-selected domain, so no changed path can omit them.
    """
    registry = load_registry(Path(".github/ci/domains.json"))
    removed = {"functional", "storage-distributed", "storage-standalone"}
    assert not removed & set(DOMAIN_PATTERNS)
    assert not removed & set(registry)
    workflow = yaml.safe_load(
        Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    jobs = workflow["jobs"]
    for job in ("python-tests", "python-distributed-coverage"):
        assert "if" not in jobs[job]
        assert "needs" not in jobs[job]
    assert "if" not in jobs["python-coverage"]
    assert jobs["python-coverage"]["needs"] == [
        "python-tests",
        "python-distributed-coverage",
    ]
    assert {"python-tests", "python-coverage"} <= set(jobs["gate"]["needs"])
    light = next(
        step["run"]
        for step in jobs["python-tests"]["steps"]
        if step["name"] == "Run a complementary light test shard"
    )
    distributed = next(
        step["run"]
        for step in jobs["python-distributed-coverage"]["steps"]
        if step["name"] == "Run distributed storage coverage"
    )
    assert 'uv run pytest -m "unit or light_coverage"' in light
    assert (
        "uv run pytest tests/integration/postgres tests/integration/s3 "
        "tests/integration/test_local_test_services_lifecycle.py "
        '-m "requires_postgres or requires_s3"'
    ) in distributed


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "web/package.json",
        "web/server.mjs",
        "openapi/v1.json",
        "scripts/container/build-frontend.sh",
        "scripts/container/ci_images.py",
        "scripts/container/pull-base-images.sh",
    ],
)
def test_frontend_inputs_select_rootless_frontend_smoke(path: str) -> None:
    registry = load_registry(Path(".github/ci/domains.json"))
    selected = select_domains([path])
    planned, runnable = classify_domains(selected, registry)
    assert planned == []
    assert "frontend" in runnable
    assert registry["frontend"] == {
        "activation_ticket": "T60",
        "command": [
            "bash",
            "web/scripts/run-rootless-smoke.sh",
            "localhost/md-converter-web:ci",
            "--existing",
        ],
        "status": "active",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "tests/functional/test_auth_api.py",
        "tests/integration/auth/test_http_session.py",
        "tests/integration/sqlite/test_sqlite_jobs.py",
        "tests/integration/postgres/test_postgresql_jobs.py",
        "tests/integration/s3/test_rustfs_profile.py",
        "tests/integration/test_local_test_services_lifecycle.py",
    ],
)
def test_always_run_suite_changes_need_no_path_selected_domain(path: str) -> None:
    """Functional, SQLite, PostgreSQL and S3 tests run in every pull request."""
    assert select_domains([path]) == []


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
        "tests/integration/anydoc/test_compat_adapter.py",
    ],
)
def test_golden_infrastructure_selects_active_document_engine_domain(
    path: str,
) -> None:
    selected = select_domains([path])
    assert "document-engines" in selected
    if path.startswith("tests/corpus/"):
        assert set(E2E) <= set(selected)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "package.json",
        "scripts/run-web-tests.mjs",
        "tests/e2e/browser-next-conversion.test.mjs",
        "tests/e2e/browser-next-admin.test.mjs",
    ],
)
def test_browser_workflow_changes_select_document_engine_domain(path: str) -> None:
    """Browser scripts and tests cannot skip the Chrome-provisioned heavy job."""
    selected = select_domains([path])
    if path == "package.json":
        assert "document-engines" in selected
    assert set(E2E) <= set(selected)


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
            "tests/integration/anydoc",
            "tests/integration/document_engines",
            "-m",
            "integration",
            "--no-cov",
        ],
        "status": "active",
    }


@pytest.mark.unit
def test_t21_e2e_domains_are_active_and_profile_specific() -> None:
    """Each final-image profile runs as complementary shards on loaded images."""
    registry = load_registry(Path(".github/ci/domains.json"))
    assert E2E_DOMAINS == {
        "e2e-standalone-1": ("standalone", 1),
        "e2e-standalone-2": ("standalone", 2),
        "e2e-distributed-1": ("distributed", 1),
        "e2e-distributed-2": ("distributed", 2),
    }
    for domain, (profile, shard) in E2E_DOMAINS.items():
        assert registry[domain] == {
            "activation_ticket": "T21",
            "command": [
                "env",
                f"MARKWEAVE_E2E_SHARD={shard}",
                "MARKWEAVE_E2E_LOCAL_IMAGE=localhost/md-converter:ci",
                "MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE=localhost/md-converter-web:ci",
                "MARKWEAVE_E2E_LOCAL_REVERSE_ATTEMPT_IMAGE="
                "localhost/md-converter-reverse-attempt:ci",
                "bash",
                "scripts/e2e/run.sh",
                profile,
            ],
            "status": "active",
        }


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".containerignore",
        "Containerfile",
        "package.json",
        "pnpm-lock.yaml",
        "playwright.config.mjs",
        "container/entrypoint.sh",
        "scripts/container/build.sh",
        "scripts/e2e/run.sh",
        "scripts/e2e/harness.sh",
        "scripts/e2e/runtime-settings.sh",
        "scripts/e2e/reverse_broker.py",
        "scripts/e2e/s3_backup.py",
        "toolchain/document-engines/package-lock.json",
        "tests/e2e/rootless.spec.mjs",
        "tests/corpus/mermaid/diagram.md",
        "deploy/standalone.yaml.example",
    ],
)
def test_final_image_inputs_select_both_t21_profiles(path: str) -> None:
    """No final-image, driver, deployment, or E2E change can skip one profile."""
    selected = select_domains([path])
    assert set(E2E) <= set(selected)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("scripts/e2e/run-compose.sh", ["compose-secure"]),
        ("scripts/e2e/run-compose-all.sh", sorted(COMPOSE_DOMAINS)),
        (
            "scripts/e2e/run-compose-simple.sh",
            ["compose-simple-docker", "compose-simple-podman"],
        ),
        ("scripts/e2e/run-compose-podman-insecure.sh", ["compose-podman-insecure"]),
    ],
)
def test_compose_runner_changes_select_only_the_compose_domain(
    path: str, expected: list[str]
) -> None:
    """Self-contained Compose runners never execute in the final-image profiles."""
    assert select_domains([path]) == expected


@pytest.mark.unit
def test_compose_runner_exclusion_does_not_hide_other_final_image_inputs() -> None:
    selected = select_domains(["scripts/e2e/run-compose.sh", "scripts/e2e/run.sh"])
    assert selected == ["compose-secure", *E2E]


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
    data["document-engines"] = entry
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
        set(DOMAIN_PATTERNS)
        - {"ci-infrastructure", "container", "frontend", "supply-chain"}
        - set(COMPOSE_DOMAINS)
    )
    assert json.loads(values["runnable-domains"]) == []
    assert json.loads(values["heavy-domains"]) == []
    assert json.loads(values["image-domains"]) == []
    assert json.loads(values["image-roles"]) == []


@pytest.mark.unit
def test_cli_partitions_runnable_domains_between_heavy_and_image_jobs(
    tmp_path: Path,
) -> None:
    """Runnable domains are split exhaustively and image roles are derived."""
    paths = tmp_path / "paths"
    paths.write_bytes(b"web/server.mjs\0compose.yaml\0")
    output = tmp_path / "output"
    assert (
        main(
            [
                "--paths-file",
                str(paths),
                "--registry",
                ".github/ci/domains.json",
                "--github-output",
                str(output),
            ]
        )
        == 0
    )
    values = {
        key: json.loads(value)
        for key, value in (
            line.split("=", maxsplit=1) for line in output.read_text().splitlines()
        )
    }
    assert values["heavy-domains"] == sorted(COMPOSE_DOMAINS)
    assert values["image-domains"] == ["frontend"]
    assert values["image-roles"] == ["frontend"]
    assert (
        sorted(values["heavy-domains"] + values["image-domains"])
        == (values["runnable-domains"])
    )


@pytest.mark.unit
def test_partition_covers_every_domain_exactly_once_with_known_roles() -> None:
    heavy, image, roles = partition_domains(sorted(DOMAIN_PATTERNS))
    assert sorted(heavy + image) == sorted(DOMAIN_PATTERNS)
    assert not set(heavy) & set(image)
    assert image == sorted(IMAGE_DOMAIN_ROLES)
    assert roles == sorted(IMAGE_ROLES)
    assert set(IMAGE_DOMAIN_ROLES) <= set(DOMAIN_PATTERNS)
    assert partition_domains(["supply-chain", "document-engines"]) == (
        ["document-engines"],
        ["supply-chain"],
        ["backend", "reverse-attempt"],
    )
    assert partition_domains([]) == ([], [], [])


@pytest.mark.unit
@pytest.mark.parametrize("path", ["compose.test.yaml", "scripts/testing/services.py"])
def test_local_services_select_ci_infrastructure(path: str) -> None:
    assert select_domains([path]) == ["ci-infrastructure"]
