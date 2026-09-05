"""Tests for secure host-native broker process assembly."""

from __future__ import annotations

import json
import os
import pwd
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.models import AuthenticatedPrincipal
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


@pytest.mark.unit
def test_loads_complete_canonical_owner_only_configuration(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)

    config = load_broker_process_config(path)

    assert config.socket_path.name == "broker.sock"
    assert config.max_units == 4
    assert config.policy.channel_limits.max_input_bytes == 101
    assert config.authentication_key == b"\x11" * 32
    assert "11" * 32 not in repr(config)


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
def test_rejects_duplicate_configuration_keys(tmp_path: Path) -> None:
    path, _ = _configuration(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="ascii")

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

    mocker.patch("markweave.broker.process.os.geteuid", return_value=os.geteuid())
    mocker.patch("markweave.broker.process._PODMAN", tmp_path / "missing-podman")
    with pytest.raises(BrokerProcessConfigurationError):
        build_broker_server(config)
    inventory.assert_not_called()


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
