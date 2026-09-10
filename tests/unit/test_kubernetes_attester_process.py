from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, cast

import pytest

from markweave import kubernetes_attester_process as target
from markweave.broker.kubernetes_attester_transport import AttesterHttpsServer
from markweave.kubernetes_attester_process import (
    AttesterProcess,
    AttesterProcessConfig,
    AttesterProcessConfigurationError,
    build_server,
    load_config,
    main,
)


def _settings(tmp_path: Path) -> dict[str, object]:
    return {
        "cgroup_root": str(tmp_path / "cgroup"),
        "client_ca_file": str(tmp_path / "ca.crt"),
        "cri_endpoint": "unix:///host/run/k3s/containerd/containerd.sock",
        "cri_executable": "/host/usr/local/bin/crictl",
        "expected_client_certificate_sha256": f"sha256:{'1' * 64}",
        "hard_shutdown_timeout_seconds": 5,
        "inventory_authentication_key_file": str(tmp_path / "key"),
        "inventory_max_records": 8,
        "inventory_path": str(tmp_path / "state" / "inventory.sqlite3"),
        "kubelet_config": str(tmp_path / "kubelet.yaml"),
        "listen_host": "0.0.0.0",  # noqa: S104 - explicit container bind address
        "listen_port": 9443,
        "max_cgroup_directories": 16,
        "max_concurrent_requests": 4,
        "max_containers": 2,
        "max_descendant_pids": 64,
        "max_request_bytes": 131072,
        "max_response_bytes": 65536,
        "max_sandboxes": 4,
        "operation_timeout_seconds": 2,
        "proc_root": str(tmp_path / "proc"),
        "request_timeout_seconds": 3,
        "server_certificate_file": str(tmp_path / "tls.crt"),
        "server_private_key_file": str(tmp_path / "tls.key"),
    }


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "attester.json"
    path.write_text(json.dumps(_settings(tmp_path)), encoding="ascii")
    return path


@pytest.mark.unit
def test_load_config_closes_and_types_every_process_input(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path), node_name="reverse-node-1")
    assert config.node_name == "reverse-node-1"
    assert config.inspector.node_name == "reverse-node-1"
    assert config.cri_executable == Path("/host/usr/local/bin/crictl")
    assert config.inspector_limits.output_bytes == 65536
    assert config.transport_limits.timeout_seconds == 3
    assert config.tls.port == 9443
    assert config.inventory_max_records == 8
    assert config.hard_shutdown_timeout_seconds == 5


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.pop("cri_endpoint"),
        lambda value: value.update(listen_port=-1),
        lambda value: value.update(operation_timeout_seconds=float("inf")),
        lambda value: value.update(inventory_path="relative"),
        lambda value: value.update(cri_endpoint="tcp://runtime"),
    ],
)
def test_load_config_rejects_unknown_missing_or_invalid_values(
    tmp_path: Path, mutation: Any
) -> None:
    settings = _settings(tmp_path)
    mutation(settings)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(settings), encoding="ascii")
    with pytest.raises(AttesterProcessConfigurationError, match="configuration"):
        load_config(path, node_name="reverse-node-1")


@pytest.mark.unit
def test_process_config_model_rejects_invalid_identity(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path), node_name="reverse-node-1")
    with pytest.raises(ValueError, match="process configuration"):
        AttesterProcessConfig(
            "",
            config.inspector,
            config.inspector_limits,
            config.cri_executable,
            config.tls,
            config.transport_limits,
            config.inventory_path,
            config.inventory_authentication_key_file,
            config.inventory_max_records,
            config.hard_shutdown_timeout_seconds,
        )


@pytest.mark.unit
def test_build_server_assembles_only_after_positive_node_fence(
    tmp_path: Path, mocker: Any
) -> None:
    config = load_config(_config_file(tmp_path), node_name="reverse-node-1")
    mocker.patch.object(target.os, "geteuid", return_value=0)
    mocker.patch.object(target, "_private_key", return_value=b"k" * 32)
    state = mocker.patch.object(target, "_state_directory")
    api = mocker.patch.object(target.KubernetesReadApi, "in_cluster").return_value
    command = mocker.patch.object(target, "build_cri_command").return_value
    inspector_factory = mocker.patch.object(target, "BoundedCriCgroupInspector")
    inspector = inspector_factory.return_value
    inspector.node_fence.return_value = SimpleNamespace(ready=True)
    ledger = mocker.patch.object(target, "SQLiteNodeAttesterLedger").return_value
    engine = mocker.patch.object(target, "NodeAttestationEngine").return_value
    service_factory = mocker.patch.object(target, "NodeAttesterService")
    expected = mocker.patch.object(target, "AttesterHttpsServer").return_value

    assert build_server(config) is expected
    state.assert_called_once_with(config.inventory_path.parent)
    inspector_factory.assert_called_once_with(
        config.inspector, config.inspector_limits, api, command
    )
    service_factory.assert_called_once_with(engine, ledger, node_name="reverse-node-1")

    inspector.node_fence.return_value = SimpleNamespace(ready=False)
    with pytest.raises(AttesterProcessConfigurationError, match="not ready"):
        build_server(config)


