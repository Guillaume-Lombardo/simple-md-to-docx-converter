"""Static contract for the casual-user Compose quickstart."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
README = ROOT / "README.md"
MARKWEAVE_DIGEST = (
    "ghcr.io/guillaume-lombardo/md-converter:0.3.0@"
    "sha256:4a16b311affb0d0a839350bd145810c1f6044cc7347d12ecd9263fe894de217d"
)
CLAMAV_DIGEST = (
    "docker.io/clamav/clamav-debian:1.4_base@"
    "sha256:c01d064057453645c5992105ef7958155afc462f87eec6894d475168f18d3c0b"
)


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_quickstart_uses_immutable_real_services_and_persistent_data() -> None:
    document = _compose()
    services = document["services"]
    application = services["markweave"]
    scanner = services["clamav"]

    assert application["image"] == MARKWEAVE_DIGEST
    assert application["platform"] == "linux/amd64"
    assert application["command"] == "embedded-worker"
    assert application["depends_on"]["clamav"]["condition"] == "service_healthy"
    assert application["ports"] == ["127.0.0.1:8080:8080"]
    assert "markweave-data:/data" in application["volumes"]

    assert scanner["image"] == CLAMAV_DIGEST
    assert scanner["healthcheck"]["test"] == [
        "CMD",
        "/usr/local/bin/clamdcheck.sh",
    ]
    assert set(scanner["cap_add"]) == {
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETGID",
        "SETUID",
    }
    assert "clamav-signatures:/var/lib/clamav" in scanner["volumes"]
    assert "ports" not in scanner

    assert set(document["volumes"]) == {"markweave-data", "clamav-signatures"}
    assert document["networks"]["scanner"]["internal"] is True
    assert document["networks"]["frontend"]["driver_opts"] == {
        "com.docker.network.bridge.enable_ip_masquerade": "false"
    }
    assert set(application["networks"]) == {"scanner", "frontend"}
    assert set(scanner["networks"]) == {"scanner", "signature-updates"}


def test_application_runtime_is_fail_closed_and_requires_a_password() -> None:
    application = _compose()["services"]["markweave"]
    environment = application["environment"]

    assert application["read_only"] is True
    assert application["user"] == "1001:0"
    assert application["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in application["security_opt"]
    assert (
        "seccomp=./spikes/toolchain/chrome-seccomp.json" in application["security_opt"]
    )
    assert {entry.split(":", 1)[0] for entry in application["tmpfs"]} == {
        "/tmp",  # noqa: S108 - asserting the required isolated container mount
        "/work",
    }
    assert environment["MD_CONVERTER_INITIAL_ADMIN_PASSWORD"].startswith(
        "${MARKWEAVE_INITIAL_ADMIN_PASSWORD:?"
    )
    assert environment["MD_CONVERTER_CLAMAV_HOST"] == "clamav"
    assert environment["MD_CONVERTER_STORAGE_PROFILE"] == "standalone"
    assert environment["MD_CONVERTER_STANDALONE_DATA_DIRECTORY"] == "/data"


def test_readme_leads_to_a_first_conversion_without_production_claims() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "docker compose up -d" in readme
    assert "http://localhost:8080" in readme
    assert "Administration" in readme
    assert "Expected fonts" in readme
    assert "not a production deployment" in readme
    assert "Linux/AMD64 only" in readme
    assert "docker compose down --volumes" not in readme
