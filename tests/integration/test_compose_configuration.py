"""Real Compose rendering checks for the quickstart configuration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from markweave.config import MalwareScanningMode, Settings
from tests.quickstart_compose_modes import SUPPORTED_MODES, ComposeMode

pytestmark = [pytest.mark.integration, pytest.mark.requires_docker]

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_BACKEND = "localhost/markweave-backend:t64"
CANDIDATE_FRONTEND = "localhost/markweave-frontend:t64"
PUBLISHED_BACKEND = (
    "ghcr.io/guillaume-lombardo/md-converter:0.7.5@"
    "sha256:237597e5aee89a744f29027e3ad0e10845296f435da8902d1ee5af2b7f591677"
)
PUBLISHED_FRONTEND = (
    "ghcr.io/guillaume-lombardo/md-converter-web:0.7.5@"
    "sha256:f628055556c61b04cfbd3202e32464ad38719d7b057ea87ed9b249e44c6d01ce"
)


def _render_mode(mode: ComposeMode, port: str) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--project-name",
        "markweave-mode-contract",
        "--project-directory",
        str(ROOT),
    ]
    for name in mode.files:
        command += ["--file", str(ROOT / name)]
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("MARKWEAVE_", "MD_CONVERTER_"))
    } | {
        "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
        "MARKWEAVE_PORT": port,
        "MARKWEAVE_PUBLIC_ORIGIN": f"http://localhost:{port}",
        "MARKWEAVE_ROUTER_PUBLIC_HOST": f"localhost:{port}",
        "MARKWEAVE_INSECURE_EVALUATION_MODE": str(mode.insecure).lower(),
        "MARKWEAVE_WORK_DEVICE": "/dev/null",
    }
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", SUPPORTED_MODES, ids=lambda mode: mode.name)
def test_supported_mode_keeps_the_reviewed_security_posture(
    mode: ComposeMode,
) -> None:
    """Every supported stack renders one loopback router and its scanner boundary."""
    services: dict[str, Any] = _render_mode(mode, "11279")["services"]
    application = services["markweave"]
    environment = application["environment"]

    published = [
        (name, port)
        for name, service in services.items()
        for port in service.get("ports", [])
    ]
    assert len(published) == 1
    owner, port = published[0]
    assert port["host_ip"] == "127.0.0.1"
    assert port["published"] == "11279"
    if mode.runtime == "podman" and mode.trusted_upstream:
        assert (owner, port["target"]) == ("markweave", 3100)
        assert application["network_mode"] == "slirp4netns"
        assert services["router"]["network_mode"] == "service:markweave"
        assert services["frontend"]["network_mode"] == "service:markweave"
    else:
        assert (owner, port["target"]) == ("router", 8080)
        assert "network_mode" not in application

    assert application["image"] == PUBLISHED_BACKEND
    assert services["frontend"]["image"] == PUBLISHED_FRONTEND
    assert services["router"]["image"] == PUBLISHED_FRONTEND
    for service in services.values():
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]

    if mode.trusted_upstream:
        assert "clamav" not in services
        assert "clamav" not in application.get("depends_on", {})
        assert environment["MARKWEAVE_MALWARE_SCANNING_MODE"] == "trusted-upstream"
    else:
        assert "ports" not in services["clamav"]
        assert "clamav" in application["depends_on"]
        assert "MARKWEAVE_MALWARE_SCANNING_MODE" not in environment
    assert environment["MARKWEAVE_INSECURE_EVALUATION_MODE"] == (
        str(mode.insecure).lower()
    )
    assert ("MARKWEAVE_COMPOSER_ENABLED" in environment) is mode.composer
    if mode.runtime == "podman":
        assert application["healthcheck"]["disable"] is True
        assert (
            services["router"]["depends_on"]["markweave"]["condition"]
            == "service_started"
        )
    else:
        assert "seccomp" in " ".join(application["security_opt"])
        assert (
            services["router"]["depends_on"]["markweave"]["condition"]
            == "service_healthy"
        )


def test_composer_candidate_overlay_does_not_change_published_quickstart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = [
        "docker",
        "compose",
        "--project-name",
        "markweave-compose-contract",
        "--file",
        str(ROOT / "compose.yaml"),
        "--file",
        str(ROOT / "compose.simple.yaml"),
    ]
    environment = os.environ | {
        "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
        "MARKWEAVE_WORK_DEVICE": "/dev/null",
    }

    def render(files: list[str]) -> dict[str, Any]:
        result = subprocess.run(
            [*base, *files, "config", "--format", "json"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return json.loads(result.stdout)

    published = render([])["services"]["markweave"]
    assert published["image"].startswith(
        "ghcr.io/guillaume-lombardo/md-converter:0.7.5@sha256:"
    )
    assert "MARKWEAVE_COMPOSER_ENABLED" not in published["environment"]
    assert not _load_rendered_settings(
        monkeypatch, published["environment"]
    ).composer_enabled

    candidate = render(["--file", str(ROOT / "compose.simple-composer.yaml")])[
        "services"
    ]["markweave"]
    settings = _load_rendered_settings(monkeypatch, candidate["environment"])
    assert settings.composer_enabled and settings.composer_admin_policy_delegated
    assert "composer-egress" in candidate["networks"]
    assert any(
        mount.get("source") == "markweave-composer-key" and mount.get("read_only")
        for mount in candidate["volumes"]
    )


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
            "target": 3100,
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
    assert frontend["depends_on"]["markweave"]["condition"] == "service_healthy"
    assert "volumes" not in frontend
    assert router["image"] == CANDIDATE_FRONTEND
    assert router["command"] == ["node", "router.mjs"]
    assert router["depends_on"]["frontend"]["condition"] == "service_healthy"
    assert router["depends_on"]["markweave"]["condition"] == "service_healthy"
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
        services["router"]["depends_on"]["frontend"]["condition"] == "service_started"
    )


def test_nextjs_podman_shared_namespace_exposes_router_to_host_forwarding() -> None:
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
    assert router["depends_on"]["frontend"]["condition"] == "service_started"
    assert router["environment"]["ROUTER_HOST"] == "0.0.0.0"  # noqa: S104
    assert router["environment"]["ROUTER_PORT"] == "3100"
    assert backend["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 3100,
            "published": "11279",
            "protocol": "tcp",
        }
    ]
    assert backend["network_mode"] == "slirp4netns"
    assert router["network_mode"] == "service:markweave"
    assert "ports" not in router
    assert "http://127.0.0.1:3100/login" in router["healthcheck"]["test"][-1]
    assert "http://127.0.0.1:8080/login" not in router["healthcheck"]["test"][-1]
