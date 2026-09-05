"""Real subprocess acceptance coverage for the host-native Podman broker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from markweave.broker.models import AuthenticatedPrincipal, ManagedUnitState
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

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[3]
PODMAN = Path("/usr/bin/podman")
PROCESS_IMAGE = "localhost/markweave-t70-process-integration:current"
DEFAULT_BASE_IMAGE = "localhost/markweave-reverse-attempt:t70-runtime-integration"
PRINCIPAL = AuthenticatedPrincipal(UUID("55555555-5555-4555-8555-555555555555"))


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


@pytest.fixture(scope="module")
def process_image() -> Iterator[tuple[str, str]]:
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    built_base = base == DEFAULT_BASE_IMAGE
    if built_base:
        subprocess.run(
            ("bash", "scripts/container/build-reverse-attempt.sh", base),
            check=True,
            cwd=ROOT,
            timeout=600,
        )
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        PROCESS_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/Containerfile"),
        "--build-arg",
        f"BASE_IMAGE={base}",
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
        if built_base:
            _podman("image", "rm", "--force", base, check=False)


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
    stdout, stderr = process.communicate(timeout=2)
    raise AssertionError((process.returncode, stdout, stderr))


def _client(socket_path: Path) -> UnixBrokerClient:
    return UnixBrokerClient(
        socket_path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=3,
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
