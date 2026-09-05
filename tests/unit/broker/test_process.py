"""Tests for secure host-native broker process assembly."""

from __future__ import annotations

import json
import os
import pwd
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.mtls_transport import (
    MtlsEndpoint,
    MtlsTransportLimits,
)
from markweave.broker.process import (
    BrokerProcess,
    BrokerProcessConfigurationError,
    build_broker_server,
    load_broker_process_config,
    main,
)
from markweave.broker.unix_transport import UnixBrokerServer, UnixTransportLimits


def _directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _configuration(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    config_parent = _directory(tmp_path / "config")
    state = _directory(tmp_path / "state")
    sockets = _directory(tmp_path / "sockets")
    hooks = _directory(tmp_path / "hooks")
    keys = _directory(tmp_path / "keys")
    key = keys / "inventory.key"
    key.write_text("11" * 32 + "\n", encoding="ascii")
    key.chmod(0o400)
    value: dict[str, object] = {
        "channel_limits": {"max_input_bytes": 101, "max_output_bytes": 202},
        "hard_shutdown_timeout_seconds": 3,
        "hooks_directory": str(hooks),
        "image_digest": "sha256:" + "a" * 64,
        "image_repository": "localhost/markweave-attempt",
        "inventory_key_path": str(key),
        "max_units": 4,
        "podman": {"operation_timeout_seconds": 2, "output_bytes": 65_536},
        "policy_revision": "integration",
        "principal_id": "11111111-1111-4111-8111-111111111111",
        "runtime_limits": {
            "cpu_period_micros": 100_000,
            "cpu_quota_micros": 50_000,
            "memory_bytes": 268_435_456,
            "pid_limit": 16,
            "wall_time_millis": 10_000,
            "workspace_bytes": 8_388_608,
        },
        "schema_version": 1,
        "socket_path": str(sockets / "broker.sock"),
        "state_directory": str(state),
        "transport": {
            "listen_backlog": 2,
            "max_handlers": 2,
            "operation_timeout_seconds": 1,
            "shutdown_timeout_seconds": 2,
        },
    }
    path = config_parent / "broker.json"
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    path.chmod(0o600)
    return path, value


def _mtls_configuration(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    path, value = _configuration(tmp_path)
    material = _directory(tmp_path / "mtls")
    for name, content in (
        ("ca.pem", "CA"),
        ("server.pem", "CERTIFICATE"),
        ("server.key", "PRIVATE KEY"),
    ):
        candidate = material / name
        candidate.write_text(content, encoding="ascii")
        candidate.chmod(0o600)
    value.pop("socket_path")
    value["schema_version"] = 2
    value["transport_kind"] = "mtls"
    value["transport"] = {
        "listen_backlog": 2,
        "max_handlers": 2,
        "max_handshakes": 2,
        "max_pending_exchanges": 2,
        "operation_timeout_seconds": 1,
        "shutdown_timeout_seconds": 2,
    }
    value["mtls"] = {
        "ca_certificate_path": str(material / "ca.pem"),
        "certificate_chain_path": str(material / "server.pem"),
        "client_leaf_certificate_sha256": ["sha256:" + "b" * 64],
        "client_uri_san": "spiffe://markweave.test/worker",
        "endpoint_host": "127.0.0.1",
        "endpoint_port": 9443,
        "local_principal_id": "22222222-2222-4222-8222-222222222222",
        "local_uri_san": "spiffe://markweave.test/broker",
        "private_key_path": str(material / "server.key"),
    }
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    return path, value


@pytest.mark.unit
def test_loads_complete_canonical_owner_only_configuration(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)

    config = load_broker_process_config(path)

    assert config.socket_path is not None
    assert config.socket_path.name == "broker.sock"
    assert config.max_units == 4
    assert config.policy.channel_limits.max_input_bytes == 101
    assert config.authentication_key == b"\x11" * 32
    assert "11" * 32 not in repr(config)


@pytest.mark.unit
def test_loads_explicit_unix_v2_without_changing_unix_contract(tmp_path: Path) -> None:
    path, value = _configuration(tmp_path)
    value["schema_version"] = 2
    value["transport_kind"] = "unix"
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    config = load_broker_process_config(path)

    assert config.transport_kind == "unix"
    assert config.socket_path is not None
    assert config.mtls_endpoint is None
    assert type(config.transport_limits) is UnixTransportLimits


@pytest.mark.unit
def test_loads_explicit_mtls_configuration_with_no_unix_socket(
    tmp_path: Path,
) -> None:
    path, _ = _mtls_configuration(tmp_path)

    config = load_broker_process_config(path)

    assert config.transport_kind == "mtls"
    assert config.socket_path is None
    assert config.mtls_endpoint == MtlsEndpoint("127.0.0.1", 9443)
    assert type(config.transport_limits) is MtlsTransportLimits
    assert config.mtls_local_identity is not None
    assert config.mtls_client_identity is not None
    assert config.mtls_client_identity.principal == config.principal
    assert "PRIVATE KEY" not in repr(config)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"schema_version": 3}),
        lambda value: value.pop("transport_kind"),
        lambda value: value.update({"transport_kind": "tcp"}),
        lambda value: value["transport"].pop("max_handshakes"),  # type: ignore[union-attr]
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"endpoint_host": "localhost"}
        ),
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"endpoint_port": 0}
        ),
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"client_leaf_certificate_sha256": []}
        ),
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"client_leaf_certificate_sha256": ["sha256:" + "A" * 64]}
        ),
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"client_uri_san": "https://markweave.test/worker"}
        ),
        lambda value: value["mtls"].update(  # type: ignore[union-attr]
            {"local_principal_id": value["principal_id"]}
        ),
        lambda value: value["mtls"].update({"unknown": True}),  # type: ignore[union-attr]
    ],
)
def test_rejects_incomplete_or_noncanonical_mtls_configuration(
    tmp_path: Path, mutation: Callable[[dict[str, object]], None]
) -> None:
    path, value = _mtls_configuration(tmp_path)
    mutation(value)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_linked_or_insecure_mtls_private_key(tmp_path: Path) -> None:
    path, value = _mtls_configuration(tmp_path)
    mtls = value["mtls"]
    assert isinstance(mtls, dict)
    private_key = Path(cast(str, mtls["private_key_path"]))
    private_key.chmod(0o644)
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)

    private_key.chmod(0o600)
    (private_key.parent / "linked.key").hardlink_to(private_key)
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_mtls_fifo_without_blocking(tmp_path: Path) -> None:
    path, value = _mtls_configuration(tmp_path)
    mtls = value["mtls"]
    assert isinstance(mtls, dict)
    private_key = Path(cast(str, mtls["private_key_path"]))
    private_key.unlink()
    os.mkfifo(private_key, mode=0o600)

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"unknown": 1}),
        lambda value: value.update({"schema_version": True}),
        lambda value: value.update({"max_units": True}),
        lambda value: value["transport"].update(  # type: ignore[union-attr]
            {"operation_timeout_seconds": float("inf")}
        ),
        lambda value: value["transport"].update(  # type: ignore[union-attr]
            {"operation_timeout_seconds": "1"}
        ),
        lambda value: value.update({"socket_path": "relative.sock"}),
        lambda value: value.update({"socket_path": "/"}),
        lambda value: value["runtime_limits"].update(  # type: ignore[union-attr]
            {"pid_limit": 0}
        ),
        lambda value: value.update({"socket_path": None}),
        lambda value: value.update({"hard_shutdown_timeout_seconds": 0}),
        lambda value: value.update({"hard_shutdown_timeout_seconds": 10**400}),
        lambda value: value.update({"hard_shutdown_timeout_seconds": 2}),
        lambda value: value.update({"image_repository": "INVALID"}),
        lambda value: value.update({"principal_id": None}),
        lambda value: value.update(
            {"principal_id": "11111111-1111-4111-8111-11111111111A"}
        ),
    ],
)
def test_rejects_invalid_configuration_values(
    tmp_path: Path, mutation: Callable[[dict[str, object]], None]
) -> None:
    path, value = _configuration(tmp_path)
    mutation(value)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/broker.json", Path("broker.json")])
