"""Create disposable host-broker material and verify its authenticated readiness."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

from markweave.broker.command_runner import BoundedCommandRunner
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    policy_specification_evidence,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerClient,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    leaf_certificate_sha256,
)
from markweave.broker.podman_runtime import PodmanIsolationRuntime
from markweave.broker.process import (
    _runtime_environment,
    build_broker_server,
    load_broker_process_config,
)
from markweave.broker.protocol import ReadyRequest, ReadyResponse

WORKER_ID = UUID("73000000-0000-4000-8000-000000000001")
SERVER_ID = UUID("73000000-0000-4000-8000-000000000002")
WORKER_URI = "spiffe://markweave.test/t73/worker"
SERVER_URI = "spiffe://markweave.test/t73/broker"
POLICY = "t73-final-image-e2e"
# Existing reverse smoke/process ceilings; these are not production defaults.
RUNTIME_LIMITS = {
    "cpu_period_micros": 100_000,
    "cpu_quota_micros": 100_000,
    "memory_bytes": 268_435_456,
    "pid_limit": 16,
    "wall_time_millis": 10_000,
    "workspace_bytes": 33_554_432,
}
_MAX_DIAGNOSTIC_BYTES = 65_536
_MAX_DIAGNOSTIC_ATTEMPTS = 32
CHANNEL_LIMITS = {"max_input_bytes": 1_000_000, "max_output_bytes": 4_000_000}


def _openssl(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed OpenSSL certificate fixture arguments
        ["/usr/bin/openssl", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=15,
    )


def _certificate(root: Path, name: str, uri: str, purpose: str) -> None:
    (root / f"{name}.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
        f"extendedKeyUsage={purpose}\nsubjectAltName=URI:{uri}\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
    )
    _openssl(
        root,
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        f"/CN={name}",
        "-keyout",
        f"{name}.key",
        "-out",
        f"{name}.csr",
    )
    _openssl(
        root,
        "x509",
        "-req",
        "-days",
        "1",
        "-in",
        f"{name}.csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-CAcreateserial",
        "-extfile",
        f"{name}.ext",
        "-out",
        f"{name}.crt",
    )


def _pin(path: Path) -> str:
    return leaf_certificate_sha256(ssl.PEM_cert_to_DER_cert(path.read_text("ascii")))


def prepare(root: Path, repository: str, digest: str, worker_host: str) -> None:
    """Create one private test CA, pinned identities and fixed broker policy."""
    os.umask(0o077)
    root.mkdir(mode=0o700)
    for name in ("state", "hooks", "tls", "worker"):
        (root / name).mkdir(mode=0o700)
    tls = root / "tls"
    _openssl(
        tls,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=T73 ephemeral test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
    )
    _certificate(tls, "server", SERVER_URI, "serverAuth")
    _certificate(tls, "client", WORKER_URI, "clientAuth")
    for name in ("ca.crt", "client.crt", "client.key"):
        shutil.copyfile(tls / name, root / "worker" / name)
        (root / "worker" / name).chmod(0o600)
    (root / "inventory.key").write_text(secrets.token_hex(32) + "\n")
    with socket.socket() as reservation:
        reservation.bind(("0.0.0.0", 0))  # noqa: S104 - pinned mTLS test listener
        port = reservation.getsockname()[1]
    configuration = {
        "schema_version": 2,
        "transport_kind": "mtls",
        "channel_limits": CHANNEL_LIMITS,
        "runtime_limits": RUNTIME_LIMITS,
        "hard_shutdown_timeout_seconds": 10,
        "hooks_directory": str(root / "hooks"),
        "state_directory": str(root / "state"),
        "image_digest": digest,
        "image_repository": repository,
        "inventory_key_path": str(root / "inventory.key"),
        "max_units": 16,
        "podman": {"operation_timeout_seconds": 20, "output_bytes": 65_536},
        "policy_revision": POLICY,
        "principal_id": str(WORKER_ID),
        "transport": {
            "listen_backlog": 8,
            "max_handlers": 8,
            "max_handshakes": 8,
            "max_pending_exchanges": 8,
            "operation_timeout_seconds": 5,
            "shutdown_timeout_seconds": 5,
        },
        "mtls": {
            "ca_certificate_path": str(tls / "ca.crt"),
            "certificate_chain_path": str(tls / "server.crt"),
            "client_leaf_certificate_sha256": [_pin(tls / "client.crt")],
            "client_uri_san": WORKER_URI,
            "endpoint_host": "0.0.0.0",  # noqa: S104 - host-native pinned mTLS
            "endpoint_port": port,
            "local_principal_id": str(SERVER_ID),
            "local_uri_san": SERVER_URI,
            "private_key_path": str(tls / "server.key"),
        },
    }
    (root / "broker.json").write_text(
        json.dumps(configuration, sort_keys=True, separators=(",", ":")) + "\n"
    )
    settings = {
        "BROKER_TRANSPORT": "mtls",
        "BROKER_PRINCIPAL_ID": str(WORKER_ID),
        "BROKER_POLICY_REVISION": POLICY,
        "BROKER_IMAGE_DIGEST": digest,
        "BROKER_OPERATION_TIMEOUT_SECONDS": "5",
        "BROKER_ENDPOINT_HOST": worker_host,
        "BROKER_ENDPOINT_PORT": str(port),
        "BROKER_CA_CERTIFICATE_PATH": "/run/reverse-client/ca.crt",
        "BROKER_CERTIFICATE_CHAIN_PATH": "/run/reverse-client/client.crt",
        "BROKER_PRIVATE_KEY_PATH": "/run/reverse-client/client.key",
        "BROKER_WORKER_URI_SAN": WORKER_URI,
        "BROKER_SERVER_URI_SAN": SERVER_URI,
        "BROKER_SERVER_PRINCIPAL_ID": str(SERVER_ID),
        "BROKER_SERVER_LEAF_SHA256": json.dumps([_pin(tls / "server.crt")]),
    }
    settings.update(
        {name.upper(): str(value) for name, value in RUNTIME_LIMITS.items()}
    )
    (root / "worker.env").write_text(
        "".join(
            f"MARKWEAVE_REVERSION_{name}={value}\n" for name, value in settings.items()
        )
    )
    # Validate before the process acquires any runtime authority.
    load_broker_process_config(root / "broker.json")


def wait_ready(root: Path) -> None:
    """Probe the real authenticated broker without exposing document data."""
    configuration = json.loads((root / "broker.json").read_text())
    tls = root / "tls"
    client = MtlsBrokerClient(
        MtlsEndpoint("127.0.0.1", configuration["mtls"]["endpoint_port"]),
        local_identity=MtlsLocalIdentity(
            tls / "ca.crt",
            tls / "client.crt",
            tls / "client.key",
            WORKER_URI,
            AuthenticatedPrincipal(WORKER_ID),
        ),
        server_identity=MtlsPeerIdentity(
            SERVER_URI,
            (_pin(tls / "server.crt"),),
            AuthenticatedPrincipal(SERVER_ID),
        ),
        operation_timeout_seconds=5,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = client.request(ReadyRequest(uuid4(), 1))
            if isinstance(response, ReadyResponse) and response.ready:
                return
        except OSError, RuntimeError:
            pass
        time.sleep(0.1)
    raise RuntimeError("T73 authenticated broker readiness timed out")


def sweep(root: Path) -> None:
    """Restart reconciliation after workers stop, proving managed units removed."""
    os.chdir(root)
    server = build_broker_server(load_broker_process_config(root / "broker.json"))
    try:
        server.start()
    finally:
        server.stop()


def _bounded_json(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(_MAX_DIAGNOSTIC_BYTES + 1)
    if len(raw) > _MAX_DIAGNOSTIC_BYTES:
        raise RuntimeError("T73 diagnostic exceeds its byte limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("T73 diagnostic object is invalid")
    return value


def _require_synthetic_binding(
    state: dict, diagnostics: dict, attempt_id: UUID
) -> None:
    if (
        state.get("schema") != "t73-reverse-lifecycle-v1"
        or diagnostics.get("schema") != "t73-reverse-attempt-diagnostics-v1"
        or any(
            diagnostics.get(key) != state.get(key)
            for key in ("profile", "scenario", "job_ids")
        )
    ):
        raise RuntimeError("T73 diagnostic source binding differs")
    target = str(UUID(state["recovery_job_id"]))
    rows = diagnostics.get("attempts")
    if (
        not isinstance(rows, list)
        or len(rows) > _MAX_DIAGNOSTIC_ATTEMPTS
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise RuntimeError("T73 diagnostic attempt bound is invalid")
    matches = [row for row in rows if row.get("job_id") == target]
    if len(matches) != 1 or matches[0].get("attempt_id") != str(attempt_id):
        raise RuntimeError("T73 paused unit is not the synthetic recovery attempt")
    if matches[0].get("attempt_number") != 1:
        raise RuntimeError("T73 barrier did not capture the first attempt")


def _no_cgroup_removal(_path: Path) -> None:
    raise RuntimeError("T73 read-only inspector cannot remove cgroups")


def _resume_owned(
    command: BoundedCommandRunner, runtime: PodmanIsolationRuntime, identity: str
) -> None:
    status, _ = command(
        ("container", "exists", identity), accepted_exit_codes=frozenset({0, 1})
    )
    if status == 0:
        inspected = runtime._inspect(identity)
        if inspected.get("State", {}).get("Paused") is True:
            command(("unpause", identity))


def _await_binding(state: dict, path: Path, attempt_id: UUID, deadline: float) -> None:
    while not path.with_suffix(".ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not path.with_suffix(".ready").exists():
        raise RuntimeError("T73 diagnostic binding readiness timed out")
    _require_synthetic_binding(state, _bounded_json(path), attempt_id)


def watch_pause(
    root: Path, state_path: Path, binding_path: Path, barrier: Path
) -> None:
    """Fault-inject only a new signed, runtime-verified synthetic managed unit.

    This prearmed bounded observer is test chaos, not production orchestration.
    Execution is held until its ready marker exists. The paused immutable unit is
    bound to the exact synthetic database attempt before the harness may crash a
    worker. Every exit unpauses that same owned incarnation if it still exists.
    """
    config = load_broker_process_config(root / "broker.json")
    inventory = SQLiteBrokerInventory(
        config.state_directory / "inventory.sqlite3",
        config.authentication_key,
        max_records=config.max_units,
    )
    environment, _ = _runtime_environment()
    command = BoundedCommandRunner(
        Path("/usr/bin/podman"), config.podman_limits, environment=environment
    )
    runtime = PodmanIsolationRuntime(
        image_repository=config.image_repository,
        run_as_uid=os.geteuid(),
        command=command,
        cgroup_root=Path(
            f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/user@{os.geteuid()}.service"
        ),
        hooks_directory=config.hooks_directory,
        cgroup_remove=_no_cgroup_removal,
    )
    previous = {
        unit.unit_id for unit in inventory.unacknowledged(limit=config.max_units)
    }
    state = _bounded_json(state_path)
    ready = barrier.with_suffix(".ready")
    release = barrier.with_suffix(".release")
    ready.write_text("ready\n")
    deadline = time.monotonic() + 30
    paused_id = None

    def interrupted(_signum: int, _frame: object) -> None:
        raise InterruptedError("T73 pause observer interrupted")

    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        while time.monotonic() < deadline:
            units = inventory.unacknowledged(limit=config.max_units)
            candidates = {
                unit.unit_id: unit
                for unit in units
                if unit.unit_id not in previous
                and unit.principal.principal_id == WORKER_ID
                and unit.policy_revision == POLICY
                and unit.policy_specification
                == policy_specification_evidence(config.policy)
                and unit.runtime_incarnation is not None
            }
            if candidates:
                for observed in runtime.discover(limit=config.max_units):
                    unit = candidates.get(observed.unit_id)
                    if unit is None:
                        continue
                    if (
                        observed.attempt_id != unit.attempt_id
                        or observed.principal_id != WORKER_ID
                        or observed.incarnation != unit.runtime_incarnation
                    ):
                        raise RuntimeError("T73 inventory/runtime binding differs")
                    inspected = runtime._inspect(observed.container_id)
                    runtime._verified_unit(
                        inspected, expected=unit, policy=config.policy
                    )
                    paused_id = observed.container_id
                    command(("pause", paused_id))
                    inspected = runtime._inspect(paused_id)
                    if inspected.get("State", {}).get("Paused") is not True:
                        raise RuntimeError("T73 exact unit did not pause")
                    _await_binding(state, binding_path, unit.attempt_id, deadline)
                    if (
                        runtime._inspect(paused_id).get("State", {}).get("Paused")
                        is not True
                    ):
                        raise RuntimeError("T73 paused unit disappeared before binding")
                    barrier.write_text(
                        json.dumps(
                            {
                                "attempt_id": str(unit.attempt_id),
                                "unit_id": str(unit.unit_id),
                                "container_id": paused_id,
                            }
                        )
                        + "\n"
                    )
                    while not release.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if not release.exists():
                        raise RuntimeError("T73 pause barrier release timed out")
                    return
            time.sleep(0.02)
        raise RuntimeError("T73 exact synthetic unit pause timed out")
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        if paused_id is not None:
            _resume_owned(command, runtime, paused_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("prepare", "ready", "sweep", "watch-pause")
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image-repository")
    parser.add_argument("--image-digest")
    parser.add_argument("--worker-host")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--barrier", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        if not all((args.image_repository, args.image_digest, args.worker_host)):
            parser.error("prepare requires image repository, digest and worker host")
        prepare(args.root, args.image_repository, args.image_digest, args.worker_host)
    elif args.operation == "watch-pause":
        if args.state is None or args.binding is None or args.barrier is None:
            parser.error("watch-pause requires state, binding and barrier")
        watch_pause(args.root, args.state, args.binding, args.barrier)
    elif args.operation == "ready":
        wait_ready(args.root)
    else:
        sweep(args.root)


if __name__ == "__main__":
    main()
