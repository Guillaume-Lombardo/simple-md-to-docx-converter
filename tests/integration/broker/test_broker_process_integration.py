"""Real subprocess acceptance coverage for the host-native Podman broker."""

from __future__ import annotations

import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError
from markweave.broker.models import (
    AuthenticatedPrincipal,
    ManagedUnitState,
    RuntimeChannelLimits,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerClient,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    leaf_certificate_sha256,
)
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    CreateRequest,
    CreateResponse,
    ProofRequest,
    ProofResponse,
    ReadyRequest,
    ReadyResponse,
    StatusRequest,
    StatusResponse,
    TerminateRequest,
    TerminateResponse,
)
from markweave.broker.unix_transport import UnixBrokerClient
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceFailureResponse,
    WorkspacePendingResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
    encode_workspace_request,
)
from markweave.reversions.models import ReverseContentLimits

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[3]
PODMAN = Path("/usr/bin/podman")
PROCESS_IMAGE = "localhost/markweave-t70-process-integration:current"
PROCESS_WORKSPACE_IMAGE = "localhost/markweave-t70-process-workspace:current"
PROCESS_WORKSPACE_BASE_IMAGE = "localhost/markweave-t70-process-workspace-base:current"
DEFAULT_BASE_IMAGE = "localhost/markweave-reverse-attempt:t70-runtime-integration"
BROKER_EXECUTABLE = ROOT / ".venv/bin/markweave-broker"
PRINCIPAL = AuthenticatedPrincipal(UUID("55555555-5555-4555-8555-555555555555"))
SERVER_PRINCIPAL = AuthenticatedPrincipal(UUID("66666666-6666-4666-8666-666666666666"))
CLIENT_URI = "spiffe://markweave.test/worker"
SERVER_URI = "spiffe://markweave.test/broker"
CHANNEL_LIMITS = RuntimeChannelLimits(1_000_000, 2_000_000)
CONTENT_LIMITS = ReverseContentLimits(
    1_000_000,
    2_000_000,
    100_000,
    1_000,
    1_000,
    1_000_000,
    1_000,
    32,
    16,
    500_000,
    1_000_000,
    1_000_000,
    2_000_000,
)


def _podman(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("CONTAINER_HOST", None)
    return subprocess.run(
        (str(PODMAN), *arguments),
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )


def _release_workspace_attempt(unit_id: UUID) -> None:
    released = _podman(
        "exec",
        f"markweave-reverse-{unit_id.hex}",
        "/usr/bin/touch",
        "/work/test.release",
    )
    assert released.stdout == ""


@pytest.fixture(scope="module")
def process_base_image() -> Iterator[str]:
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    built_base = base == DEFAULT_BASE_IMAGE
    if built_base:
        subprocess.run(
            ("bash", "scripts/container/build-reverse-attempt.sh", base),
            check=True,
            cwd=ROOT,
            timeout=600,
        )
    else:
        _podman("image", "exists", base)
    try:
        yield base
    finally:
        if built_base:
            _podman("image", "rm", "--force", base, check=False)


@pytest.fixture(scope="module")
def process_image(process_base_image: str) -> Iterator[tuple[str, str]]:
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        PROCESS_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/Containerfile"),
        "--build-arg",
        f"BASE_IMAGE={process_base_image}",
        str(ROOT / "tests/integration/broker/fixtures"),
    )
    inspected = json.loads(
        _podman("image", "inspect", PROCESS_IMAGE, "--format", "json").stdout
    )[0]
    digest = inspected["Digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    try:
        yield PROCESS_IMAGE.rsplit(":", 1)[0], digest
    finally:
        _podman("image", "rm", "--force", PROCESS_IMAGE, check=False)


@pytest.fixture(scope="module")
def process_workspace_image(process_base_image: str) -> Iterator[tuple[str, str]]:
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        PROCESS_WORKSPACE_BASE_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/WorkspaceContainerfile"),
        "--build-arg",
        f"BASE_IMAGE={process_base_image}",
        str(ROOT),
    )
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        PROCESS_WORKSPACE_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/DelayedWorkspaceContainerfile"),
        "--build-arg",
        f"BASE_IMAGE={PROCESS_WORKSPACE_BASE_IMAGE}",
        str(ROOT / "tests/integration/broker/fixtures"),
    )
    inspected = json.loads(
        _podman("image", "inspect", PROCESS_WORKSPACE_IMAGE, "--format", "json").stdout
    )[0]
    digest = inspected["Digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    try:
        yield PROCESS_WORKSPACE_IMAGE.rsplit(":", 1)[0], digest
    finally:
        _podman("image", "rm", "--force", PROCESS_WORKSPACE_IMAGE, check=False)
        _podman("image", "rm", "--force", PROCESS_WORKSPACE_BASE_IMAGE, check=False)


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _write_configuration(root: Path, repository: str, digest: str) -> tuple[Path, Path]:
    config_directory = _private_directory(root / "config")
    state_directory = _private_directory(root / "state")
    socket_directory = _private_directory(root / "socket")
    hooks_directory = _private_directory(root / "hooks")
    key_directory = _private_directory(root / "key")
    key = key_directory / "inventory.key"
    key.write_text("22" * 32 + "\n", encoding="ascii")
    key.chmod(0o400)
    socket_path = socket_directory / "broker.sock"
    value = {
        "channel_limits": {
            "max_input_bytes": 1_000_000,
            "max_output_bytes": 2_000_000,
        },
        "hard_shutdown_timeout_seconds": 4,
        "hooks_directory": str(hooks_directory),
        "image_digest": digest,
        "image_repository": repository,
        "inventory_key_path": str(key),
        "max_units": 8,
        "podman": {"operation_timeout_seconds": 20, "output_bytes": 65_536},
        "policy_revision": "process-integration",
        "principal_id": str(PRINCIPAL.principal_id),
        "runtime_limits": {
            "cpu_period_micros": 100_000,
            "cpu_quota_micros": 100_000,
            "memory_bytes": 268_435_456,
            "pid_limit": 16,
            "wall_time_millis": 10_000,
            "workspace_bytes": 8_388_608,
        },
        "schema_version": 1,
        "socket_path": str(socket_path),
        "state_directory": str(state_directory),
        "transport": {
            "listen_backlog": 4,
            "max_handlers": 4,
            "operation_timeout_seconds": 2,
            "shutdown_timeout_seconds": 2,
        },
    }
    config = config_directory / "broker.json"
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    config.chmod(0o600)
    return config, socket_path


def _write_process_ca(root: Path, *, name: str = "ca") -> Path:
    certificate = root / f"{name}.crt"
    subprocess.run(
        (
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={name}",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-keyout",
            str(root / f"{name}.key"),
            "-out",
            str(certificate),
        ),
        check=True,
        capture_output=True,
        timeout=10,
    )
    certificate.chmod(0o600)
    return certificate


def _issue_process_certificate(
    root: Path, *, name: str, uri: str, eku: str, ca_name: str = "ca"
) -> tuple[Path, Path]:
    certificate = root / f"{name}.crt"
    key = root / f"{name}.key"
    request = root / f"{name}.csr"
    extensions = root / f"{name}.ext"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
        f"extendedKeyUsage={eku}\n"
        f"subjectAltName=URI:{uri}\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="ascii",
    )
    subprocess.run(
        (
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            str(key),
            "-out",
            str(request),
        ),
        check=True,
        capture_output=True,
        timeout=10,
    )
    subprocess.run(
        (
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(request),
            "-CA",
            str(root / f"{ca_name}.crt"),
            "-CAkey",
            str(root / f"{ca_name}.key"),
            "-CAcreateserial",
            "-extfile",
            str(extensions),
            "-out",
            str(certificate),
        ),
        check=True,
        capture_output=True,
        timeout=10,
    )
    certificate.chmod(0o600)
    key.chmod(0o600)
    return certificate, key