def test_rejects_non_path_or_relative_configuration_path(path: object) -> None:
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(cast(Any, path))


@pytest.mark.unit
@pytest.mark.parametrize("raw", [b'{"schema_version":1, "x":2}\n', b"{}"])
def test_rejects_noncanonical_or_missing_configuration(
    tmp_path: Path, raw: bytes
) -> None:
    path, _ = _configuration(tmp_path)
    path.write_bytes(raw)

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_non_ascii_configuration(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)
    path.write_bytes(b"\xff")

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_duplicate_configuration_keys(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="ascii")

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_nonfinite_json_float_literal_before_canonicalization(
    tmp_path: Path,
) -> None:
    path, _ = _configuration(tmp_path)
    encoded = path.read_text(encoding="ascii")
    path.write_text(
        encoded.replace(
            '"hard_shutdown_timeout_seconds":3', '"hard_shutdown_timeout_seconds":1e400'
        ),
        encoding="ascii",
    )

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_oversized_configuration_before_decoding(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)
    path.write_bytes(b"{" + b" " * 16_384 + b"}")

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_insecure_or_linked_key(tmp_path: Path) -> None:
    path, value = _configuration(tmp_path)
    key_value = value["inventory_key_path"]
    assert isinstance(key_value, str)
    key = Path(key_value)
    key.chmod(0o644)

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)

    key.chmod(0o400)
    linked = key.parent / "linked.key"
    linked.hardlink_to(key)
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "key_value",
    ["11" * 31 + "\n", "zz" * 32 + "\n", "AA" * 32 + "\n"],
)
def test_rejects_noncanonical_key_encoding(tmp_path: Path, key_value: str) -> None:
    path, value = _configuration(tmp_path)
    key_path = value["inventory_key_path"]
    assert isinstance(key_path, str)
    key = Path(key_path)
    key.chmod(0o600)
    key.write_text(key_value, encoding="ascii")
    key.chmod(0o400)

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_shared_or_insecure_runtime_directories(tmp_path: Path) -> None:
    path, value = _configuration(tmp_path)
    hooks_directory = value["hooks_directory"]
    value["hooks_directory"] = value["state_directory"]
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)

    value["hooks_directory"] = hooks_directory
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    state_value = value["state_directory"]
    assert isinstance(state_value, str)
    Path(state_value).chmod(0o750)
    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_missing_runtime_directory(tmp_path: Path) -> None:
    path, value = _configuration(tmp_path)
    value["state_directory"] = str(tmp_path / "missing-state")
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_rejects_nonempty_hooks_directory(tmp_path: Path) -> None:
    path, value = _configuration(tmp_path)
    hooks = value["hooks_directory"]
    assert isinstance(hooks, str)
    (Path(hooks) / "ambient-hook.json").write_text("{}", encoding="ascii")

    with pytest.raises(BrokerProcessConfigurationError):
        load_broker_process_config(path)


