"""Real Compose rendering checks for the quickstart configuration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from markweave.config import MalwareScanningMode, Settings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_BACKEND = "localhost/markweave-backend:t64"
CANDIDATE_FRONTEND = "localhost/markweave-frontend:t64"


def _load_rendered_settings(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> Settings:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(f"MARKWEAVE_{field_name.upper()}", raising=False)
        monkeypatch.delenv(f"MD_CONVERTER_{field_name.upper()}", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return Settings.load()


def test_trusted_upstream_podman_renders_custom_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the published port and strict login origin aligned."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-compose-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.podman.yaml"),
            "--file",
            str(ROOT / "compose.trusted-upstream.yaml"),
            "--file",
            str(ROOT / "compose.podman-trusted-upstream.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_PORT": "11279",
            "MARKWEAVE_PUBLIC_ORIGIN": "https://converter.example",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    document: dict[str, Any] = json.loads(result.stdout)
    application = document["services"]["markweave"]

    assert application["environment"]["MARKWEAVE_PUBLIC_ORIGIN"] == (
        "https://converter.example"
    )
    assert application["environment"]["MARKWEAVE_INSECURE_EVALUATION_MODE"] == "false"
    assert (
        application["environment"]["MARKWEAVE_MALWARE_SCANNING_MODE"]
        == "trusted-upstream"
    )
    assert not any(
        name.startswith("MD_CONVERTER_") for name in application["environment"]
    )
    assert (
        _load_rendered_settings(
            monkeypatch, application["environment"]
        ).malware_scanning_mode
        is MalwareScanningMode.TRUSTED_UPSTREAM
    )
    assert application["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8080,
            "published": "11279",
            "protocol": "tcp",
        }
    ]
    assert application["network_mode"] == "slirp4netns"


def test_clamav_free_compose_can_enable_insecure_evaluation_mode() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-insecure-compose-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.trusted-upstream.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_INSECURE_EVALUATION_MODE": "true",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    document: dict[str, Any] = json.loads(result.stdout)

    assert (
        document["services"]["markweave"]["environment"][
            "MARKWEAVE_INSECURE_EVALUATION_MODE"
        ]
        == "true"
    )
    assert not any(
        name.startswith("MD_CONVERTER_")
        for name in document["services"]["markweave"]["environment"]
    )
    assert "clamav" not in document["services"]["markweave"].get("depends_on", {})


def test_nextjs_cutover_renders_one_public_router_and_isolated_frontend() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-nextjs-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.nextjs.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_CUTOVER_BACKEND_IMAGE": CANDIDATE_BACKEND,
            "MARKWEAVE_CUTOVER_FRONTEND_IMAGE": CANDIDATE_FRONTEND,
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_PORT": "11279",
            "MARKWEAVE_PUBLIC_ORIGIN": "http://localhost:11279",
            "MARKWEAVE_ROUTER_PUBLIC_HOST": "localhost:11279",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    services: dict[str, Any] = json.loads(result.stdout)["services"]
    backend = services["markweave"]
    frontend = services["frontend"]
    router = services["router"]

    assert backend["image"] == CANDIDATE_BACKEND
    assert "ports" not in backend
    assert frontend["image"] == CANDIDATE_FRONTEND
    assert set(frontend["networks"]) == {"frontend"}
    assert frontend["environment"] == {
        "HOSTNAME": "0.0.0.0"  # noqa: S104 - rendered container binding
    }
    assert "volumes" not in frontend
    assert router["image"] == CANDIDATE_FRONTEND
    assert router["command"] == ["node", "router.mjs"]
    assert router["environment"] == {
        "BACKEND_ORIGIN": "http://markweave:8080",
        "FRONTEND_ORIGIN": "http://frontend:3000",
        "PUBLIC_HOSTS": "localhost:11279",
        "ROUTER_HOST": "0.0.0.0",  # noqa: S104 - rendered deployment binding
        "ROUTER_PORT": "8080",
        "ROUTER_REQUEST_MAX_BYTES": "1100000",
        "ROUTER_UPSTREAM_TIMEOUT_MS": "30000",
    }
    assert router["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8080,
            "published": "11279",
            "protocol": "tcp",
        }
    ]


def test_nextjs_podman_uses_staged_backend_readiness_without_compose_health() -> None:
    """The real Podman command must not require its disabled backend healthcheck."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-nextjs-podman-staged-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.podman.yaml"),
            "--file",
            str(ROOT / "compose.nextjs.yaml"),
            "--file",
            str(ROOT / "compose.nextjs-podman.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_CUTOVER_BACKEND_IMAGE": CANDIDATE_BACKEND,
            "MARKWEAVE_CUTOVER_FRONTEND_IMAGE": CANDIDATE_FRONTEND,
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_PORT": "11279",
            "MARKWEAVE_PUBLIC_ORIGIN": "http://localhost:11279",
            "MARKWEAVE_ROUTER_PUBLIC_HOST": "localhost:11279",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    services: dict[str, Any] = json.loads(result.stdout)["services"]

    assert services["markweave"]["healthcheck"]["disable"] is True
    assert (
        services["frontend"]["depends_on"]["markweave"]["condition"]
        == "service_started"
    )
    assert (
        services["router"]["depends_on"]["markweave"]["condition"] == "service_started"
    )
    assert (
        services["router"]["depends_on"]["frontend"]["condition"] == "service_healthy"
    )


def test_nextjs_podman_shared_namespace_healthcheck_targets_the_router() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-nextjs-podman-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.podman.yaml"),
            "--file",
            str(ROOT / "compose.trusted-upstream.yaml"),
            "--file",
            str(ROOT / "compose.podman-trusted-upstream.yaml"),
            "--file",
            str(ROOT / "compose.nextjs.yaml"),
            "--file",
            str(ROOT / "compose.nextjs-podman.yaml"),
            "--file",
            str(ROOT / "compose.nextjs-podman-trusted-upstream.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_CUTOVER_BACKEND_IMAGE": CANDIDATE_BACKEND,
            "MARKWEAVE_CUTOVER_FRONTEND_IMAGE": CANDIDATE_FRONTEND,
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_PORT": "11279",
            "MARKWEAVE_PUBLIC_ORIGIN": "http://localhost:11279",
            "MARKWEAVE_ROUTER_PUBLIC_HOST": "localhost:11279",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    services = json.loads(result.stdout)["services"]
    backend = services["markweave"]
    frontend = services["frontend"]
    router = services["router"]

    assert backend["healthcheck"]["disable"] is True
    assert frontend["depends_on"]["markweave"]["condition"] == "service_started"
    assert router["depends_on"]["markweave"]["condition"] == "service_started"
    assert router["depends_on"]["frontend"]["condition"] == "service_healthy"
    assert router["environment"]["ROUTER_PORT"] == "3100"
    assert "http://127.0.0.1:3100/login" in router["healthcheck"]["test"][-1]
    assert "http://127.0.0.1:8080/login" not in router["healthcheck"]["test"][-1]
