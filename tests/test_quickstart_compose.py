"""Static contracts for the casual-user Compose quickstart and its real E2E runner."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
SIMPLE_OVERLAY = ROOT / "compose.simple.yaml"
PODMAN_OVERLAY = ROOT / "compose.podman.yaml"
TRUSTED_UPSTREAM_OVERLAY = ROOT / "compose.trusted-upstream.yaml"
PODMAN_TRUSTED_UPSTREAM_OVERLAY = ROOT / "compose.podman-trusted-upstream.yaml"
README = ROOT / "README.md"
RUNNER = ROOT / "scripts/e2e/run-compose.sh"
ALL_RUNNER = ROOT / "scripts/e2e/run-compose-all.sh"
SIMPLE_RUNNER = ROOT / "scripts/e2e/run-compose-simple.sh"
QUICKSTART = ROOT / "scripts/quickstart.sh"
SIMPLE_QUICKSTART = ROOT / "scripts/quickstart-simple.sh"
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
    "ghcr.io/guillaume-lombardo/md-converter:0.5.2@"
    "sha256:7d6c69ff76004bf1db6781eeec49fadac9633dbc3d8725e19060b67538fc8d8e"
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
    assert application["command"] == "serve"
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
    assert environment["MARKWEAVE_WORKER_MEMORY_BUDGET_BYTES"] == "805306368"
    assert environment["MARKWEAVE_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES"] == (
        "268435456"
    )
    assert environment["MARKWEAVE_INITIAL_ADMIN_PASSWORD"].startswith(
        "${MARKWEAVE_INITIAL_ADMIN_PASSWORD:?"
    )
    assert environment["MARKWEAVE_PUBLIC_ORIGIN"] == (
        "${MARKWEAVE_PUBLIC_ORIGIN:-http://localhost:8080}"
    )
    assert environment["MARKWEAVE_CLAMAV_HOST"] == "clamav"
    assert environment["MARKWEAVE_STORAGE_PROFILE"] == "standalone"
    assert environment["MARKWEAVE_STANDALONE_DATA_DIRECTORY"] == "/data"
    assert environment["MARKWEAVE_JOB_RESULT_RETENTION_SECONDS"] == "600"
    assert environment["MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS"] == "30"


def test_published_compose_uses_only_canonical_environment_names() -> None:
    environment = _compose()["services"]["markweave"]["environment"]

    assert len(environment) == 73
    assert all(key.startswith("MARKWEAVE_") for key in environment)


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
    assert "scripts/quickstart-simple.sh up" in readme
    assert "scripts/quickstart-simple.sh down" in readme
    assert "scripts/quickstart-simple.sh password" in readme
    assert "scripts/quickstart.sh down" in readme
    assert "scripts/quickstart.sh password" in readme
    assert "http://localhost:8080" in readme
    assert "**Templates**" in readme
    assert "**Administration**" not in readme
    assert "examples/quickstart-template.docx.base64" in readme
    assert "examples/quickstart-source.md" in readme
    assert ", ".join(EXPECTED_FONTS) in readme
    assert "exact 256 MiB ext4 loop" in readme
    assert "no physical capacity cap" in readme
    assert "Rootless Podman Compose" in readme
    assert "MARKWEAVE_SIMPLE_RUNTIME=podman" in readme
    assert "MARKWEAVE_SIMPLE_PORT=11279" in readme
    assert "http://localhost:11279" in readme
    assert "up --trust-upstream-antivirus" in readme
    assert "neither pulls nor starts the ClamAV image" in readme
    assert "prevents any direct or alternate route to Markweave" in " ".join(
        readme.split()
    )
    assert "`flock` from util-linux" in readme
    assert "Rootful Docker Compose only" in readme
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


def test_simple_overlay_resets_only_the_privileged_volume_options() -> None:
    overlay = SIMPLE_OVERLAY.read_text(encoding="utf-8")

    assert "physically unbounded named volume" in overlay
    assert overlay.endswith("volumes:\n  markweave-work:\n    driver_opts: !reset {}\n")
    assert "services:" not in overlay


def test_trusted_upstream_overlay_removes_local_scanner_dependency() -> None:
    overlay = TRUSTED_UPSTREAM_OVERLAY.read_text(encoding="utf-8")

    assert "depends_on: !reset {}" in overlay
    assert "MARKWEAVE_MALWARE_SCANNING_MODE: trusted-upstream" in overlay
    assert "MD_CONVERTER_" not in overlay
    assert "profiles:" in overlay
    assert "local-antivirus" in overlay


def test_podman_trusted_upstream_overlay_uses_rootless_user_mode_network() -> None:
    overlay = PODMAN_TRUSTED_UPSTREAM_OVERLAY.read_text(encoding="utf-8")

    assert "network_mode: slirp4netns" in overlay
    assert "networks: !reset []" in overlay
    assert "ports:" not in overlay


def test_podman_overlay_replaces_only_unsupported_clamav_tmpfs_options() -> None:
    overlay = PODMAN_OVERLAY.read_text(encoding="utf-8")

    assert "tmpfs: !override" in overlay
    assert "uid=" not in overlay
    assert "gid=" not in overlay
    assert "/run:rw,nosuid,nodev,noexec,size=8m" in overlay
    assert "/tmp:rw,nosuid,nodev,noexec,size=64m" in overlay  # noqa: S108
    assert "/var/lock:rw,nosuid,nodev,noexec,size=1m" in overlay
    assert "/var/log/clamav:rw,nosuid,nodev,noexec,size=32m,mode=0750" in overlay
    assert "chown 1000:1000 /var/log/clamav && exec /init" in overlay
    assert overlay.count("disable: true") == 2
    assert "condition: service_started" in overlay
    assert "security_opt: !override" in overlay
    assert "no-new-privileges:true" in overlay
    assert "seccomp=unconfined" not in overlay
    assert "reviewed Chrome seccomp profile" in overlay
    assert "frontend:" in overlay
    assert "internal: true" in overlay
    assert "driver_opts: !reset {}" in overlay


def test_simple_quickstart_is_unprivileged_and_removes_only_exact_scratch() -> None:
    script = SIMPLE_QUICKSTART.read_text(encoding="utf-8")

    assert "sudo" not in script
    assert "compose.simple.yaml" in script
    assert "compose.podman.yaml" in script
    assert "MARKWEAVE_WORK_DEVICE=/dev/null" in script
    assert 'readonly requested_runtime="${MARKWEAVE_SIMPLE_RUNTIME:-auto}"' in script
    assert "--trust-upstream-antivirus" in script
    assert "trusted_upstream_antivirus=true" in script
    assert 'files+=(--file "$repository/compose.trusted-upstream.yaml")' in script
    assert (
        'files+=(--file "$repository/compose.podman-trusted-upstream.yaml")' in script
    )
    assert "command -v slirp4netns >/dev/null 2>&1" in script
    assert "The ClamAV-free Podman quickstart requires slirp4netns." in script
    assert "compose rm --stop --force clamav" in script
    assert "Trusted upstream antivirus mode is active" in script
    assert "candidate=docker" in script
    assert "candidate=podman" in script
    assert "podman compose" in script
    assert "rootless Podman only" in script
    assert 'CONTAINERS_CONF="$podman_config_file"' in script
    assert "spikes/toolchain/chrome-seccomp.json" in script
    assert "command -v crun" in script
    assert "{{.Host.OCIRuntime.Path}}" in script
    assert 'runtime="%s"' in script
    assert "Podman's OCI runtime must be an executable absolute path" in script
    assert "wait_for_podman_scanner" in script
    assert "wait_for_application" in script
    assert "/usr/local/bin/clamdcheck.sh" in script
    assert "http://127.0.0.1:8080/health/ready" in script
    assert "flock --exclusive --nonblock --close --conflict-exit-code 75" in script
    assert "env MARKWEAVE_SIMPLE_STATE_LOCKED=1" in script
    assert "Another simple quickstart command is already using" in script
    assert "${XDG_STATE_HOME:-$HOME/.local/state}" in script
    assert "markweave-quickstart-simple" in script
    assert 'readonly project="${MARKWEAVE_SIMPLE_PROJECT:-markweave-simple}"' in script
    assert 'readonly port="${MARKWEAVE_SIMPLE_PORT:-8080}"' in script
    assert '[[ ! -e "$password_file" ]]' in script
    assert '[[ -f "$path" && ! -L "$path" && -O "$path" ]]' in script
    assert 'com.docker.compose.project" }}' in script
    assert 'com.docker.compose.volume" }}' in script
    assert "{{json .Options}}" in script
    assert '"${runtime_command[@]}" volume rm "$work_volume"' in script
    assert '--label "com.docker.compose.project=$project"' in script
    assert '--label "com.docker.compose.volume=markweave-work"' in script
    assert "--network none --read-only --user 0:0" in script
    assert "--cap-drop ALL --cap-add CHOWN --security-opt no-new-privileges" in script
    assert "chmod 0770 /work && chown 1001:0 /work" in script
    assert "down --remove-orphans" in script
    assert "down --volumes" not in script
    assert "no physical capacity cap" in script
    assert "Markweave is ready with $runtime_name" in script


def test_simple_quickstart_has_an_explicit_warned_insecure_mode() -> None:
    script = SIMPLE_QUICKSTART.read_text(encoding="utf-8")

    assert "--insecure" in script
    assert "insecure=true" in script
    assert "INSECURE MODE is active" in script
    assert "Never expose this mode to a network or production" in script


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


@pytest.mark.parametrize("quickstart", [QUICKSTART, SIMPLE_QUICKSTART])
def test_quickstarts_pass_the_exact_public_origin_to_compose(
    quickstart: Path,
) -> None:
    script = quickstart.read_text(encoding="utf-8")

    assert (
        'readonly public_origin="${MARKWEAVE_PUBLIC_ORIGIN:-http://localhost:$port}"'
        in script
    )
    assert "MARKWEAVE_PUBLIC_ORIGIN=%s" in script
    assert 'MARKWEAVE_PORT="$port" MARKWEAVE_PUBLIC_ORIGIN="$public_origin"' in script
    assert "verify_application_public_origin" in script
    assert "The public origin must be a single-line HTTP origin." in script


def test_simple_quickstart_probes_the_browser_origin_before_readiness() -> None:
    script = SIMPLE_QUICKSTART.read_text(encoding="utf-8")

    assert 'os.environ.get("MARKWEAVE_PUBLIC_ORIGIN")' in script
    assert 'headers={"Origin": origin}' in script
    assert 'os.environ.get("MARKWEAVE_INSECURE_EVALUATION_MODE")' in script
    assert '("null", "https://attacker.invalid")' in script
    assert "does not match the requested login-origin policy" in script


@pytest.mark.parametrize("quickstart", [QUICKSTART, SIMPLE_QUICKSTART])
def test_quickstarts_reject_multiline_public_origins(
    tmp_path: Path,
    quickstart: Path,
) -> None:
    rejected = subprocess.run(
        [str(quickstart), "up"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_PUBLIC_ORIGIN": "https://converter.example\nINJECTED=value",
            "XDG_STATE_HOME": str(tmp_path / "state"),
        },
    )

    assert rejected.returncode != 0
    assert "The public origin must be a single-line HTTP origin." in rejected.stderr


@pytest.mark.parametrize(
    ("quickstart", "directory"),
    [
        (QUICKSTART, "markweave-quickstart"),
        (SIMPLE_QUICKSTART, "markweave-quickstart-simple"),
    ],
)
def test_quickstart_password_is_create_once_and_rejects_symlinks(
    tmp_path: Path,
    quickstart: Path,
    directory: str,
) -> None:
    environment = os.environ | {"XDG_STATE_HOME": str(tmp_path / "state")}
    command = [str(quickstart), "password"]

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
    state = tmp_path / "state" / directory
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


def test_simple_quickstart_rejects_a_concurrent_state_user(tmp_path: Path) -> None:
    environment = os.environ | {"XDG_STATE_HOME": str(tmp_path / "state")}
    command = [str(SIMPLE_QUICKSTART), "password"]
    subprocess.run(command, check=True, capture_output=True, text=True, env=environment)
    state = tmp_path / "state" / "markweave-quickstart-simple"
    ready = tmp_path / "lock-ready"
    holder = subprocess.Popen(
        [
            "flock",
            "--exclusive",
            str(state),
            "sh",
            "-c",
            'printf ready >"$1"; sleep 30',
            "sh",
            str(ready),
        ]
    )
    try:
        for _ in range(100):
            if ready.exists():
                break
            assert holder.poll() is None
            time.sleep(0.01)
        assert ready.exists()
        rejected = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert rejected.returncode != 0
        assert "Another simple quickstart command is already using" in rejected.stderr
    finally:
        holder.terminate()
        holder.wait(timeout=5)


@pytest.mark.parametrize("flag", ["--trust-upstream-antivirus", "--insecure"])
def test_clamav_free_podman_rejects_missing_slirp4netns(
    tmp_path: Path,
    flag: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command_name in ("bash", "chmod", "dirname", "env", "flock", "mkdir"):
        command = shutil.which(command_name)
        assert command is not None
        (fake_bin / command_name).symlink_to(command)
    podman = fake_bin / "podman"
    podman.write_text(
        "#!/bin/sh\n"
        "if [ \"$1 $2\" = 'info --format' ]; then printf 'true\\n'; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    podman.chmod(0o755)

    rejected = subprocess.run(
        [str(SIMPLE_QUICKSTART), "up", flag],
        check=False,
        capture_output=True,
        text=True,
        env={
            "HOME": str(tmp_path),
            "MARKWEAVE_SIMPLE_RUNTIME": "podman",
            "PATH": str(fake_bin),
            "XDG_STATE_HOME": str(tmp_path / "state"),
        },
    )

    assert rejected.returncode != 0
    assert "The ClamAV-free Podman quickstart requires slirp4netns." in rejected.stderr


def test_compose_e2e_is_isolated_and_exercises_real_restart_workflow() -> None:
    runner = RUNNER.read_text(encoding="utf-8")

    assert '"XDG_STATE_HOME=$state_home"' in runner
    assert '"MARKWEAVE_QUICKSTART_PROJECT=$project"' in runner
    assert '"MARKWEAVE_QUICKSTART_PORT=$port"' in runner
    assert '"$quickstart_script"' in runner
    assert runner.count("quickstart up") == 5
    assert runner.count("quickstart down") >= 2
    assert "docker compose" in runner
    assert "MARKWEAVE_PUBLIC_ORIGIN=http://localhost:%s" in runner
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


def test_simple_compose_e2e_exercises_unprivileged_lifecycle_and_rollback() -> None:
    runner = SIMPLE_RUNNER.read_text(encoding="utf-8")

    assert "sudo" not in runner
    assert '"XDG_STATE_HOME=$state_home"' in runner
    assert '"MARKWEAVE_SIMPLE_PROJECT=$project"' in runner
    assert '"MARKWEAVE_SIMPLE_PORT=$port"' in runner
    assert '"MARKWEAVE_SIMPLE_RUNTIME=$runtime"' in runner
    assert "MARKWEAVE_PUBLIC_ORIGIN=http://localhost:%s" in runner
    assert "--login-origin" in runner
    assert "MARKWEAVE_SIMPLE_E2E_RUNTIME" in runner
    assert "runtime_command=(docker)" in runner
    assert "runtime_command=(podman)" in runner
    assert "compose.simple.yaml" in runner
    assert runner.count("quickstart up") == 5
    assert runner.count("quickstart down") >= 2
    assert "tests.e2e.service_workflow checkpoint" in runner
    assert "tests.e2e.service_workflow exercise-mermaid" in runner
    assert "verify_helper_service_stopped" in runner
    assert "podman-compose.sock" in runner
    assert "tests.e2e.service_workflow verify-checkpoint" in runner
    assert 'port "$application_id" 8080/tcp' in runner
    assert 'port "$scanner_id"' in runner
    assert 'socket.create_connection(("clamav", 3310), 5)' in runner
    assert 'socket.create_connection(("1.1.1.1", 443), 2)' in runner
    assert "HostConfig.SecurityOpt" in runner
    assert "all(int(values[name], 16) == 0" in runner
    assert '[[ "$security_options" != *unconfined* ]]' in runner
    assert "grep -Eq '^Seccomp:[[:space:]]+2$' /proc/1/status" in runner
    assert 'exec "$application_id" test -f /work/simple-rerun-marker' in runner
    assert "stopped restart" in runner
    assert "expected-up-failure.log" in runner
    assert 'kill -0 "$port_blocker_pid"' in runner
    assert 'volume inspect "$data_volume"' in runner
    assert 'volume inspect "$signatures_volume"' in runner
    assert "{{json .Options}}" in runner


def test_compose_ci_runs_secure_and_simple_real_e2e_paths() -> None:
    runner = ALL_RUNNER.read_text(encoding="utf-8")

    assert runner.endswith(
        "bash scripts/e2e/run-compose.sh\n"
        "MARKWEAVE_SIMPLE_E2E_RUNTIME=docker bash scripts/e2e/run-compose-simple.sh\n"
        "MARKWEAVE_SIMPLE_E2E_RUNTIME=podman bash scripts/e2e/run-compose-simple.sh\n"
    )


def test_standalone_final_image_rejects_spoofed_proxy_origin_headers() -> None:
    runner = FINAL_IMAGE_RUNNER.read_text(encoding="utf-8")

    assert "MARKWEAVE_PUBLIC_ORIGIN=http://127.0.0.1:8080" in runner
    assert 'login("http://127.0.0.1:8080")' in runner
    assert 'login("https://attacker.example")' in runner
    assert '"Forwarded": "host=attacker.example;proto=https"' in runner
    assert '"X-Forwarded-Host": "attacker.example"' in runner
    assert '"X-Forwarded-Proto": "https"' in runner
    assert "assert accepted_status == 200" in runner
    assert "assert hostile_status == 403" in runner
    assert '"LOGIN_ORIGIN_INVALID"' in runner
    assert "MARKWEAVE_INSECURE_EVALUATION_MODE=true" in runner
    assert "verify-disabled-login-origin" in runner
    assert '"event":"insecure_evaluation_mode_enabled"' in runner
    assert "--publish 127.0.0.1::8080" in runner
    assert 'podman rm --force "$application_name"' in runner
    assert "MARKWEAVE_SESSION_ABSOLUTE_SECONDS=2" in runner
    assert "verify-session-expiration" in runner
    assert 'podman rm --force "$expiry_application_name" "$clamav_name"' in runner