@pytest.mark.unit
def test_factory_uses_derived_identity_capacity_and_hermetic_commands(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    config = load_broker_process_config(path)
    old_umask = os.umask(0o022)
    os.umask(old_umask)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    runtime = mocker.patch("markweave.broker.process.PodmanIsolationRuntime")
    service = mocker.patch("markweave.broker.process.IsolationBrokerService")
    dispatcher = mocker.patch("markweave.broker.process.BrokerDispatcher")
    server = mocker.patch("markweave.broker.process.UnixBrokerServer")
    server.return_value._adopt_authority_lock.side_effect = os.close
    runner = mocker.patch("markweave.broker.process.BoundedCommandRunner")
    try:
        built = build_broker_server(config)
    finally:
        os.umask(old_umask)

    assert built is server.return_value
    inventory.assert_called_once_with(
        config.state_directory / "inventory.sqlite3",
        b"\x11" * 32,
        max_records=4,
    )
    assert service.call_args.kwargs["max_discovered_units"] == 4
    dispatcher.assert_called_once_with(service.return_value)
    assert runtime.call_args.kwargs["run_as_uid"] == os.geteuid()
    assert runtime.call_args.kwargs["cgroup_root"] == Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service"
    )
    environments = [call.kwargs["environment"] for call in runner.call_args_list]
    assert environments[0] == {
        "CONTAINERS_CONF": "/dev/null",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.geteuid()}/bus",
        "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": f"/run/user/{os.geteuid()}",
    }
    assert all(
        not any(key.startswith("CONTAINER_") for key in environment)
        for environment in environments
    )


