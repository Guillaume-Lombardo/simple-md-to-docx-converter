"""Production process assembly for the trusted Kubernetes node attester."""

from __future__ import annotations

import json
import math
import os
import signal
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Never, cast

from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_attester_inventory import SQLiteNodeAttesterLedger
from markweave.broker.kubernetes_attester_transport import (
    AttesterHttpsServer,
    AttesterServerTlsConfig,
    AttesterTransportLimits,
    NodeAttesterService,
)
from markweave.broker.kubernetes_inspector import (
    BoundedCriCgroupInspector,
    InspectorConfig,
    InspectorLimits,
    KubernetesReadApi,
    build_cri_command,
)
from markweave.broker.models import EvidenceDigest

_CONFIG_MAX_BYTES = 64 * 1024
_KEY_BYTES = 32
_FILE_MODE = 0o400
_STATE_DIRECTORY_MODE = 0o700
_EXPECTED_KEYS = frozenset(
    {
        "cgroup_root",
        "cni_config",
        "cni_config_sha256",
        "cni_plugin",
        "cni_plugin_sha256",
        "client_ca_file",
        "cri_endpoint",
        "cri_executable",
        "expected_client_certificate_sha256",
        "hard_shutdown_timeout_seconds",
        "inventory_authentication_key_file",
        "inventory_max_records",
        "inventory_path",
        "kubelet_config",
        "listen_host",
        "listen_port",
        "max_cgroup_directories",
        "max_concurrent_requests",
        "max_containers",
        "max_descendant_pids",
        "max_request_bytes",
        "max_response_bytes",
        "max_sandboxes",
        "operation_timeout_seconds",
        "proc_root",
        "request_timeout_seconds",
        "runtime_config",
        "runtime_config_sha256",
        "runtime_wrapper",
        "runtime_wrapper_sha256",
        "server_certificate_file",
        "server_private_key_file",
    }
)


class AttesterProcessConfigurationError(RuntimeError):
    """Content-free invalid process or host configuration."""


@dataclass(frozen=True, slots=True)
class AttesterProcessConfig:
    """Fully validated process inputs assembled from an immutable file."""

    node_name: str
    inspector: InspectorConfig
    inspector_limits: InspectorLimits
    cri_executable: Path
    tls: AttesterServerTlsConfig
    transport_limits: AttesterTransportLimits
    inventory_path: Path
    inventory_authentication_key_file: Path
    inventory_max_records: int
    hard_shutdown_timeout_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.node_name) is not str
            or not self.node_name
            or type(self.inspector) is not InspectorConfig
            or type(self.inspector_limits) is not InspectorLimits
            or not isinstance(self.cri_executable, Path)
            or not self.cri_executable.is_absolute()
            or type(self.tls) is not AttesterServerTlsConfig
            or type(self.transport_limits) is not AttesterTransportLimits
            or not isinstance(self.inventory_path, Path)
            or not self.inventory_path.is_absolute()
            or not isinstance(self.inventory_authentication_key_file, Path)
            or not self.inventory_authentication_key_file.is_absolute()
            or type(self.inventory_max_records) is not int
            or self.inventory_max_records <= 0
            or type(self.hard_shutdown_timeout_seconds) not in {int, float}
            or not math.isfinite(self.hard_shutdown_timeout_seconds)
            or self.hard_shutdown_timeout_seconds <= 0
        ):
            raise ValueError("Kubernetes attester process configuration is invalid")


def load_config(path: Path, *, node_name: str) -> AttesterProcessConfig:
    """Decode one closed immutable configuration document."""

    try:
        raw = path.read_bytes()
        if not 0 < len(raw) <= _CONFIG_MAX_BYTES:
            raise ValueError
        value = json.loads(raw.decode("ascii"))
        if not isinstance(value, Mapping) or set(value) != _EXPECTED_KEYS:
            raise ValueError
        settings = cast(Mapping[str, object], value)
        inspector_limits = InspectorLimits(
            _number(settings, "operation_timeout_seconds"),
            _integer(settings, "max_response_bytes"),
            _integer(settings, "max_sandboxes"),
            _integer(settings, "max_containers"),
            _integer(settings, "max_cgroup_directories"),
            _integer(settings, "max_descendant_pids"),
        )
        return AttesterProcessConfig(
            node_name,
            InspectorConfig(
                node_name,
                _text(settings, "cri_endpoint"),
                _path(settings, "kubelet_config"),
                _path(settings, "cni_config"),
                _text(settings, "cni_config_sha256"),
                _path(settings, "cni_plugin"),
                _text(settings, "cni_plugin_sha256"),
                _path(settings, "runtime_config"),
                _text(settings, "runtime_config_sha256"),
                _path(settings, "runtime_wrapper"),
                _text(settings, "runtime_wrapper_sha256"),
                _path(settings, "cgroup_root"),
                _path(settings, "proc_root"),
            ),
            inspector_limits,
            _path(settings, "cri_executable"),
            AttesterServerTlsConfig(
                _text(settings, "listen_host"),
                _integer(settings, "listen_port", allow_zero=True),
                _path(settings, "server_certificate_file"),
                _path(settings, "server_private_key_file"),
                _path(settings, "client_ca_file"),
                EvidenceDigest(_text(settings, "expected_client_certificate_sha256")),
            ),
            AttesterTransportLimits(
                _integer(settings, "max_request_bytes"),
                _integer(settings, "max_response_bytes"),
                _number(settings, "request_timeout_seconds"),
                _integer(settings, "max_concurrent_requests"),
            ),
            _path(settings, "inventory_path"),
            _path(settings, "inventory_authentication_key_file"),
            _integer(settings, "inventory_max_records"),
            _number(settings, "hard_shutdown_timeout_seconds"),
        )
    except OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError:
        raise AttesterProcessConfigurationError(
            "Kubernetes attester process configuration is invalid"
        ) from None


