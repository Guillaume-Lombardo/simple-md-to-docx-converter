"""Static security contracts for the host-native broker deployment assets."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from markweave.broker.mtls_transport import MtlsTransportLimits
from markweave.broker.process import load_broker_process_config
from markweave.broker.unix_transport import UnixTransportLimits

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
UNIT = ROOT / "packaging/systemd/user/markweave-broker.service"
UNIX_TEMPLATE = ROOT / "packaging/broker/broker-unix-v1.json.in"
MTLS_TEMPLATE = ROOT / "packaging/broker/broker-mtls-v2.json.in"
TOKEN = re.compile(r"@REQUIRED_[A-Z0-9_]+@")


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _material(path: Path, value: bytes = b"material\n") -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def _rendered_values(tmp_path: Path) -> dict[str, str]:
    state = _private_directory(tmp_path / "state")
    socket = _private_directory(tmp_path / "socket")
    hooks = _private_directory(tmp_path / "hooks")
    keys = _private_directory(tmp_path / "keys")
    tls = _private_directory(tmp_path / "tls")
    inventory_key = _material(keys / "inventory.key", b"12" * 32 + b"\n")
    ca = _material(tls / "ca.pem")
    chain = _material(tls / "server.pem")
    private_key = _material(tls / "server.key")
    return {
        "@REQUIRED_ABSOLUTE_EMPTY_HOOKS_DIRECTORY@": str(hooks),
        "@REQUIRED_ABSOLUTE_INVENTORY_KEY_PATH@": str(inventory_key),
        "@REQUIRED_ABSOLUTE_PRIVATE_CA_PATH@": str(ca),
        "@REQUIRED_ABSOLUTE_SERVER_CHAIN_PATH@": str(chain),
        "@REQUIRED_ABSOLUTE_SERVER_KEY_PATH@": str(private_key),
        "@REQUIRED_ABSOLUTE_STATE_DIRECTORY@": str(state),
        "@REQUIRED_ABSOLUTE_UNIX_SOCKET_PATH@": str(socket / "broker.sock"),
        "@REQUIRED_BROKER_PRINCIPAL_UUID@": "22222222-2222-4222-8222-222222222222",
        "@REQUIRED_BROKER_SPIFFE_URI@": "spiffe://markweave.test/broker",
        "@REQUIRED_CANONICAL_IPV4_ADDRESS@": "127.0.0.1",
        "@REQUIRED_CLIENT_LEAF_CERTIFICATE_SHA256@": "sha256:" + "b" * 64,
        "@REQUIRED_CLIENT_SPIFFE_URI@": "spiffe://markweave.test/worker",
        "@REQUIRED_CPU_PERIOD_MICROS@": "100000",
        "@REQUIRED_CPU_QUOTA_MICROS@": "50000",
        "@REQUIRED_HARD_SHUTDOWN_SECONDS@": "4",
        "@REQUIRED_LISTEN_BACKLOG@": "2",
        "@REQUIRED_MAX_HANDLERS@": "2",
        "@REQUIRED_MAX_HANDSHAKES@": "2",
        "@REQUIRED_MAX_INPUT_BYTES@": "101",
        "@REQUIRED_MAX_OUTPUT_BYTES@": "202",
        "@REQUIRED_MAX_PENDING_EXCHANGES@": "2",
        "@REQUIRED_MAX_UNITS@": "2",
        "@REQUIRED_MEMORY_BYTES@": "1048576",
        "@REQUIRED_PID_LIMIT@": "8",
        "@REQUIRED_PODMAN_OPERATION_SECONDS@": "2",
        "@REQUIRED_PODMAN_OUTPUT_BYTES@": "4096",
        "@REQUIRED_POLICY_REVISION@": "deployment-test",
        "@REQUIRED_REVERSE_IMAGE_REPOSITORY@": "localhost/markweave-attempt",
        "@REQUIRED_REVERSE_IMAGE_SHA256_DIGEST@": "sha256:" + "a" * 64,
        "@REQUIRED_TCP_PORT@": "9443",
        "@REQUIRED_TRANSPORT_OPERATION_SECONDS@": "1",
        "@REQUIRED_TRANSPORT_SHUTDOWN_SECONDS@": "2",
        "@REQUIRED_WALL_TIME_MILLIS@": "1000",
        "@REQUIRED_WORKER_PRINCIPAL_UUID@": "11111111-1111-4111-8111-111111111111",
        "@REQUIRED_WORKSPACE_BYTES@": "4096",
    }


def _render(template: Path, tmp_path: Path) -> Path:
    rendered = template.read_text(encoding="ascii")
    values = _rendered_values(tmp_path)
    assert set(TOKEN.findall(rendered)) <= set(values)
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    assert TOKEN.search(rendered) is None
    parsed = json.loads(rendered)
    assert rendered == json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n"
    config = _private_directory(tmp_path / "config") / "broker.json"
    config.write_text(rendered, encoding="ascii")
    config.chmod(0o600)
    return config


def test_systemd_user_unit_has_exact_bounded_process_contract() -> None:
    assert UNIT.read_text(encoding="ascii") == (
        "[Unit]\n"
        "Description=Markweave rootless Podman isolation broker\n"
        "Documentation=https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/"
        "blob/main/docs/reverse-broker-deployment.md\n"
        "\n"
        "[Service]\n"
        "Type=exec\n"
        "ExecStart=/usr/local/bin/markweave-broker "
        "%h/.config/markweave-broker/broker.json\n"
        "Restart=on-failure\n"
        "RestartPreventExitStatus=2\n"
        "KillMode=control-group\n"
        "TimeoutStopSec=infinity\n"
        "UMask=0077\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def test_systemd_user_unit_has_no_ambient_authority_or_budget() -> None:
    unit = UNIT.read_text(encoding="ascii")
    forbidden = (
        "Environment=",
        "EnvironmentFile=",
        "Password=",
        "PodmanSocket=",
        "RuntimeMaxSec=",
        "TimeoutStartSec=",
        "User=",
    )

    assert not any(value in unit for value in forbidden)
    assert "TimeoutStopSec=infinity" in unit
    assert "/run/podman/" not in unit
    assert "unix:///" not in unit


@pytest.mark.parametrize(
    ("template", "schema_version", "limits_type"),
    [
        (UNIX_TEMPLATE, 1, UnixTransportLimits),
        (MTLS_TEMPLATE, 2, MtlsTransportLimits),
    ],
)
def test_configuration_templates_render_to_the_exact_process_schema(
    tmp_path: Path,
    template: Path,
    schema_version: int,
    limits_type: type[UnixTransportLimits] | type[MtlsTransportLimits],
) -> None:
    config_path = _render(template, tmp_path)
    config = load_broker_process_config(config_path)

    assert config.transport_limits.__class__ is limits_type
    assert json.loads(config_path.read_text())["schema_version"] == schema_version


def test_configuration_templates_have_only_explicit_required_inputs() -> None:
    unix = UNIX_TEMPLATE.read_text(encoding="ascii")
    mtls = MTLS_TEMPLATE.read_text(encoding="ascii")

    assert TOKEN.search(unix)
    assert TOKEN.search(mtls)
    assert "socket_path" in unix and "mtls" not in unix
    assert '"transport_kind":"mtls"' in mtls and "socket_path" not in mtls
    assert "document" not in unix.casefold()
    assert "document" not in mtls.casefold()
