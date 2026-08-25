"""Static contracts for the casual-user Compose quickstart and its real E2E runner."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
README = ROOT / "README.md"
RUNNER = ROOT / "scripts/e2e/run-compose.sh"
FINAL_IMAGE_RUNNER = ROOT / "scripts/e2e/run.sh"
TEMPLATE = ROOT / "examples/quickstart-template.docx.base64"
SOURCE = ROOT / "examples/quickstart-source.md"
EXPECTED_FONTS = (
    "Aptos",
    "Aptos Display",
    "Calibri",
    "Cambria",
    "Cambria Math",
    "Consolas",
    "Courier New",
    "Times New Roman",
)
MARKWEAVE_DIGEST = (
    "ghcr.io/guillaume-lombardo/md-converter:0.3.0@"
    "sha256:4a16b311affb0d0a839350bd145810c1f6044cc7347d12ecd9263fe894de217d"
)
CLAMAV_DIGEST = (
    "docker.io/clamav/clamav-debian:1.4_base@"
    "sha256:c01d064057453645c5992105ef7958155afc462f87eec6894d475168f18d3c0b"
)
TEMPLATE_SHA256 = "1f3b39249773bee382a816006fa6bf040a156ff4dd572c5c9f1de674436eb11f"


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
    assert application["ports"] == ["127.0.0.1:${MARKWEAVE_PORT:-8080}:8080"]
    assert set(application["volumes"]) == {
        "markweave-data:/data",
        "markweave-work:/work",
    }

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

    assert set(document["volumes"]) == {
        "markweave-data",
        "markweave-work",
        "clamav-signatures",
    }
    assert document["networks"]["scanner"]["internal"] is True
    assert document["networks"]["frontend"]["driver_opts"] == {
        "com.docker.network.bridge.enable_ip_masquerade": "false"
    }
    assert set(application["networks"]) == {"scanner", "frontend"}
    assert set(scanner["networks"]) == {"scanner", "signature-updates"}


def test_application_has_disk_workspace_and_memory_headroom() -> None:
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
    }
    assert application["shm_size"] == "128m"
    assert application["mem_limit"] == "1g"
    assert environment["MD_CONVERTER_WORKER_MEMORY_BUDGET_BYTES"] == "805306368"
    assert environment["MD_CONVERTER_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES"] == (
        "268435456"
    )
    assert environment["MD_CONVERTER_INITIAL_ADMIN_PASSWORD"].startswith(
        "${MARKWEAVE_INITIAL_ADMIN_PASSWORD:?"
    )
    assert environment["MD_CONVERTER_CLAMAV_HOST"] == "clamav"
    assert environment["MD_CONVERTER_STORAGE_PROFILE"] == "standalone"
    assert environment["MD_CONVERTER_STANDALONE_DATA_DIRECTORY"] == "/data"


def test_committed_quickstart_fixture_is_stable_docx_with_declared_fonts() -> None:
    encoded = TEMPLATE.read_bytes()
    template = base64.b64decode(b"".join(encoded.splitlines()), validate=True)
    assert hashlib.sha256(template).hexdigest() == TEMPLATE_SHA256
    with zipfile.ZipFile(io.BytesIO(template)) as archive:
        assert "[Content_Types].xml" in archive.namelist()
        assert "word/document.xml" in archive.namelist()
        declarations = b"\n".join(
            archive.read(name) for name in archive.namelist() if name.endswith(".xml")
        ).decode("utf-8")
    for family in EXPECTED_FONTS:
        assert family in declarations
    assert SOURCE.read_text(encoding="utf-8") == (
        "# My first document\n\nHello from **Markweave**.\n"
    )


def test_readme_uses_reproducible_template_and_safe_password_file() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "--env-file /tmp/markweave-quickstart.env up -d" in readme
    assert "http://localhost:8080" in readme
    assert "**Templates**" in readme
    assert "**Administration**" not in readme
    assert "examples/quickstart-template.docx.base64" in readme
    assert "examples/quickstart-source.md" in readme
    assert ", ".join(EXPECTED_FONTS) in readme
    assert "markweave_markweave-work" in readme
    assert 'com.docker.compose.volume" }}' in readme
    assert "not a production deployment" in readme
    assert "Linux/AMD64 only" in readme
    assert "docker compose down --volumes" not in readme
    assert "export MARKWEAVE_INITIAL_ADMIN_PASSWORD" not in readme


def test_compose_e2e_is_isolated_and_exercises_real_restart_workflow() -> None:
    runner = RUNNER.read_text(encoding="utf-8")

    assert '--project-name "$project"' in runner
    assert "MARKWEAVE_PORT=%s\\n" in runner
    assert "docker compose" in runner
    assert "tests.e2e.service_workflow checkpoint" in runner
    assert "tests.e2e.service_workflow verify-checkpoint" in runner
    assert 'docker port "$application_id" 8080/tcp' in runner
    assert 'docker port "$scanner_id"' in runner
    assert 'socket.create_connection(("clamav", 3310), 5)' in runner
    assert 'socket.create_connection(("1.1.1.1", 443), 2)' in runner
    assert 'volume="${project}_markweave-work"' in runner
    assert 'com.docker.compose.project" }}' in runner
    assert 'com.docker.compose.volume" }}' in runner
    assert "down --volumes --remove-orphans" in runner
    assert "down --remove-orphans" in runner


def test_standalone_final_image_rejects_spoofed_proxy_origin_headers() -> None:
    runner = FINAL_IMAGE_RUNNER.read_text(encoding="utf-8")

    assert "MD_CONVERTER_PUBLIC_ORIGIN=http://127.0.0.1:8080" in runner
    assert 'login("http://127.0.0.1:8080")' in runner
    assert 'login("https://attacker.example")' in runner
    assert '"Forwarded": "host=attacker.example;proto=https"' in runner
    assert '"X-Forwarded-Host": "attacker.example"' in runner
    assert '"X-Forwarded-Proto": "https"' in runner
    assert "assert accepted_status == 200" in runner
    assert "assert hostile_status == 403" in runner
    assert '"LOGIN_ORIGIN_INVALID"' in runner