def build_server(config: AttesterProcessConfig) -> AttesterHttpsServer:
    """Assemble the read-only inspector, durable ledger, service, and mTLS server."""

    if type(config) is not AttesterProcessConfig or os.geteuid() != 0:
        raise AttesterProcessConfigurationError(
            "Kubernetes attester process configuration is invalid"
        )
    key = _private_key(config.inventory_authentication_key_file)
    _state_directory(config.inventory_path.parent)
    api = KubernetesReadApi.in_cluster(
        operation_seconds=config.inspector_limits.operation_seconds
    )
    inspector = BoundedCriCgroupInspector(
        config.inspector,
        config.inspector_limits,
        api,
        build_cri_command(config.cri_executable, config.inspector_limits),
    )
    fence = inspector.node_fence(config.node_name)
    if fence.ready is not True:
        raise AttesterProcessConfigurationError("Kubernetes node fence is not ready")
    ledger = SQLiteNodeAttesterLedger(
        config.inventory_path,
        key,
        max_records=config.inventory_max_records,
    )
    service = NodeAttesterService(
        NodeAttestationEngine(inspector), ledger, node_name=config.node_name
    )
    return AttesterHttpsServer(service, config.tls, config.transport_limits)


class AttesterProcess:
    """Serve until signalled, with an independent hard shutdown deadline."""

    def __init__(
        self,
        server: AttesterHttpsServer,
        *,
        hard_shutdown_timeout_seconds: float,
        hard_exit: Callable[[int], Never] = os._exit,
    ) -> None:
        if (
            type(server) is not AttesterHttpsServer
            or type(hard_shutdown_timeout_seconds) not in {int, float}
            or not math.isfinite(hard_shutdown_timeout_seconds)
            or hard_shutdown_timeout_seconds <= 0
            or not callable(hard_exit)
        ):
            raise ValueError("Kubernetes attester process is invalid")
        self._server = server
        self._timeout = float(hard_shutdown_timeout_seconds)
        self._hard_exit = hard_exit
        self._shutdown_started = Event()
        self._shutdown_complete = Event()

    def run(self) -> int:
        previous = {
            number: signal.signal(number, self._signal)
            for number in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            self._server.serve_forever()
            return 0
        finally:
            self._shutdown_complete.set()
            for number, handler in previous.items():
                signal.signal(number, handler)

    def _signal(self, _number: int, _frame: object) -> None:
        if self._shutdown_started.is_set():
            self._hard_exit(1)
        self._shutdown_started.set()
        Thread(target=self._shutdown, daemon=True, name="attester-shutdown").start()
        Thread(target=self._watchdog, daemon=True, name="attester-watchdog").start()

    def _shutdown(self) -> None:
        self._server.shutdown()

    def _watchdog(self) -> None:
        if not self._shutdown_complete.wait(self._timeout):
            self._hard_exit(1)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the node attester with content-free terminal diagnostics."""

    arguments = tuple(argv if argv is not None else sys.argv[1:])
    node_name = os.environ.get("MARKWEAVE_NODE_NAME", "")
    if len(arguments) != 1 or not node_name:
        os.write(2, b"Kubernetes attester configuration failed\n")
        return 2
    try:
        config = load_config(Path(arguments[0]), node_name=node_name)
        server = build_server(config)
        return AttesterProcess(
            server,
            hard_shutdown_timeout_seconds=config.hard_shutdown_timeout_seconds,
        ).run()
    except AttesterProcessConfigurationError:
        os.write(2, b"Kubernetes attester configuration failed\n")
        return 2
    except BaseException:
        os.write(2, b"Kubernetes attester runtime failed\n")
        return 1


def _private_key(path: Path) -> bytes:
    try:
        opened = path.lstat()
        key = path.read_bytes()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != _FILE_MODE
            or opened.st_nlink != 1
            or len(key) != _KEY_BYTES
        ):
            raise ValueError
        return key
    except OSError, ValueError:
        raise AttesterProcessConfigurationError(
            "Kubernetes attester authentication key is invalid"
        ) from None


def _state_directory(path: Path) -> None:
    try:
        opened = path.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) != _STATE_DIRECTORY_MODE
        ):
            raise ValueError
    except OSError, ValueError:
        raise AttesterProcessConfigurationError(
            "Kubernetes attester state directory is invalid"
        ) from None


def _text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if type(result) is not str or not result or "\x00" in result:
        raise ValueError
    return result


def _path(value: Mapping[str, object], key: str) -> Path:
    result = Path(_text(value, key))
    if not result.is_absolute():
        raise ValueError
    return result


def _integer(value: Mapping[str, object], key: str, *, allow_zero: bool = False) -> int:
    result = value.get(key)
    minimum = 0 if allow_zero else 1
    if type(result) is not int or result < minimum:
        raise ValueError
    return result


def _number(value: Mapping[str, object], key: str) -> float:
    result = value.get(key)
    if type(result) is int:
        numeric = float(result)
    elif type(result) is float:
        numeric = result
    else:
        raise ValueError
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError
    return numeric


if __name__ == "__main__":  # pragma: no cover - console script boundary
    raise SystemExit(main())
