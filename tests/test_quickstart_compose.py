"""Static contracts for the casual-user Compose quickstart and its real E2E runner."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import stat
import subprocess
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
QUICKSTART = ROOT / "scripts/quickstart.sh"
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
    assert "restart" not in application
    assert application["depends_on"]["clamav"]["condition"] == "service_healthy"
    assert application["ports"] == ["127.0.0.1:${MARKWEAVE_PORT:-8080}:8080"]
    assert set(application["volumes"]) == {
        "markweave-data:/data",
        "markweave-work:/work",
    }

    assert scanner["image"] == CLAMAV_DIGEST
    assert "restart" not in scanner
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
    assert document["volumes"]["markweave-work"] == {
        "driver": "local",
        "driver_opts": {
            "type": "ext4",
            "device": "${MARKWEAVE_WORK_DEVICE:?Run scripts/quickstart.sh up}",
            "o": "rw,nosuid,nodev",
        },
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

    assert "scripts/quickstart.sh up" in readme
    assert "scripts/quickstart.sh down" in readme
    assert "scripts/quickstart.sh password" in readme
    assert "http://localhost:8080" in readme
    assert "**Templates**" in readme
    assert "**Administration**" not in readme
    assert "examples/quickstart-template.docx.base64" in readme
    assert "examples/quickstart-source.md" in readme
    assert ", ".join(EXPECTED_FONTS) in readme
    assert "exact 256 MiB ext4 loop" in readme
    assert "rootful Docker Engine" in readme
    assert "unix:///var/run/docker.sock" in readme
    assert "`DOCKER_HOST` and remote or non-default Docker contexts" in readme
    assert "Docker Desktop" in readme
    assert "rootless Docker" in readme
    assert "do not automatically restart" in readme
    assert "never from a stale" in readme
    assert "reused for an unrelated file" in readme
    assert "requests `sudo`" in readme
    assert "not a production deployment" in readme
    assert "AMD64 Linux host" in readme
    assert "docker compose down --volumes" not in readme
    assert "export MARKWEAVE_INITIAL_ADMIN_PASSWORD" not in readme


def test_quickstart_script_uses_private_create_once_state_and_exact_cleanup() -> None:
    script = QUICKSTART.read_text(encoding="utf-8")

    assert "${XDG_STATE_HOME:-$HOME/.local/state}" in script
    assert 'mktemp "$state_directory/password.XXXXXX"' in script
    assert "openssl rand -hex 24" in script
    assert '[[ ! -e "$password_file" ]]' in script
    assert 'chmod 0600 -- "$password_file"' in script
    assert '[[ -f "$path" && ! -L "$path" && -O "$path" ]]' in script
    assert 'truncate -s "$work_bytes"' in script
    assert "readonly work_bytes=268435456" in script
    assert 'readonly project="${MARKWEAVE_QUICKSTART_PROJECT:-markweave}"' in script
    assert 'readonly port="${MARKWEAVE_QUICKSTART_PORT:-8080}"' in script
    assert "Docker Desktop is not supported" in script
    assert "Rootless Docker is not supported" in script
    assert '[[ -z "${DOCKER_HOST:-}" ]]' in script
    assert '[[ "$context" == default ]]' in script
    assert "unix:///var/run/docker.sock" in script
    assert "docker context inspect" in script
    assert "{{.Architecture}}" in script
    assert '[[ "$(uname -m)" == x86_64 || "$(uname -m)" == amd64 ]]' in script
    assert "mkfs.ext4" in script
    assert "readonly losetup=/usr/sbin/losetup" in script
    assert 'sudo "$losetup" --find --show' in script
    assert 'sudo "$losetup" --detach' in script
    assert 'com.docker.compose.project" }}' in script
    assert 'com.docker.compose.volume" }}' in script
    assert 'index .Options "device"' in script
    assert 'index .Options "o"' in script
    assert 'docker container ls --all --quiet --filter "volume=$work_volume"' in script
    assert "Refusing to remove a work volume that is still used" in script
    assert 'docker volume rm "$work_volume"' in script
    assert "resize_stopped_work_image" in script
    assert "format_work_device" in script
    assert 'device_backs_work_image "$device"' in script
    assert "down --remove-orphans" in script
    assert "down --volumes" not in script
    assert "markweave-data" not in script
    assert "clamav-signatures" not in script
    assert "/quickstart-template.docx" in (ROOT / ".gitignore").read_text()


def test_quickstart_password_is_create_once_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    environment = os.environ | {"XDG_STATE_HOME": str(tmp_path / "state")}
    command = [str(QUICKSTART), "password"]

    first = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    second = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()
    state = tmp_path / "state" / "markweave-quickstart"
    password = state / "password.env"

    assert first == second
    assert len(first) == 48
    assert all(character in "0123456789abcdef" for character in first)
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(password.stat().st_mode) == 0o600

    password.unlink()
    target = tmp_path / "redirected"
    target.write_text("unchanged", encoding="utf-8")
    password.symlink_to(target)
    rejected = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected.returncode != 0
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_compose_e2e_is_isolated_and_exercises_real_restart_workflow() -> None:
    runner = RUNNER.read_text(encoding="utf-8")

    assert '"XDG_STATE_HOME=$state_home"' in runner
    assert '"MARKWEAVE_QUICKSTART_PROJECT=$project"' in runner
    assert '"MARKWEAVE_QUICKSTART_PORT=$port"' in runner
    assert '"$quickstart_script"' in runner
    assert runner.count("quickstart up") == 5
    assert runner.count("quickstart down") >= 2
    assert "docker compose" in runner
    assert "tests.e2e.service_workflow checkpoint" in runner
    assert "tests.e2e.service_workflow verify-checkpoint" in runner
    assert 'docker port "$application_id" 8080/tcp' in runner
    assert 'docker port "$scanner_id"' in runner
    assert 'socket.create_connection(("clamav", 3310), 5)' in runner
    assert 'socket.create_connection(("1.1.1.1", 443), 2)' in runner
    assert "capacity = stats.f_blocks * stats.f_frsize" in runner
    assert "errno.ENOSPC" in runner
    assert "268_435_456" in runner
    assert 'readonly work_volume="${project}_markweave-work"' in runner
    assert "com.docker.compose.project=$project" in runner
    assert "com.docker.compose.volume=markweave-work" in runner
    assert 'index .Options "device"' in runner
    assert "sudo /usr/sbin/losetup --detach" in runner
    assert "unrelated_device" in runner
    assert "unrelated_down_device" in runner
    assert 'test "$unrelated_device" = "$stale_device"' in runner
    assert (
        'test "$(backing_file "$unrelated_down_device")" = '
        '"$unrelated_down_image"' in runner
    )
    assert "filesystem_uuid" in runner
    assert "port_blocker_pid" in runner
    assert "expected-up-failure.log" in runner
    assert 'kill -0 "$port_blocker_pid"' in runner
    assert 'test ! -e "$work_image"' in runner
    assert (
        'docker container ls --all --quiet --filter "label=com.docker.compose.project=$project"'
        in runner
    )
    assert 'docker volume inspect "$data_volume"' in runner
    assert 'docker volume inspect "$signatures_volume"' in runner
    assert 'file_sha256 "$state_directory/password.env"' in runner
    assert 'file_sha256 "$template_file"' in runner
    assert 'file_sha256 "$unrelated_image"' in runner
    assert 'file_sha256 "$unrelated_down_image"' in runner
    assert "mkfs.ext4" not in runner
    assert "mount -t ext4" not in runner
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