def _write_mtls_configuration(
    root: Path, repository: str, digest: str
) -> tuple[Path, MtlsEndpoint, MtlsLocalIdentity, MtlsPeerIdentity]:
    config, _ = _write_configuration(root, repository, digest)
    material = _private_directory(root / "mtls")
    ca = _write_process_ca(material)
    server_certificate, server_key = _issue_process_certificate(
        material, name="server", uri=SERVER_URI, eku="serverAuth"
    )
    client_certificate, client_key = _issue_process_certificate(
        material, name="client", uri=CLIENT_URI, eku="clientAuth"
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        endpoint = MtlsEndpoint("127.0.0.1", reservation.getsockname()[1])
    value = json.loads(config.read_text(encoding="ascii"))
    value.pop("socket_path")
    value["schema_version"] = 2
    value["transport_kind"] = "mtls"
    value["transport"] = {
        "listen_backlog": 4,
        "max_handlers": 4,
        "max_handshakes": 4,
        "max_pending_exchanges": 4,
        "operation_timeout_seconds": 3,
        "shutdown_timeout_seconds": 2,
    }
    value["mtls"] = {
        "ca_certificate_path": str(ca),
        "certificate_chain_path": str(server_certificate),
        "client_leaf_certificate_sha256": [
            leaf_certificate_sha256(
                ssl.PEM_cert_to_DER_cert(client_certificate.read_text(encoding="ascii"))
            )
        ],
        "client_uri_san": CLIENT_URI,
        "endpoint_host": endpoint.host,
        "endpoint_port": endpoint.port,
        "local_principal_id": str(SERVER_PRINCIPAL.principal_id),
        "local_uri_san": SERVER_URI,
        "private_key_path": str(server_key),
    }
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    client_local = MtlsLocalIdentity(
        ca, client_certificate, client_key, CLIENT_URI, PRINCIPAL
    )
    server_peer = MtlsPeerIdentity(
        SERVER_URI,
        (
            leaf_certificate_sha256(
                ssl.PEM_cert_to_DER_cert(server_certificate.read_text(encoding="ascii"))
            ),
        ),
        SERVER_PRINCIPAL,
    )
    return config, endpoint, client_local, server_peer


def _start(config: Path, socket_path: Path) -> subprocess.Popen[bytes]:
    environment = dict(os.environ)
    environment.update(
        {
            "CONTAINER_HOST": "tcp://127.0.0.1:1/private",
            "CONTAINERS_CONF": "/private/containers.conf",
            "HOME": "/private/home",
            "PATH": "/private/bin",
        }
    )
    process = subprocess.Popen(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=environment,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if socket_path.exists():
            try:
                client = UnixBrokerClient(
                    socket_path,
                    expected_server_uid=os.geteuid(),
                    expected_principal=PRINCIPAL,
                    operation_timeout_seconds=1,
                )
                response = client.request(ReadyRequest(uuid4(), 1))
                if response == ReadyResponse(response.request_id, True):
                    return process
            except Exception:
                response = None
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate(timeout=5)
    raise AssertionError((process.returncode, stdout, stderr))


def _start_mtls(
    config: Path,
    endpoint: MtlsEndpoint,
    local_identity: MtlsLocalIdentity,
    server_identity: MtlsPeerIdentity,
) -> tuple[subprocess.Popen[bytes], MtlsBrokerClient]:
    environment = dict(os.environ)
    environment.update(
        {
            "CONTAINER_HOST": "tcp://127.0.0.1:1/private",
            "CONTAINERS_CONF": "/private/containers.conf",
            "HOME": "/private/home",
            "PATH": "/private/bin",
        }
    )
    process = subprocess.Popen(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env=environment,
    )
    client = MtlsBrokerClient(
        endpoint,
        local_identity=local_identity,
        server_identity=server_identity,
        operation_timeout_seconds=3,
        workspace_limits=CHANNEL_LIMITS,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            response = client.request(ReadyRequest(uuid4(), 1))
            if isinstance(response, ReadyResponse) and response.ready:
                return process, client
        except Exception:
            response = None
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate(timeout=5)
    raise AssertionError((process.returncode, stdout, stderr))


def _client(socket_path: Path) -> UnixBrokerClient:
    return UnixBrokerClient(
        socket_path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=3,
        workspace_limits=CHANNEL_LIMITS,
    )


def _stop(process: subprocess.Popen[bytes], requested_signal: signal.Signals) -> None:
    process.send_signal(requested_signal)
    stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 0
    assert stdout == b""
    assert stderr == b""


def _cleanup_unit(unit_id: UUID) -> None:
    _podman("rm", "--force", f"markweave-reverse-{unit_id.hex}", check=False)
    subprocess.run(
        ("/usr/bin/systemctl", "--user", "stop", f"markweavet70{unit_id.hex}.slice"),
        check=False,
        capture_output=True,
        timeout=20,
    )


def _systemd_properties(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        (
            "/usr/bin/systemctl",
            "--user",
            "show",
            unit,
            "--property=ActiveState",
            "--property=ExecMainStatus",
            "--property=KillMode",
            "--property=MainPID",
            "--property=NRestarts",
            "--property=Result",
            "--property=SubState",
            "--property=TimeoutStopUSec",
            "--property=UMask",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return dict(line.split("=", 1) for line in shown.stdout.splitlines() if "=" in line)


def _start_systemd_broker(config: Path, *, restart: bool = True) -> str:
    assert BROKER_EXECUTABLE.is_file()
    unit = f"markweave-broker-test-{uuid4().hex}.service"
    completed = subprocess.run(
        (
            "/usr/bin/systemd-run",
            "--user",
            "--quiet",
            f"--unit={unit}",
            "--property=Type=exec",
            f"--property=Restart={'on-failure' if restart else 'no'}",
            "--property=RestartPreventExitStatus=2",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=infinity",
            "--property=UMask=0077",
            "--setenv=CONTAINER_HOST=tcp://127.0.0.1:1/private",
            "--setenv=CONTAINERS_CONF=/private/containers.conf",
            str(BROKER_EXECUTABLE),
            str(config),
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return unit


def _wait_systemd_ready(
    unit: str, client: UnixBrokerClient | MtlsBrokerClient, sequence: int
) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            ready = client.request(ReadyRequest(uuid4(), sequence))
        except Exception:
            ready = None
        if isinstance(ready, ReadyResponse) and ready.ready:
            properties = _systemd_properties(unit)
            assert properties["ActiveState"] == "active"
            assert properties["KillMode"] == "control-group"
            assert properties["TimeoutStopUSec"] == "infinity"
            assert properties["UMask"] == "0077"
            return
        if _systemd_properties(unit).get("ActiveState") == "failed":
            break
        time.sleep(0.05)
    raise AssertionError(_systemd_properties(unit))


def test_systemd_user_service_readiness_rejects_property_mismatch(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock(spec=UnixBrokerClient)
    client.request.return_value = ReadyResponse(uuid4(), True)
    mocker.patch(
        f"{__name__}._systemd_properties",
        return_value={
            "ActiveState": "active",
            "KillMode": "control-group",
            "TimeoutStopUSec": "infinity",
            "UMask": "0022",
        },
    )

    with pytest.raises(AssertionError):
        _wait_systemd_ready("markweave-broker-test.service", client, 1)


def _stop_systemd_broker(unit: str) -> dict[str, str]:
    subprocess.run(
        ("/usr/bin/systemctl", "--user", "stop", unit),
        check=False,
        capture_output=True,
        timeout=10,
    )
    deadline = time.monotonic() + 10
    properties: dict[str, str] = {}
    while time.monotonic() < deadline:
        properties = _systemd_properties(unit)
        if properties.get("ActiveState") in {"failed", "inactive", None}:
            break
        time.sleep(0.05)
    subprocess.run(
        ("/usr/bin/systemctl", "--user", "reset-failed", unit),
        check=False,
        capture_output=True,
        timeout=10,
    )
    return properties


def _systemd_broker_diagnostics(unit: str) -> bytes:
    completed = subprocess.run(
        (
            "/usr/bin/journalctl",
            "--user",
            f"--unit={unit}",
            "--output=cat",
            "--no-pager",
            "--grep=^broker (configuration|runtime) failed$",
        ),
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    return completed.stdout


@pytest.mark.parametrize("failure", ["malformed", "mode", "symlink", "fifo"])
def test_real_process_rejects_insecure_mtls_key_before_state_mutation(
    tmp_path: Path, failure: str
) -> None:
    config, _, _, _ = _write_mtls_configuration(
        tmp_path, "localhost/unused-attempt", "sha256:" + "a" * 64
    )
    value = json.loads(config.read_text(encoding="ascii"))
    private_key = Path(value["mtls"]["private_key_path"])
    if failure == "malformed":
        private_key.write_text("not a private key", encoding="ascii")
    elif failure == "mode":
        private_key.chmod(0o644)
    elif failure == "symlink":
        target = private_key.parent / "replacement.key"
        target.write_bytes(private_key.read_bytes())
        target.chmod(0o600)
        private_key.unlink()
        private_key.symlink_to(target)
    else:
        private_key.unlink()
        os.mkfifo(private_key, mode=0o600)

    completed = subprocess.run(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        check=False,
        capture_output=True,
        timeout=10,
        cwd=ROOT,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"broker configuration failed\n"
    assert list((tmp_path / "state").iterdir()) == []


def test_real_process_rejects_wrong_mtls_pin_without_inventory_mutation(
    tmp_path: Path,
) -> None:
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        tmp_path, "localhost/unused-attempt", "sha256:" + "a" * 64
    )
    process, client = _start_mtls(config, endpoint, client_local, server_peer)
    inventory = tmp_path / "state" / "inventory.sqlite3"
    before = (inventory.stat().st_size, inventory.stat().st_mtime_ns)
    wrong_peer = MtlsPeerIdentity(SERVER_URI, ("sha256:" + "0" * 64,), SERVER_PRINCIPAL)
    wrong_client = MtlsBrokerClient(
        endpoint,
        local_identity=client_local,
        server_identity=wrong_peer,
        operation_timeout_seconds=2,
        workspace_limits=CHANNEL_LIMITS,
    )
    try:
        with pytest.raises(BrokerError):
            wrong_client.request(ReadyRequest(uuid4(), 2))
        assert (inventory.stat().st_size, inventory.stat().st_mtime_ns) == before
        ready = client.request(ReadyRequest(uuid4(), 3))
        assert isinstance(ready, ReadyResponse) and ready.ready
        _stop(process, signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("failure", ["ca", "uri-san", "eku"])
def test_real_process_rejects_invalid_client_certificate_without_dispatch(
    tmp_path: Path, failure: str
) -> None:
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        tmp_path, "localhost/unused-attempt", "sha256:" + "a" * 64
    )
    material = client_local.certificate_chain.parent
    ca_name = "ca"
    uri = CLIENT_URI
    eku = "clientAuth"
    if failure == "ca":
        _write_process_ca(material, name="untrusted-ca")
        ca_name = "untrusted-ca"
    elif failure == "uri-san":
        uri = "spiffe://markweave.test/other-worker"
    else:
        eku = "serverAuth"
    certificate, key = _issue_process_certificate(
        material,
        name=f"invalid-{failure}",
        uri=uri,
        eku=eku,
        ca_name=ca_name,
    )
    value = json.loads(config.read_text(encoding="ascii"))
    value["mtls"]["client_leaf_certificate_sha256"].append(
        leaf_certificate_sha256(
            ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
        )
    )
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    invalid_local = MtlsLocalIdentity(
        client_local.ca_certificate,
        certificate,
        key,
        CLIENT_URI,
        PRINCIPAL,
    )
    process, client = _start_mtls(config, endpoint, client_local, server_peer)
    inventory = tmp_path / "state" / "inventory.sqlite3"
    before = (inventory.stat().st_size, inventory.stat().st_mtime_ns)
    invalid_client = MtlsBrokerClient(
        endpoint,
        local_identity=invalid_local,
        server_identity=server_peer,
        operation_timeout_seconds=2,
        workspace_limits=CHANNEL_LIMITS,
    )
    try:
        with pytest.raises(BrokerError):
            invalid_client.request(ReadyRequest(uuid4(), 2))
        assert (inventory.stat().st_size, inventory.stat().st_mtime_ns) == before
        ready = client.request(ReadyRequest(uuid4(), 3))
        assert isinstance(ready, ReadyResponse) and ready.ready
        _stop(process, signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def _assert_unit_absent(unit_id: UUID) -> None:
    assert (
        _podman(
            "container", "exists", f"markweave-reverse-{unit_id.hex}", check=False
        ).returncode
        == 1
    )
    shown = subprocess.run(
        (
            "/usr/bin/systemctl",
            "--user",
            "show",
            f"markweavet70{unit_id.hex}.slice",
            "--property=ActiveState",
            "--property=ControlGroup",
            "--property=SubState",
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    properties = dict(line.split("=", 1) for line in shown.stdout.splitlines())
    assert properties == {
        "ActiveState": "inactive",
        "ControlGroup": "",
        "SubState": "dead",
    }
    cgroup = Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service/markweavet70{unit_id.hex}.slice"
    )
    assert not cgroup.exists()


def test_failed_handler_drain_is_bounded_by_independent_hard_watchdog() -> None:
    program = """
import time
from threading import Thread

from markweave.broker.process import BrokerProcess
from markweave.broker.unix_transport import UnixBrokerServer


class UndrainedServer(UnixBrokerServer):
    def __init__(self):
        pass

    @property
    def failed(self):
        return False

    def start(self):
        pass

    def wait_stopping(self, timeout=None):
        return True

    def stop(self):
        Thread(target=lambda: time.sleep(10), daemon=False).start()
        raise RuntimeError("private handler failure")

    def request_stop(self):
        pass


raise SystemExit(
    BrokerProcess(UndrainedServer(), hard_shutdown_timeout_seconds=0.1).run()
)
"""
    started = time.monotonic()

    completed = subprocess.run(
        (sys.executable, "-c", program),
        check=False,
        capture_output=True,
        timeout=10,
        cwd=ROOT,
    )

    assert completed.returncode == 1
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("requested_signal", [signal.SIGINT, signal.SIGTERM])
def test_real_process_serves_complete_lifecycle_and_stops_cleanly(
    tmp_path: Path,
    process_image: tuple[str, str],
    requested_signal: signal.Signals,
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_image)
    process = _start(config, socket_path)
    client = _client(socket_path)
    attempt_id = uuid4()
    unit_id: UUID | None = None
    try:
        ready = client.request(ReadyRequest(uuid4(), 1))
        assert isinstance(ready, ReadyResponse) and ready.ready
        created = client.request(CreateRequest(uuid4(), 2, attempt_id))
        assert isinstance(created, CreateResponse)
        assert created.state is ManagedUnitState.CREATED
        unit_id = created.unit_id
        status = client.request(StatusRequest(uuid4(), 3, attempt_id, unit_id))
        assert isinstance(status, StatusResponse)
        assert status.state is ManagedUnitState.CREATED
        terminated = client.request(TerminateRequest(uuid4(), 4, attempt_id, unit_id))
        assert isinstance(terminated, TerminateResponse)
        proof = client.request(ProofRequest(uuid4(), 5, attempt_id, unit_id))
        assert isinstance(proof, ProofResponse)
        assert proof.proof == terminated.proof
        acknowledged = client.request(
            AcknowledgeRequest(
                uuid4(), 6, attempt_id, unit_id, terminated.proof.proof_id
            )
        )
        assert isinstance(acknowledged, AcknowledgeResponse)
        assert acknowledged.acknowledged
        _assert_unit_absent(unit_id)
        _stop(process, requested_signal)
        assert not socket_path.exists()
        assert Path(f"{socket_path}.lock").stat().st_mode & 0o777 == 0o600
        for state_file in (tmp_path / "state").iterdir():
            assert state_file.stat().st_mode & 0o777 == 0o600
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if unit_id is not None:
            _cleanup_unit(unit_id)


def test_real_process_serves_complete_lifecycle_over_mtls(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        tmp_path, *process_image
    )
    process, client = _start_mtls(config, endpoint, client_local, server_peer)
    attempt_id = uuid4()
    unit_id: UUID | None = None
    try:
        created = client.request(CreateRequest(uuid4(), 2, attempt_id))
        assert isinstance(created, CreateResponse)
        assert created.state is ManagedUnitState.CREATED
        unit_id = created.unit_id
        status = client.request(StatusRequest(uuid4(), 3, attempt_id, unit_id))
        assert isinstance(status, StatusResponse)
        terminated = client.request(TerminateRequest(uuid4(), 4, attempt_id, unit_id))
        assert isinstance(terminated, TerminateResponse)
        proof = client.request(ProofRequest(uuid4(), 5, attempt_id, unit_id))
        assert isinstance(proof, ProofResponse)
        assert proof.proof == terminated.proof
        acknowledged = client.request(
            AcknowledgeRequest(
                uuid4(), 6, attempt_id, unit_id, terminated.proof.proof_id
            )
        )
        assert isinstance(acknowledged, AcknowledgeResponse)
        _assert_unit_absent(unit_id)
        _stop(process, signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if unit_id is not None:
            _cleanup_unit(unit_id)


def test_second_mtls_process_is_refused_before_shared_state_mutation(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    first_root = _private_directory(tmp_path / "first")
    second_root = _private_directory(tmp_path / "second")
    first_config, endpoint, client_local, server_peer = _write_mtls_configuration(
        first_root, *process_image
    )
    first, client = _start_mtls(first_config, endpoint, client_local, server_peer)
    second_config, _, _, _ = _write_mtls_configuration(second_root, *process_image)
    first_value = json.loads(first_config.read_text(encoding="ascii"))
    second_value = json.loads(second_config.read_text(encoding="ascii"))
    second_value["state_directory"] = first_value["state_directory"]
    second_config.write_text(
        json.dumps(second_value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    inventory = Path(first_value["state_directory"]) / "inventory.sqlite3"
    before = (inventory.stat().st_size, inventory.stat().st_mtime_ns)
    try:
        second = subprocess.run(
            (
                sys.executable,
                "-m",
                "markweave.broker.process",
                str(second_config),
            ),
            check=False,
            capture_output=True,
            timeout=10,
            cwd=ROOT,
        )

        assert second.returncode == 1
        assert second.stdout == b""
        assert second.stderr == b"broker runtime failed\n"
        assert (inventory.stat().st_size, inventory.stat().st_mtime_ns) == before
        ready = client.request(ReadyRequest(uuid4(), 2))
        assert isinstance(ready, ReadyResponse) and ready.ready
        _stop(first, signal.SIGTERM)
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=3)


def test_real_process_workspace_pending_success_and_failure_over_mtls(  # noqa: PLR0915
    tmp_path: Path, process_workspace_image: tuple[str, str]
) -> None:
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        tmp_path, *process_workspace_image
    )
    value = json.loads(config.read_text(encoding="ascii"))
    value["runtime_limits"]["wall_time_millis"] = 30_000
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    process, client = _start_mtls(config, endpoint, client_local, server_peer)
    units: list[tuple[UUID, UUID]] = []
    try:
        successful_attempt = uuid4()
        created = client.request(CreateRequest(uuid4(), 2, successful_attempt))
        assert isinstance(created, CreateResponse)
        units.append((successful_attempt, created.unit_id))
        stage = WorkspaceStageRequest(
            uuid4(),
            3,
            successful_attempt,
            created.unit_id,
            2,
            ".pdf",
            CONTENT_LIMITS,
            (ROOT / "spikes/anydoc/corpus/pdf/text.pdf").read_bytes(),
        )
        receipt = client.stage_workspace(stage)
        assert isinstance(receipt, WorkspaceStageReceipt)
        pending = client.collect_workspace(
            WorkspaceCollectRequest(
                uuid4(),
                4,
                receipt.request_id,
                receipt.stage_sequence,
                receipt.attempt_id,
                receipt.unit_id,
                receipt.create_sequence,
                receipt.incarnation_id,
            )
        )
        assert isinstance(pending, WorkspacePendingResponse)
        _release_workspace_attempt(created.unit_id)
        response = pending
        sequence = 5
        deadline = time.monotonic() + 25
        while (
            isinstance(response, WorkspacePendingResponse)
            and time.monotonic() < deadline
        ):
            response = client.collect_workspace(
                WorkspaceCollectRequest(
                    uuid4(),
                    sequence,
                    receipt.request_id,
                    receipt.stage_sequence,
                    receipt.attempt_id,
                    receipt.unit_id,
                    receipt.create_sequence,
                    receipt.incarnation_id,
                )
            )
            sequence += 1
            if isinstance(response, WorkspacePendingResponse):
                time.sleep(0.05)
        assert isinstance(response, WorkspaceSuccessResponse)
        assert response.result

        failed_attempt = uuid4()
        failed = client.request(CreateRequest(uuid4(), 20, failed_attempt))
        assert isinstance(failed, CreateResponse)
        units.append((failed_attempt, failed.unit_id))
        failed_receipt = client.stage_workspace(
            WorkspaceStageRequest(
                uuid4(),
                21,
                failed_attempt,
                failed.unit_id,
                20,
                ".pdf",
                CONTENT_LIMITS,
                b"not a PDF",
            )
        )
        assert isinstance(failed_receipt, WorkspaceStageReceipt)
        _release_workspace_attempt(failed.unit_id)
        failed_response: object = WorkspacePendingResponse(uuid4(), failed_receipt)
        deadline = time.monotonic() + 25
        while (
            isinstance(failed_response, WorkspacePendingResponse)
            and time.monotonic() < deadline
        ):
            failed_response = client.collect_workspace(
                WorkspaceCollectRequest(
                    uuid4(),
                    22,
                    failed_receipt.request_id,
                    failed_receipt.stage_sequence,
                    failed_receipt.attempt_id,
                    failed_receipt.unit_id,
                    failed_receipt.create_sequence,
                    failed_receipt.incarnation_id,
                )
            )
            if isinstance(failed_response, WorkspacePendingResponse):
                time.sleep(0.05)
        assert isinstance(failed_response, WorkspaceFailureResponse)

        for sequence, (attempt_id, unit_id) in enumerate(units, start=30):
            terminated = client.request(
                TerminateRequest(uuid4(), sequence, attempt_id, unit_id)
            )
            assert isinstance(terminated, TerminateResponse)
            acknowledged = client.request(
                AcknowledgeRequest(
                    uuid4(),
                    sequence + 10,
                    attempt_id,
                    unit_id,
                    terminated.proof.proof_id,
                )
            )
            assert isinstance(acknowledged, AcknowledgeResponse)
            _assert_unit_absent(unit_id)
        _stop(process, signal.SIGINT)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for _, unit_id in units:
            _cleanup_unit(unit_id)


def test_real_process_workspace_lost_receipt_retry_pending_success_and_failure(  # noqa: PLR0915
    tmp_path: Path, process_workspace_image: tuple[str, str]
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_workspace_image)
    value = json.loads(config.read_text(encoding="ascii"))
    value["runtime_limits"]["wall_time_millis"] = 30_000
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    process = _start(config, socket_path)
    client = _client(socket_path)
    units: list[tuple[UUID, UUID]] = []
    try:
        successful_attempt = uuid4()
        created = client.request(CreateRequest(uuid4(), 2, successful_attempt))
        assert isinstance(created, CreateResponse)
        units.append((successful_attempt, created.unit_id))
        source = (ROOT / "spikes/anydoc/corpus/pdf/text.pdf").read_bytes()
        stage = WorkspaceStageRequest(
            uuid4(),
            3,
            successful_attempt,
            created.unit_id,
            2,
            ".pdf",
            CONTENT_LIMITS,
            source,
        )

        # Simulate a lost STAGE acknowledgement after the complete request reached the broker.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(socket_path))
            connection.sendall(encode_workspace_request(stage))
            connection.shutdown(socket.SHUT_WR)
        receipt = client.stage_workspace(stage)
        assert isinstance(receipt, WorkspaceStageReceipt)

        collect_sequence = 4
        pending_seen = False
        deadline = time.monotonic() + 25
        response = None
        while time.monotonic() < deadline:
            response = client.collect_workspace(
                WorkspaceCollectRequest(
                    uuid4(),
                    collect_sequence,
                    receipt.request_id,
                    receipt.stage_sequence,
                    receipt.attempt_id,
                    receipt.unit_id,
                    receipt.create_sequence,
                    receipt.incarnation_id,
                )
            )
            collect_sequence += 1
            if isinstance(response, WorkspacePendingResponse):
                if not pending_seen:
                    _release_workspace_attempt(created.unit_id)
                pending_seen = True
                time.sleep(0.05)
                continue
            break
        assert pending_seen
        assert isinstance(response, WorkspaceSuccessResponse)
        assert response.result
        assert source[:32] not in repr(response).encode()

        failed_attempt = uuid4()
        failed = client.request(CreateRequest(uuid4(), 20, failed_attempt))
        assert isinstance(failed, CreateResponse)
        units.append((failed_attempt, failed.unit_id))
        failed_stage = WorkspaceStageRequest(
            uuid4(),
            21,
            failed_attempt,
            failed.unit_id,
            20,
            ".pdf",
            CONTENT_LIMITS,
            b"not a PDF",
        )
        failed_receipt = client.stage_workspace(failed_stage)
        assert isinstance(failed_receipt, WorkspaceStageReceipt)
        deadline = time.monotonic() + 25
        failed_response = None
        failed_pending_seen = False
        while time.monotonic() < deadline:
            failed_response = client.collect_workspace(
                WorkspaceCollectRequest(
                    uuid4(),
                    22,
                    failed_receipt.request_id,
                    failed_receipt.stage_sequence,
                    failed_receipt.attempt_id,
                    failed_receipt.unit_id,
                    failed_receipt.create_sequence,
                    failed_receipt.incarnation_id,
                )
            )
            if isinstance(failed_response, WorkspacePendingResponse):
                if not failed_pending_seen:
                    _release_workspace_attempt(failed.unit_id)
                failed_pending_seen = True
                time.sleep(0.05)
                continue
            break
        assert failed_pending_seen
        assert isinstance(failed_response, WorkspaceFailureResponse)

        for index, (attempt_id, unit_id) in enumerate(units, start=30):
            terminated = client.request(
                TerminateRequest(uuid4(), index, attempt_id, unit_id)
            )
            assert isinstance(terminated, TerminateResponse)
            acknowledged = client.request(
                AcknowledgeRequest(
                    uuid4(),
                    index + 10,
                    attempt_id,
                    unit_id,
                    terminated.proof.proof_id,
                )
            )
            assert isinstance(acknowledged, AcknowledgeResponse)
            _assert_unit_absent(unit_id)
        _stop(process, signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for _, unit_id in units:
            _cleanup_unit(unit_id)


def test_second_process_is_refused_by_lifecycle_lock(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_image)
    first = _start(config, socket_path)
    program = """
import os
import sys
from pathlib import Path
from uuid import UUID

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.unix_transport import UnixBrokerServer, UnixTransportLimits

server = UnixBrokerServer(
    Path(sys.argv[1]),
    expected_client_uid=os.geteuid(),
    principal=AuthenticatedPrincipal(UUID(sys.argv[2])),
    dispatcher=object.__new__(BrokerDispatcher),
    limits=UnixTransportLimits(1, 1, 1, 1),
)
try:
    server.start()
except RuntimeError as error:
    if str(error) != "Broker Unix server is already active":
        os.write(2, b"unexpected runtime failure\\n")
        raise SystemExit(2)
    os.write(2, b"broker runtime failed\\n")
    raise SystemExit(1)
except BaseException:
    os.write(2, b"unexpected broker failure\\n")
    raise SystemExit(3)
server.stop()
os.write(2, b"unexpected broker start\\n")
raise SystemExit(4)
"""
    second = subprocess.run(
        (sys.executable, "-c", program, str(socket_path), str(PRINCIPAL.principal_id)),
        check=False,
        capture_output=True,
        timeout=10,
    )
    try:
        assert second.returncode == 1
        assert second.stdout == b""
        assert second.stderr == b"broker runtime failed\n"
        ready = _client(socket_path).request(ReadyRequest(uuid4(), 7))
        assert isinstance(ready, ReadyResponse) and ready.ready
    finally:
        _stop(first, signal.SIGTERM)


def test_uid_authority_lock_excludes_distinct_state_and_socket_process(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_image)
    second_root = _private_directory(tmp_path / "second")
    second_config, second_socket = _write_configuration(second_root, *process_image)
    first_value = json.loads(config.read_text(encoding="ascii"))
    second_value = json.loads(second_config.read_text(encoding="ascii"))
    assert first_value["state_directory"] != second_value["state_directory"]
    first = _start(config, socket_path)
    first_state = Path(first_value["state_directory"])
    second_state = Path(second_value["state_directory"])
    before = {
        path.name: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
        for path in first_state.iterdir()
    }
    assert list(second_state.iterdir()) == []
    authority_directory = Path(f"/run/user/{os.geteuid()}/markweave-broker")
    assert authority_directory.resolve(strict=True) == authority_directory
    assert authority_directory.stat().st_uid == os.geteuid()
    assert authority_directory.stat().st_mode & 0o777 == 0o700
    assert (
        authority_directory / "broker-authority.lock"
    ).stat().st_mode & 0o777 == 0o600

    second = subprocess.run(
        (sys.executable, "-m", "markweave.broker.process", str(second_config)),
        check=False,
        capture_output=True,
        timeout=10,
    )
    after = {
        path.name: (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
        for path in first_state.iterdir()
    }
    try:
        assert second.returncode == 1
        assert second.stdout == b""
        assert second.stderr == b"broker runtime failed\n"
        assert not second_socket.exists()
        assert after == before
        assert list(second_state.iterdir()) == []
        ready = _client(socket_path).request(ReadyRequest(uuid4(), 7))
        assert isinstance(ready, ReadyResponse) and ready.ready
    finally:
        _stop(first, signal.SIGTERM)


@pytest.mark.parametrize("fifo", ["config", "key"])
def test_fifo_configuration_inputs_are_rejected_without_blocking(
    tmp_path: Path, fifo: str
) -> None:
    config, _ = _write_configuration(
        tmp_path,
        "localhost/markweave-attempt",
        "sha256:" + "a" * 64,
    )
    value = json.loads(config.read_text(encoding="ascii"))
    target = config if fifo == "config" else Path(value["inventory_key_path"])
    target.unlink()
    os.mkfifo(target, mode=0o600)

    completed = subprocess.run(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        check=False,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"broker configuration failed\n"


def test_nonfinite_json_float_entrypoint_failure_is_bounded_and_content_free(
    tmp_path: Path,
) -> None:
    config, _ = _write_configuration(
        tmp_path,
        "localhost/markweave-attempt",
        "sha256:" + "a" * 64,
    )
    encoded = config.read_text(encoding="ascii")
    config.write_text(
        encoded.replace(
            '"hard_shutdown_timeout_seconds":4', '"hard_shutdown_timeout_seconds":1e400'
        ),
        encoding="ascii",
    )

    completed = subprocess.run(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        check=False,
        capture_output=True,
        timeout=10,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr == b"broker configuration failed\n"


def test_sigkill_live_orphan_is_reconciled_before_restarted_listener(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_image)
    first = _start(config, socket_path)
    client = _client(socket_path)
    attempt_id = uuid4()
    created = client.request(CreateRequest(uuid4(), 2, attempt_id))
    assert isinstance(created, CreateResponse)
    try:
        first.kill()
        stdout, stderr = first.communicate(timeout=3)
        assert first.returncode == -signal.SIGKILL
        assert stdout == b""
        assert stderr == b""
        assert (
            _podman(
                "container",
                "exists",
                f"markweave-reverse-{created.unit_id.hex}",
                check=False,
            ).returncode
            == 0
        )

        restarted = _start(config, socket_path)
        try:
            status = _client(socket_path).request(
                StatusRequest(uuid4(), 3, attempt_id, created.unit_id)
            )
            assert isinstance(status, StatusResponse)
            assert status.state is ManagedUnitState.REMOVED
            _assert_unit_absent(created.unit_id)
        finally:
            _stop(restarted, signal.SIGTERM)
    finally:
        _cleanup_unit(created.unit_id)


def test_mtls_sigkill_live_orphan_is_reconciled_before_restarted_listener(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        tmp_path, *process_image
    )
    first, client = _start_mtls(config, endpoint, client_local, server_peer)
    attempt_id = uuid4()
    created = client.request(CreateRequest(uuid4(), 2, attempt_id))
    assert isinstance(created, CreateResponse)
    try:
        first.kill()
        stdout, stderr = first.communicate(timeout=3)
        assert first.returncode == -signal.SIGKILL
        assert stdout == b""
        assert stderr == b""
        assert (
            _podman(
                "container",
                "exists",
                f"markweave-reverse-{created.unit_id.hex}",
                check=False,
            ).returncode
            == 0
        )

        restarted, restarted_client = _start_mtls(
            config, endpoint, client_local, server_peer
        )
        try:
            status = restarted_client.request(
                StatusRequest(uuid4(), 3, attempt_id, created.unit_id)
            )
            assert isinstance(status, StatusResponse)
            assert status.state is ManagedUnitState.REMOVED
            _assert_unit_absent(created.unit_id)
        finally:
            _stop(restarted, signal.SIGTERM)
    finally:
        _cleanup_unit(created.unit_id)


@pytest.mark.parametrize("invalid", ["key-mode", "config-mode", "malformed"])
def test_real_process_rejects_bad_config_and_key_modes_content_free(
    tmp_path: Path, process_image: tuple[str, str], invalid: str
) -> None:
    config, _ = _write_configuration(tmp_path, *process_image)
    value = json.loads(config.read_text(encoding="ascii"))
    if invalid == "key-mode":
        Path(value["inventory_key_path"]).chmod(0o644)
    elif invalid == "config-mode":
        config.chmod(0o644)
    else:
        config.write_text('{"schema_version":1}\n', encoding="ascii")

    rejected = subprocess.run(
        (sys.executable, "-m", "markweave.broker.process", str(config)),
        check=False,
        capture_output=True,
        timeout=5,
    )

    assert rejected.returncode == 2
    assert rejected.stdout == b""
    assert rejected.stderr == b"broker configuration failed\n"
    assert str(config).encode() not in rejected.stderr


def _systemd_transport(
    root: Path, image: tuple[str, str], transport_kind: str
) -> tuple[Path, UnixBrokerClient | MtlsBrokerClient, Path | None]:
    if transport_kind == "unix":
        config, socket_path = _write_configuration(root, *image)
        return config, _client(socket_path), socket_path
    config, endpoint, client_local, server_peer = _write_mtls_configuration(
        root, *image
    )
    value = json.loads(config.read_text(encoding="ascii"))
    value["transport"]["operation_timeout_seconds"] = 10
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    return (
        config,
        MtlsBrokerClient(
            endpoint,
            local_identity=client_local,
            server_identity=server_peer,
            operation_timeout_seconds=10,
            workspace_limits=CHANNEL_LIMITS,
        ),
        None,
    )


def _collect_systemd_workspace(
    client: UnixBrokerClient | MtlsBrokerClient,
    receipt: WorkspaceStageReceipt,
    *,
    sequence: int,
) -> WorkspaceSuccessResponse | WorkspaceFailureResponse:
    deadline = time.monotonic() + 25
    response: object = WorkspacePendingResponse(uuid4(), receipt)
    while (
        isinstance(response, WorkspacePendingResponse) and time.monotonic() < deadline
    ):
        response = client.collect_workspace(
            WorkspaceCollectRequest(
                uuid4(),
                sequence,
                receipt.request_id,
                receipt.stage_sequence,
                receipt.attempt_id,
                receipt.unit_id,
                receipt.create_sequence,
                receipt.incarnation_id,
            )
        )
        sequence += 1
        if isinstance(response, WorkspacePendingResponse):
            time.sleep(0.05)
    assert isinstance(response, (WorkspaceSuccessResponse, WorkspaceFailureResponse))
    return response


def _terminate_acknowledge_systemd_unit(
    client: UnixBrokerClient | MtlsBrokerClient,
    *,
    attempt_id: UUID,
    unit_id: UUID,
    sequence: int,
) -> None:
    terminated = client.request(
        TerminateRequest(uuid4(), sequence, attempt_id, unit_id)
    )
    assert isinstance(terminated, TerminateResponse)
    proof = client.request(ProofRequest(uuid4(), sequence + 1, attempt_id, unit_id))
    assert isinstance(proof, ProofResponse)
    assert proof.proof == terminated.proof
    acknowledged = client.request(
        AcknowledgeRequest(
            uuid4(),
            sequence + 2,
            attempt_id,
            unit_id,
            terminated.proof.proof_id,
        )
    )
    assert isinstance(acknowledged, AcknowledgeResponse)
    assert acknowledged.acknowledged
    _assert_unit_absent(unit_id)


@pytest.mark.parametrize("transport_kind", ["unix", "mtls"])
def test_systemd_user_service_runs_workspace_success_and_failure(
    tmp_path: Path,
    process_workspace_image: tuple[str, str],
    transport_kind: str,
) -> None:
    config, client, socket_path = _systemd_transport(
        tmp_path, process_workspace_image, transport_kind
    )
    value = json.loads(config.read_text(encoding="ascii"))
    value["runtime_limits"]["wall_time_millis"] = 30_000
    config.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    unit = _start_systemd_broker(config)
    units: list[tuple[UUID, UUID]] = []
    try:
        _wait_systemd_ready(unit, client, 1)
        successful_attempt = uuid4()
        created = client.request(CreateRequest(uuid4(), 2, successful_attempt))
        assert isinstance(created, CreateResponse)
        units.append((successful_attempt, created.unit_id))
        receipt = client.stage_workspace(
            WorkspaceStageRequest(
                uuid4(),
                3,
                successful_attempt,
                created.unit_id,
                2,
                ".pdf",
                CONTENT_LIMITS,
                (ROOT / "spikes/anydoc/corpus/pdf/text.pdf").read_bytes(),
            )
        )
        assert isinstance(receipt, WorkspaceStageReceipt)
        _release_workspace_attempt(created.unit_id)
        success = _collect_systemd_workspace(client, receipt, sequence=4)
        assert isinstance(success, WorkspaceSuccessResponse)
        assert success.result
        _terminate_acknowledge_systemd_unit(
            client,
            attempt_id=successful_attempt,
            unit_id=created.unit_id,
            sequence=10,
        )

        failed_attempt = uuid4()
        failed = client.request(CreateRequest(uuid4(), 20, failed_attempt))
        assert isinstance(failed, CreateResponse)
        units.append((failed_attempt, failed.unit_id))
        failed_receipt = client.stage_workspace(
            WorkspaceStageRequest(
                uuid4(),
                21,
                failed_attempt,
                failed.unit_id,
                20,
                ".pdf",
                CONTENT_LIMITS,
                b"not a PDF",
            )
        )
        assert isinstance(failed_receipt, WorkspaceStageReceipt)
        _release_workspace_attempt(failed.unit_id)
        failure = _collect_systemd_workspace(client, failed_receipt, sequence=22)
        assert isinstance(failure, WorkspaceFailureResponse)
        _terminate_acknowledge_systemd_unit(
            client,
            attempt_id=failed_attempt,
            unit_id=failed.unit_id,
            sequence=30,
        )
        properties = _stop_systemd_broker(unit)
        assert properties["ActiveState"] == "inactive"
        assert properties["ExecMainStatus"] == "0"
        assert properties["Result"] == "success"
        if socket_path is not None:
            assert not socket_path.exists()
    finally:
        _stop_systemd_broker(unit)
        for _, unit_id in units:
            _cleanup_unit(unit_id)


def test_systemd_user_service_restarts_and_sweeps_live_orphan_before_ready(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, socket_path = _write_configuration(tmp_path, *process_image)
    client = _client(socket_path)
    unit = _start_systemd_broker(config)
    attempt_id = uuid4()
    unit_id: UUID | None = None
    try:
        _wait_systemd_ready(unit, client, 1)
        initial_pid = _systemd_properties(unit)["MainPID"]
        created = client.request(CreateRequest(uuid4(), 2, attempt_id))
        assert isinstance(created, CreateResponse)
        unit_id = created.unit_id
        assert (
            _podman(
                "container", "exists", f"markweave-reverse-{unit_id.hex}", check=False
            ).returncode
            == 0
        )

        killed = subprocess.run(
            (
                "/usr/bin/systemctl",
                "--user",
                "kill",
                "--kill-whom=main",
                "--signal=SIGKILL",
                unit,
            ),
            check=False,
            capture_output=True,
            timeout=10,
        )
        assert killed.returncode == 0
        _wait_systemd_ready(unit, client, 3)
        properties = _systemd_properties(unit)
        assert int(properties["NRestarts"]) >= 1
        assert properties["MainPID"] != initial_pid
        status = client.request(StatusRequest(uuid4(), 4, attempt_id, unit_id))
        assert isinstance(status, StatusResponse)
        assert status.state is ManagedUnitState.REMOVED
        _assert_unit_absent(unit_id)
        proof = client.request(ProofRequest(uuid4(), 5, attempt_id, unit_id))
        assert isinstance(proof, ProofResponse)
        acknowledged = client.request(
            AcknowledgeRequest(uuid4(), 6, attempt_id, unit_id, proof.proof.proof_id)
        )
        assert isinstance(acknowledged, AcknowledgeResponse)
        assert acknowledged.acknowledged
        properties = _stop_systemd_broker(unit)
        assert properties["Result"] == "success"
    finally:
        _stop_systemd_broker(unit)
        if unit_id is not None:
            _cleanup_unit(unit_id)


def test_systemd_user_service_does_not_restart_invalid_configuration(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    config, _ = _write_configuration(tmp_path, *process_image)
    value = json.loads(config.read_text(encoding="ascii"))
    Path(value["inventory_key_path"]).chmod(0o644)
    unit = _start_systemd_broker(config)
    try:
        deadline = time.monotonic() + 10
        properties: dict[str, str] = {}
        while time.monotonic() < deadline:
            properties = _systemd_properties(unit)
            if properties.get("ActiveState") == "failed":
                break
            time.sleep(0.05)
        assert properties["ActiveState"] == "failed"
        assert properties["ExecMainStatus"] == "2"
        assert properties["NRestarts"] == "0"
        assert properties["Result"] == "exit-code"
        assert _systemd_broker_diagnostics(unit) == b"broker configuration failed\n"
        assert list((tmp_path / "state").iterdir()) == []
    finally:
        _stop_systemd_broker(unit)


def test_second_systemd_user_service_is_refused_by_per_uid_authority(
    tmp_path: Path, process_image: tuple[str, str]
) -> None:
    first_root = _private_directory(tmp_path / "first")
    second_root = _private_directory(tmp_path / "second")
    first_config, first_socket = _write_configuration(first_root, *process_image)
    second_config, _ = _write_configuration(second_root, *process_image)
    first_client = _client(first_socket)
    first_unit = _start_systemd_broker(first_config)
    second_unit: str | None = None
    try:
        _wait_systemd_ready(first_unit, first_client, 1)
        second_unit = _start_systemd_broker(second_config, restart=False)
        deadline = time.monotonic() + 10
        properties: dict[str, str] = {}
        while time.monotonic() < deadline:
            properties = _systemd_properties(second_unit)
            if properties.get("ActiveState") == "failed":
                break
            time.sleep(0.05)
        assert properties["ActiveState"] == "failed"
        assert properties["ExecMainStatus"] == "1"
        assert properties["NRestarts"] == "0"
        assert _systemd_broker_diagnostics(second_unit) == b"broker runtime failed\n"
        assert list((second_root / "state").iterdir()) == []
        ready = first_client.request(ReadyRequest(uuid4(), 2))
        assert isinstance(ready, ReadyResponse) and ready.ready
        first_properties = _stop_systemd_broker(first_unit)
        assert first_properties["Result"] == "success"
    finally:
        if second_unit is not None:
            _stop_systemd_broker(second_unit)
        _stop_systemd_broker(first_unit)
