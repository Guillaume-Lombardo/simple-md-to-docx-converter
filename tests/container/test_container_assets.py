"""Static contracts for the T20 final-image assets."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.container import summarize_supply_chain

pytestmark = pytest.mark.unit


def test_container_domain_is_active_and_runs_rootless_harness() -> None:
    registry = json.loads(Path(".github/ci/domains.json").read_text(encoding="utf-8"))
    assert registry["container"] == {
        "activation_ticket": "T20",
        "command": ["bash", "scripts/container/run-ci.sh"],
        "status": "active",
    }


@pytest.mark.parametrize(
    "script",
    [
        "container/entrypoint.sh",
        "container/preflight.sh",
        "scripts/container/build.sh",
        "scripts/container/blocking-mmdc.sh",
        "scripts/container/api-smoke.sh",
        "scripts/container/distributed-api-smoke.sh",
        "scripts/container/run-ci.sh",
        "scripts/container/smoke.sh",
        "scripts/container/supply-chain.sh",
    ],
)
def test_container_shell_assets_are_syntactically_valid(script: str) -> None:
    subprocess.run(["bash", "-n", script], check=True)


def test_final_image_pins_all_downloaded_artifacts() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")
    assert "ubi9/python-314@sha256:" in containerfile
    for artifact in ("PANDOC", "CHROME", "LIBREOFFICE", "UV"):
        assert f"ARG {artifact}_SHA256=" in containerfile
    assert "rpm --checksig /tmp/google-chrome.rpm" in containerfile
    assert "RPM_INVENTORY_SHA256" in containerfile
    assert "uv sync --locked --no-dev --no-editable" in containerfile


def test_entrypoint_contract_has_only_the_three_approved_modes() -> None:
    entrypoint = Path("container/entrypoint.sh").read_text(encoding="utf-8")
    assert "api|embedded-worker|external-worker" in entrypoint
    assert "md_converter.runtime" in entrypoint
    assert "md-converter-preflight" in entrypoint


def test_smoke_enforces_rootless_read_only_bounded_runtime() -> None:
    smoke = Path("scripts/container/smoke.sh").read_text(encoding="utf-8")
    for contract in (
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--memory=768m",
        "--pids-limit=256",
        "--tmpfs /tmp:",
        "--tmpfs /work:",
        "--shm-size=128m",
    ):
        assert contract in smoke


@pytest.mark.parametrize(
    ("manifest", "mode"),
    [
        ("deploy/standalone.yaml.example", "embedded-worker"),
        ("deploy/distributed.yaml.example", "external-worker"),
    ],
)
def test_deployment_examples_apply_worker_security_and_t18_limits(
    manifest: str, mode: str
) -> None:
    documents = tuple(yaml.safe_load_all(Path(manifest).read_text(encoding="utf-8")))
    worker = next(
        container
        for document in documents
        for container in document["spec"]["template"]["spec"]["containers"]
        if container["args"] == [mode]
    )
    security = worker["securityContext"]
    assert security == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }
    assert worker["resources"]["limits"] == {
        "memory": "${WORKER_MEMORY_BUDGET_BYTES}",
        "ephemeral-storage": "${WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES}",
    }


def test_distributed_test_profile_is_provider_neutral_rustfs() -> None:
    deployment = Path("deploy/rustfs-ci.yaml").read_text(encoding="utf-8")
    assert "ghcr.io/rustfs/rustfs:" in deployment
    assert "minio" not in deployment.casefold()


def test_distributed_smoke_covers_active_shutdown_and_lease_recovery() -> None:
    smoke = Path("scripts/container/distributed-api-smoke.sh").read_text(
        encoding="utf-8"
    )
    for contract in (
        "--submit-blocking-job",
        "blocking-mmdc.sh",
        "podman stop --time 8",
        "{{.State.Pid}}",
        "--assert-running-job",
        "--recover-job",
    ):
        assert contract in smoke


def test_supply_chain_retains_complete_scan_and_ci_evidence() -> None:
    script = Path("scripts/container/supply-chain.sh").read_text(encoding="utf-8")
    workflow = yaml.safe_load(
        Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    assert "--only-fixed" not in script
    assert "vulnerabilities.json" in script
    heavy_steps = workflow["jobs"]["heavy"]["steps"]
    upload = next(
        step
        for step in heavy_steps
        if step["name"] == "Retain final-image verification evidence"
    )
    assert upload["if"] == "${{ always() && matrix.domain == 'container' }}"
    assert upload["with"]["retention-days"] == 30
    assert upload["with"]["if-no-files-found"] == "error"
    for artifact in (
        "sbom.cdx.json",
        "sbom.spdx.json",
        "vulnerabilities.json",
        "image-metadata.json",
    ):
        assert artifact in upload["with"]["path"]
    assert workflow["permissions"] == {"contents": "read"}


def test_supply_chain_summary_gates_fixable_and_records_unfixed_critical(
    mocker, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("sbom.cdx.json", "sbom.spdx.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    report = {
        "matches": [
            {
                "artifact": {"name": "fixable"},
                "vulnerability": {
                    "id": "CVE-FIXED",
                    "severity": "Critical",
                    "fix": {"versions": ["2"]},
                },
            },
            {
                "artifact": {"name": "unfixed"},
                "vulnerability": {
                    "id": "CVE-UNFIXED",
                    "severity": "Critical",
                    "fix": {"versions": []},
                },
            },
        ]
    }
    (tmp_path / "vulnerabilities.json").write_text(json.dumps(report), encoding="utf-8")
    inspected = mocker.patch("scripts.container.summarize_supply_chain.subprocess.run")
    inspected.return_value.stdout = json.dumps(
        [{"Id": "sha256:image", "Digest": "sha256:digest", "Size": 123}]
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["summary", "--image", "image:test", "--artifacts", str(tmp_path)],
    )

    assert summarize_supply_chain.main() == 1
    evidence = json.loads((tmp_path / "image-metadata.json").read_text())
    assert evidence["vulnerabilities"]["counts_by_severity"] == {"Critical": 2}
    assert evidence["vulnerabilities"]["critical_with_fix"][0]["id"] == "CVE-FIXED"
    assert evidence["vulnerabilities"]["critical_without_fix"][0]["id"] == "CVE-UNFIXED"