@pytest.mark.unit
def test_factory_selects_mtls_with_exact_loaded_identity_and_workspace_limits(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _mtls_configuration(tmp_path)
    config = load_broker_process_config(path)
    mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    mocker.patch("markweave.broker.process.PodmanIsolationRuntime")
    service = mocker.patch("markweave.broker.process.IsolationBrokerService")
    dispatcher = mocker.patch("markweave.broker.process.BrokerDispatcher")
    unix_server = mocker.patch("markweave.broker.process.UnixBrokerServer")
    mtls_server = mocker.patch("markweave.broker.process.MtlsBrokerServer")
    mtls_server.return_value._adopt_authority_lock.side_effect = os.close
    context_builder = mocker.patch(
        "markweave.broker.process.build_mtls_server_context", return_value=object()
    )
    mocker.patch("markweave.broker.process.BoundedCommandRunner")

    built = build_broker_server(config)

    assert built is mtls_server.return_value
    unix_server.assert_not_called()
    dispatcher.assert_called_once_with(service.return_value)
    mtls_server.assert_called_once_with(
        config.mtls_endpoint,
        local_identity=config.mtls_local_identity,
        client_identity=config.mtls_client_identity,
        dispatcher=dispatcher.return_value,
        limits=config.transport_limits,
        workspace_limits=config.policy.channel_limits,
        server_context=context_builder.return_value,
    )
    assert config.mtls_local_identity is not None
    loaded_identity = context_builder.call_args.args[0]
    assert loaded_identity.principal == config.mtls_local_identity.principal
    assert str(loaded_identity.private_key).startswith("/proc/self/fd/")
    assert context_builder.call_args.kwargs == {
        "declared_identity": config.mtls_local_identity
    }


@pytest.mark.unit
def test_factory_rejects_invalid_mtls_pem_before_inventory_mutation(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _mtls_configuration(tmp_path)
    config = load_broker_process_config(path)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    authority = mocker.patch("markweave.broker.process._acquire_authority_lock")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()
    authority.assert_not_called()
    assert not (config.state_directory / "inventory.sqlite3").exists()


@pytest.mark.unit
def test_factory_preserves_secure_mtls_file_rejection_before_inventory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _mtls_configuration(tmp_path)
    config = load_broker_process_config(path)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    mocker.patch(
        "markweave.broker.process._open_secure_file",
        side_effect=BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ),
    )

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()


@pytest.mark.unit
def test_factory_rejects_changed_mtls_file_length_before_inventory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _mtls_configuration(tmp_path)
    config = load_broker_process_config(path)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    authority = mocker.patch("markweave.broker.process._acquire_authority_lock")
    mocker.patch("markweave.broker.process.os.pread", return_value=b"")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()
    authority.assert_not_called()
    assert not (config.state_directory / "inventory.sqlite3").exists()


@pytest.mark.unit
def test_factory_rejects_mixed_unix_and_mtls_state_before_inventory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    config = replace(
        load_broker_process_config(path),
        mtls_endpoint=MtlsEndpoint("127.0.0.1", 9443),
    )
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("invalid_field", ["socket", "limits"])
def test_factory_rejects_incoherent_unix_state_before_inventory(
    tmp_path: Path, mocker: MockerFixture, invalid_field: str
) -> None:
    path, _ = _configuration(tmp_path)
    config = load_broker_process_config(path)
    if invalid_field == "socket":
        config = replace(config, socket_path=None)
    else:
        config = replace(
            config,
            transport_limits=MtlsTransportLimits(1, 1, 2, 2, 2, 2),
        )
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    authority = mocker.patch("markweave.broker.process._acquire_authority_lock")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()
    authority.assert_not_called()
    assert not (config.state_directory / "inventory.sqlite3").exists()


@pytest.mark.unit
@pytest.mark.parametrize("invalid_field", ["socket", "limits", "endpoint", "kind"])
def test_factory_rejects_incoherent_mtls_state_before_inventory(
    tmp_path: Path, mocker: MockerFixture, invalid_field: str
) -> None:
    path, _ = _mtls_configuration(tmp_path)
    config = load_broker_process_config(path)
    if invalid_field == "socket":
        config = replace(config, socket_path=tmp_path / "broker.sock")
    elif invalid_field == "limits":
        config = replace(config, transport_limits=UnixTransportLimits(1, 2, 2, 2))
    elif invalid_field == "endpoint":
        config = replace(config, mtls_endpoint=None)
    else:
        config = replace(config, transport_kind="tcp")
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()


@pytest.mark.unit
def test_factory_rejects_linked_inventory_before_sqlite_creation(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    config = load_broker_process_config(path)
    external = tmp_path / "external.sqlite3"
    external.write_bytes(b"")
    external.chmod(0o600)
    (config.state_directory / "inventory.sqlite3").hardlink_to(external)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    inventory.assert_not_called()


@pytest.mark.unit
def test_factory_rejects_root_and_missing_fixed_commands_before_inventory(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    config = load_broker_process_config(path)
    inventory = mocker.patch("markweave.broker.process.SQLiteBrokerInventory")
    mocker.patch("markweave.broker.process.os.geteuid", return_value=0)
    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)
    inventory.assert_not_called()

    mocker.patch("markweave.broker.process.os.geteuid", return_value=1000)
    mocker.patch("markweave.broker.process._PODMAN", tmp_path / "missing-podman")
    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)
    inventory.assert_not_called()


@pytest.mark.unit
def test_factory_rejects_wrong_config_type() -> None:
    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(cast(Any, object()))


@pytest.mark.unit
def test_factory_rejects_non_bytes_key_and_releases_authority_lock(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    config = replace(
        load_broker_process_config(path), authentication_key=cast(Any, "not-bytes")
    )
    descriptor = os.open(os.devnull, os.O_RDONLY)
    mocker.patch(
        "markweave.broker.process._acquire_authority_lock", return_value=descriptor
    )

    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)

    with pytest.raises(OSError):
        os.fstat(descriptor)


def _server(tmp_path: Path, mocker: MockerFixture) -> UnixBrokerServer:
    parent = _directory(tmp_path / "socket")
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    return UnixBrokerServer(
        parent / "broker.sock",
        expected_client_uid=os.geteuid(),
        principal=AuthenticatedPrincipal(UUID("22222222-2222-4222-8222-222222222222")),
        dispatcher=dispatcher,
        limits=UnixTransportLimits(1, 1, 1, 1),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("server_value", "timeout", "hard_exit"),
    [
        ("server", 1, os._exit),
        (None, "1", os._exit),
        (None, 0, os._exit),
        (None, float("inf"), os._exit),
        (None, 1, None),
    ],
)
def test_process_rejects_invalid_constructor_arguments(
    tmp_path: Path,
    mocker: MockerFixture,
    server_value: object,
    timeout: object,
    hard_exit: object,
) -> None:
    server = _server(tmp_path, mocker) if server_value is None else server_value

    with pytest.raises(ValueError, match="Broker process is invalid"):
        BrokerProcess(
            cast(Any, server),
            hard_shutdown_timeout_seconds=cast(Any, timeout),
            hard_exit=cast(Any, hard_exit),
        )


@pytest.mark.unit
def test_process_returns_nonzero_for_internal_fatal(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _server(tmp_path, mocker)
    hard_exit = mocker.Mock()
    mocker.patch.object(
        server, "start", side_effect=lambda: server._record_fatal(ValueError())
    )
    mocker.patch.object(server, "stop", side_effect=RuntimeError())
    process = BrokerProcess(
        server, hard_shutdown_timeout_seconds=0.001, hard_exit=hard_exit
    )

    assert process.run() == 1
    assert process._watchdog is not None
    process._watchdog.join()
    assert not process._shutdown_complete.is_set()
    hard_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_repeated_signal_escalates_without_reopening_admission(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _server(tmp_path, mocker)
    hard_exit = mocker.Mock()
    process = BrokerProcess(
        server, hard_shutdown_timeout_seconds=1, hard_exit=hard_exit
    )

    process._handle_signal(15, None)
    process._shutdown_complete.set()
    process._handle_signal(15, None)
    assert process._watchdog is not None
    process._watchdog.join()

    assert server.stopping
    hard_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_signal_during_reconciliation_is_reapplied_before_waiting(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _server(tmp_path, mocker)
    process = BrokerProcess(server, hard_shutdown_timeout_seconds=1)
    start = mocker.patch.object(
        server, "start", side_effect=lambda: process._handle_signal(15, None)
    )
    stop = mocker.patch.object(server, "stop")

    assert process.run() == 0

    start.assert_called_once_with()
    stop.assert_called_once_with()
    assert server.stopping


@pytest.mark.unit
def test_independent_watchdog_hard_exits_when_shutdown_never_completes(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _server(tmp_path, mocker)
    hard_exit = mocker.Mock()
    process = BrokerProcess(
        server, hard_shutdown_timeout_seconds=0.001, hard_exit=hard_exit
    )

    process._watch_shutdown()

    hard_exit.assert_called_once_with(1)


@pytest.mark.unit
def test_main_emits_only_bounded_content_free_diagnostics(
    capfd: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main([]) == 2
    assert capfd.readouterr() == ("", "broker configuration failed\n")

    missing = tmp_path / "private-document-name.json"
    assert main([str(missing)]) == 2
    captured = capfd.readouterr()
    assert captured == ("", "broker configuration failed\n")
    assert "private-document-name" not in captured.err


@pytest.mark.unit
def test_main_contains_unexpected_runtime_failure(
    capfd: pytest.CaptureFixture[str], tmp_path: Path, mocker: MockerFixture
) -> None:
    path, _ = _configuration(tmp_path)
    mocker.patch(
        "markweave.broker.process.build_broker_server",
        side_effect=RuntimeError("private runtime detail"),
    )

    assert main([str(path)]) == 1
    assert capfd.readouterr() == ("", "broker runtime failed\n")
