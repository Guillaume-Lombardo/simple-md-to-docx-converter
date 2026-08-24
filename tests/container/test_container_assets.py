"""Static contracts for the T20 final-image assets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

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