@pytest.mark.unit
def test_build_server_rejects_non_root_process(tmp_path: Path, mocker: Any) -> None:
    config = load_config(_config_file(tmp_path), node_name="reverse-node-1")
    mocker.patch.object(target.os, "geteuid", return_value=1001)
    with pytest.raises(AttesterProcessConfigurationError, match="configuration"):
        build_server(config)


@pytest.mark.unit
def test_private_key_and_state_directory_enforce_root_owned_modes(
    tmp_path: Path, mocker: Any
) -> None:
    key = tmp_path / "key"
    key.write_bytes(b"k" * 32)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    real_key_stat = key.lstat()
    real_state_stat = state.lstat()

    mocker.patch.object(
        Path,
        "lstat",
        side_effect=[
            SimpleNamespace(
                st_mode=(real_key_stat.st_mode & ~0o777) | 0o400,
                st_uid=0,
                st_nlink=1,
            ),
            SimpleNamespace(
                st_mode=(real_state_stat.st_mode & ~0o777) | 0o700,
                st_uid=0,
            ),
        ],
    )
    assert target._private_key(key) == b"k" * 32
    target._state_directory(state)

    mocker.patch.object(
        Path,
        "lstat",
        return_value=SimpleNamespace(
            st_mode=(real_key_stat.st_mode & ~0o777) | 0o644,
            st_uid=1001,
            st_nlink=2,
        ),
    )
    with pytest.raises(AttesterProcessConfigurationError, match="key is invalid"):
        target._private_key(key)
    with pytest.raises(AttesterProcessConfigurationError, match="directory is invalid"):
        target._state_directory(state)


@pytest.mark.unit
def test_attester_process_serves_and_restores_signal_handlers(mocker: Any) -> None:
    server = object.__new__(AttesterHttpsServer)
    serve = mocker.patch.object(AttesterHttpsServer, "serve_forever")
    handlers: list[tuple[int, object]] = []
    mocker.patch.object(
        target.signal,
        "signal",
        side_effect=lambda number, handler: handlers.append((number, handler)) or None,
    )
    process = AttesterProcess(server, hard_shutdown_timeout_seconds=1)
    assert process.run() == 0
    serve.assert_called_once_with()
    assert [number for number, _ in handlers[:2]] == [signal.SIGINT, signal.SIGTERM]
    assert len(handlers) == 4


@pytest.mark.unit
def test_attester_process_signal_starts_shutdown_and_double_signal_exits(
    mocker: Any,
) -> None:
    server = object.__new__(AttesterHttpsServer)
    shutdown = mocker.patch.object(AttesterHttpsServer, "shutdown")
    exits: list[int] = []

    def hard_exit(code: int) -> Never:
        exits.append(code)
        raise RuntimeError("hard exit")

    process = AttesterProcess(
        server, hard_shutdown_timeout_seconds=1, hard_exit=hard_exit
    )
    thread = mocker.patch.object(target, "Thread")
    process._signal(signal.SIGTERM, None)
    assert thread.call_count == 2
    process._shutdown()
    shutdown.assert_called_once_with()
    process._shutdown_complete.set()
    process._watchdog()
    with pytest.raises(RuntimeError, match="hard exit"):
        process._signal(signal.SIGTERM, None)
    assert exits == [1]


@pytest.mark.unit
def test_main_returns_stable_content_free_statuses(
    tmp_path: Path, mocker: Any, capfd: Any
) -> None:
    mocker.patch.dict(target.os.environ, {}, clear=True)
    assert main([]) == 2
    assert "configuration failed" in capfd.readouterr().err

    mocker.patch.dict(target.os.environ, {"MARKWEAVE_NODE_NAME": "node"})
    mocker.patch.object(
        target,
        "load_config",
        side_effect=AttesterProcessConfigurationError("sensitive"),
    )
    assert main([str(tmp_path / "config")]) == 2
    assert "sensitive" not in capfd.readouterr().err

    config = cast(
        AttesterProcessConfig, SimpleNamespace(hard_shutdown_timeout_seconds=1)
    )
    mocker.patch.object(target, "load_config", return_value=config)
    mocker.patch.object(target, "build_server", side_effect=RuntimeError("sensitive"))
    assert main([str(tmp_path / "config")]) == 1
    assert "sensitive" not in capfd.readouterr().err
