"""Host-native lifecycle-only assembly for the rootless Podman broker."""

from __future__ import annotations

import fcntl
import json
import math
import os
import pwd
import re
import signal
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread
from typing import Any, Never
from uuid import UUID

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerServer,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    MtlsServerContext,
    MtlsTransportLimits,
    build_mtls_server_context_from_material,
)
from markweave.broker.podman_runtime import (
    BoundedCommandRunner,
    PodmanCommandLimits,
    PodmanIsolationRuntime,
    SystemdCgroupRemover,
)
from markweave.broker.service import IsolationBrokerService
from markweave.broker.unix_transport import UnixBrokerServer, UnixTransportLimits

_CONFIG_MAX_BYTES = 16_384
_KEY_FILE_BYTES = 65
_OWNER_DIRECTORY_MODE = 0o700
_OWNER_FILE_MODES = frozenset({0o400, 0o600})
_INVENTORY_MODE = 0o600
_PODMAN = Path("/usr/bin/podman")
_SYSTEMCTL = Path("/usr/bin/systemctl")
_INVENTORY_NAME = "inventory.sqlite3"
_AUTHORITY_DIRECTORY_NAME = "markweave-broker"
_AUTHORITY_LOCK_NAME = "broker-authority.lock"
_LEGACY_SCHEMA_VERSION = 1
_TRANSPORT_SCHEMA_VERSION = 2
_DISTINCT_RUNTIME_DIRECTORIES = 3
_AUTHENTICATION_KEY_BYTES = 32
_IMAGE_REPOSITORY_MAX_BYTES = 128
_IMAGE_REPOSITORY_PATTERN = re.compile(
    r"(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?"
    r"/[a-z0-9][a-z0-9._/-]*\Z"
)
_TLS_MATERIAL_MAX_BYTES = 65_536
_TLS_MATERIAL_COUNT = 3
_UNIX_TRANSPORT = "unix"
_MTLS_TRANSPORT = "mtls"


class BrokerProcessConfigurationError(ValueError):
    """A content-free broker process configuration failure."""


@dataclass(frozen=True, slots=True)
class BrokerProcessConfig:
    """Complete deployment-supplied broker process configuration."""

    socket_path: Path | None
    state_directory: Path
    hooks_directory: Path
    inventory_key_path: Path
    image_repository: str
    principal: AuthenticatedPrincipal
    policy: BrokerPolicy
    max_units: int
    transport_limits: UnixTransportLimits | MtlsTransportLimits
    hard_shutdown_timeout_seconds: float
    podman_limits: PodmanCommandLimits
    authentication_key: bytes = field(repr=False)
    transport_kind: str = _UNIX_TRANSPORT
    mtls_endpoint: MtlsEndpoint | None = None
    mtls_local_identity: MtlsLocalIdentity | None = None
    mtls_client_identity: MtlsPeerIdentity | None = None
    mtls_material: tuple[bytes, bytes, bytes] | None = field(default=None, repr=False)


def _configuration_failure() -> Never:
    raise BrokerProcessConfigurationError("Broker process configuration is invalid")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _configuration_failure()
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _configuration_failure()
    return parsed


def _open_secure_file(path: Path, *, maximum: int) -> int:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        named = path.lstat()
        resolved = path.resolve(strict=True)
        if (
            resolved != path
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) not in _OWNER_FILE_MODES
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or not 0 < opened.st_size <= maximum
        ):
            _configuration_failure()
        return descriptor
    except BrokerProcessConfigurationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error


def _secure_file_bytes(path: Path, *, maximum: int) -> bytes:
    descriptor = _open_secure_file(path, maximum=maximum)
    try:
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = maximum + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            if len(value) > maximum or len(value) != opened.st_size:
                _configuration_failure()
            return value
        except BrokerProcessConfigurationError:
            raise
        except OSError as error:
            raise BrokerProcessConfigurationError(
                "Broker process configuration is invalid"
            ) from error
    finally:
        os.close(descriptor)


def _secure_directory(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    if (
        not path.is_absolute()
        or resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != _OWNER_DIRECTORY_MODE
    ):
        _configuration_failure()
    return metadata.st_dev, metadata.st_ino


def _required_object(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _configuration_failure()
    return value


def _positive_integer(value: Any) -> int:
    if type(value) is not int or value <= 0:
        _configuration_failure()
    return value


def _positive_number(value: Any) -> float:
    if type(value) not in {int, float}:
        _configuration_failure()
    try:
        number = float(value)
    except OverflowError:
        _configuration_failure()
    if number <= 0 or not math.isfinite(number):
        _configuration_failure()
    return number


def _absolute_path(value: Any) -> Path:
    if type(value) is not str or not value or "\x00" in value:
        _configuration_failure()
    path = Path(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        _configuration_failure()
    return path


def load_broker_process_config(  # noqa: PLR0912,PLR0915
    path: Path,
) -> BrokerProcessConfig:
    """Load one canonical, bounded, owner-only broker configuration and key."""

    if not isinstance(path, Path) or not path.is_absolute():
        _configuration_failure()
    _secure_directory(path.parent)
    raw = _secure_file_bytes(path, maximum=_CONFIG_MAX_BYTES)
    try:
        decoded = raw.decode("ascii")
        value = json.loads(
            decoded,
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: _configuration_failure(),
            parse_float=_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        _configuration_failure()
    schema_version = value["schema_version"]
    common_fields = {
        "channel_limits",
        "hard_shutdown_timeout_seconds",
        "hooks_directory",
        "image_digest",
        "image_repository",
        "inventory_key_path",
        "max_units",
        "podman",
        "policy_revision",
        "principal_id",
        "runtime_limits",
        "schema_version",
        "state_directory",
        "transport",
    }
    if schema_version == _LEGACY_SCHEMA_VERSION:
        transport_kind = _UNIX_TRANSPORT
        root_fields = frozenset(common_fields | {"socket_path"})
    elif schema_version == _TRANSPORT_SCHEMA_VERSION:
        transport_kind = value.get("transport_kind")
        if transport_kind == _UNIX_TRANSPORT:
            root_fields = frozenset(common_fields | {"socket_path", "transport_kind"})
        elif transport_kind == _MTLS_TRANSPORT:
            root_fields = frozenset(common_fields | {"mtls", "transport_kind"})
        else:
            _configuration_failure()
    else:
        _configuration_failure()
    root = _required_object(value, root_fields)
    canonical = (
        json.dumps(
            root,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if decoded != canonical or root["schema_version"] != schema_version:
        _configuration_failure()

    runtime = _required_object(
        root["runtime_limits"],
        frozenset(
            {
                "cpu_period_micros",
                "cpu_quota_micros",
                "memory_bytes",
                "pid_limit",
                "wall_time_millis",
                "workspace_bytes",
            }
        ),
    )
    channel = _required_object(
        root["channel_limits"],
        frozenset({"max_input_bytes", "max_output_bytes"}),
    )
    unix_transport_fields = {
        "listen_backlog",
        "max_handlers",
        "operation_timeout_seconds",
        "shutdown_timeout_seconds",
    }
    transport_fields = unix_transport_fields
    if transport_kind == _MTLS_TRANSPORT:
        transport_fields = unix_transport_fields | {
            "max_handshakes",
            "max_pending_exchanges",
        }
    transport = _required_object(root["transport"], frozenset(transport_fields))
    podman = _required_object(
        root["podman"], frozenset({"operation_timeout_seconds", "output_bytes"})
    )
    state_directory = _absolute_path(root["state_directory"])
    hooks_directory = _absolute_path(root["hooks_directory"])
    key_path = _absolute_path(root["inventory_key_path"])
    state_identity = _secure_directory(state_directory)
    hooks_identity = _secure_directory(hooks_directory)
    socket_path: Path | None = None
    if transport_kind == _UNIX_TRANSPORT:
        socket_path = _absolute_path(root["socket_path"])
        if socket_path.name in {"", ".", ".."}:
            _configuration_failure()
        directory_identities = {
            state_identity,
            _secure_directory(socket_path.parent),
            hooks_identity,
        }
    else:
        directory_identities = {state_identity, hooks_identity}
    expected_directory_count = (
        _DISTINCT_RUNTIME_DIRECTORIES if socket_path is not None else 2
    )
    if len(directory_identities) != expected_directory_count:
        _configuration_failure()
    try:
        if os.listdir(hooks_directory):
            _configuration_failure()
    except OSError as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    _secure_directory(key_path.parent)
    key_raw = _secure_file_bytes(key_path, maximum=_KEY_FILE_BYTES)
    if len(key_raw) != _KEY_FILE_BYTES or key_raw[-1:] != b"\n":
        _configuration_failure()
    try:
        key_text = key_raw[:-1].decode("ascii")
        authentication_key = bytes.fromhex(key_text)
    except (UnicodeDecodeError, ValueError) as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    if (
        len(authentication_key) != _AUTHENTICATION_KEY_BYTES
        or key_text != authentication_key.hex()
    ):
        _configuration_failure()
    try:
        if type(root["principal_id"]) is not str:
            _configuration_failure()
        image_repository = root["image_repository"]
        if (
            type(image_repository) is not str
            or _IMAGE_REPOSITORY_PATTERN.fullmatch(image_repository) is None
            or ".." in image_repository.split("/")
            or len(image_repository) > _IMAGE_REPOSITORY_MAX_BYTES
        ):
            _configuration_failure()
        principal = AuthenticatedPrincipal(UUID(root["principal_id"]))
        if str(principal.principal_id) != root["principal_id"]:
            _configuration_failure()
        policy = BrokerPolicy(
            root["policy_revision"],
            root["image_digest"],
            RuntimeLimits(
                *(
                    _positive_integer(runtime[field])
                    for field in RuntimeLimits.__dataclass_fields__
                )
            ),
            RuntimeChannelLimits(
                *(
                    _positive_integer(channel[field])
                    for field in RuntimeChannelLimits.__dataclass_fields__
                )
            ),
        )
        if transport_kind == _UNIX_TRANSPORT:
            limits: UnixTransportLimits | MtlsTransportLimits = UnixTransportLimits(
                _positive_number(transport["operation_timeout_seconds"]),
                _positive_number(transport["shutdown_timeout_seconds"]),
                _positive_integer(transport["max_handlers"]),
                _positive_integer(transport["listen_backlog"]),
            )
        else:
            limits = MtlsTransportLimits(
                _positive_number(transport["operation_timeout_seconds"]),
                _positive_number(transport["shutdown_timeout_seconds"]),
                _positive_integer(transport["max_handshakes"]),
                _positive_integer(transport["max_pending_exchanges"]),
                _positive_integer(transport["max_handlers"]),
                _positive_integer(transport["listen_backlog"]),
            )
        hard_shutdown = _positive_number(root["hard_shutdown_timeout_seconds"])
        podman_limits = PodmanCommandLimits(
            _positive_number(podman["operation_timeout_seconds"]),
            _positive_integer(podman["output_bytes"]),
        )
    except (TypeError, ValueError) as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    if hard_shutdown <= limits.shutdown_timeout_seconds:
        _configuration_failure()
    mtls_endpoint: MtlsEndpoint | None = None
    mtls_local_identity: MtlsLocalIdentity | None = None
    mtls_client_identity: MtlsPeerIdentity | None = None
    mtls_material: tuple[bytes, bytes, bytes] | None = None
    if transport_kind == _MTLS_TRANSPORT:
        mtls = _required_object(
            root["mtls"],
            frozenset(
                {
                    "ca_certificate_path",
                    "certificate_chain_path",
                    "client_leaf_certificate_sha256",
                    "client_uri_san",
                    "endpoint_host",
                    "endpoint_port",
                    "local_principal_id",
                    "local_uri_san",
                    "private_key_path",
                }
            ),
        )
        ca_path = _absolute_path(mtls["ca_certificate_path"])
        certificate_path = _absolute_path(mtls["certificate_chain_path"])
        private_key_path = _absolute_path(mtls["private_key_path"])
        material_paths = (ca_path, certificate_path, private_key_path)
        if len(set(material_paths)) != len(material_paths):
            _configuration_failure()
        loaded_material: list[bytes] = []
        for material_path in material_paths:
            _secure_directory(material_path.parent)
            loaded_material.append(
                _secure_file_bytes(material_path, maximum=_TLS_MATERIAL_MAX_BYTES)
            )
        mtls_material = (
            loaded_material[0],
            loaded_material[1],
            loaded_material[2],
        )
        try:
            local_principal_value = mtls["local_principal_id"]
            if type(local_principal_value) is not str:
                _configuration_failure()
            local_principal = AuthenticatedPrincipal(UUID(local_principal_value))
            if (
                str(local_principal.principal_id) != local_principal_value
                or local_principal == principal
            ):
                _configuration_failure()
            pins_value = mtls["client_leaf_certificate_sha256"]
            if type(pins_value) is not list:
                _configuration_failure()
            mtls_endpoint = MtlsEndpoint(
                mtls["endpoint_host"], _positive_integer(mtls["endpoint_port"])
            )
            mtls_local_identity = MtlsLocalIdentity(
                ca_path,
                certificate_path,
                private_key_path,
                mtls["local_uri_san"],
                local_principal,
            )
            mtls_client_identity = MtlsPeerIdentity(
                mtls["client_uri_san"], tuple(pins_value), principal
            )
        except (TypeError, ValueError) as error:
            raise BrokerProcessConfigurationError(
                "Broker process configuration is invalid"
            ) from error
    return BrokerProcessConfig(
        socket_path,
        state_directory,
        hooks_directory,
        key_path,
        image_repository,
        principal,
        policy,
        _positive_integer(root["max_units"]),
        limits,
        hard_shutdown,
        podman_limits,
        authentication_key,
        transport_kind,
        mtls_endpoint,
        mtls_local_identity,
        mtls_client_identity,
        mtls_material,
    )


def _inventory_leaf(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != _INVENTORY_MODE
        or metadata.st_nlink != 1
    ):
        _configuration_failure()


def _inventory_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        _inventory_leaf(candidate)


def _runtime_root() -> Path:
    root = Path(f"/run/user/{os.geteuid()}")
    _secure_directory(root)
    return root


def _acquire_authority_lock() -> int:
    authority_directory = _runtime_root() / _AUTHORITY_DIRECTORY_NAME
    try:
        authority_directory.mkdir(mode=_OWNER_DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    _secure_directory(authority_directory)
    path = authority_directory / _AUTHORITY_LOCK_NAME
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            _INVENTORY_MODE,
        )
        opened = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != _INVENTORY_MODE
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            _configuration_failure()
    except BrokerProcessConfigurationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        raise RuntimeError("Broker authority lock is unavailable") from error
    return descriptor


def _runtime_environment() -> tuple[dict[str, str], dict[str, str]]:
    uid = os.geteuid()
    home = pwd.getpwuid(uid).pw_dir
    runtime_directory = str(_runtime_root())
    common = {
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_directory}/bus",
        "HOME": home,
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": runtime_directory,
    }
    return {**common, "CONTAINERS_CONF": "/dev/null"}, common


def _prepared_mtls_server_context(
    config: BrokerProcessConfig,
) -> MtlsServerContext | None:
    if config.transport_kind == _UNIX_TRANSPORT:
        if (
            config.socket_path is None
            or type(config.transport_limits) is not UnixTransportLimits
            or config.mtls_endpoint is not None
            or config.mtls_local_identity is not None
            or config.mtls_client_identity is not None
            or config.mtls_material is not None
        ):
            _configuration_failure()
        return None
    material = config.mtls_material
    if (
        config.transport_kind != _MTLS_TRANSPORT
        or config.socket_path is not None
        or type(config.transport_limits) is not MtlsTransportLimits
        or config.mtls_endpoint is None
        or config.mtls_endpoint.port == 0
        or config.mtls_local_identity is None
        or config.mtls_client_identity is None
        or type(material) is not tuple
        or len(material) != _TLS_MATERIAL_COUNT
        or any(
            type(value) is not bytes or not 0 < len(value) <= _TLS_MATERIAL_MAX_BYTES
            for value in material
        )
        or config.mtls_local_identity.principal == config.principal
        or config.mtls_client_identity.principal != config.principal
    ):
        _configuration_failure()
    local = config.mtls_local_identity
    try:
        return build_mtls_server_context_from_material(local, material)
    except BrokerProcessConfigurationError:
        raise
    except (OSError, ValueError) as error:
        raise BrokerProcessConfigurationError(
            "Broker process configuration is invalid"
        ) from error


def build_broker_server(
    config: BrokerProcessConfig,
) -> UnixBrokerServer | MtlsBrokerServer:
    """Construct the complete runtime, inventory, service, and selected transport."""

    if type(config) is not BrokerProcessConfig:
        _configuration_failure()
    if os.geteuid() <= 0:
        _configuration_failure()
    for command in (_PODMAN, _SYSTEMCTL):
        if not command.is_file() or not os.access(command, os.X_OK):
            _configuration_failure()
    mtls_server_context = _prepared_mtls_server_context(config)
    inventory_path = config.state_directory / _INVENTORY_NAME
    _inventory_files(inventory_path)
    os.umask(0o077)
    authority_lock = _acquire_authority_lock()
    authentication_key = config.authentication_key
    try:
        if type(authentication_key) is not bytes:
            _configuration_failure()
        inventory = SQLiteBrokerInventory(
            inventory_path, authentication_key, max_records=config.max_units
        )
        _inventory_files(inventory_path)
        podman_environment, systemd_environment = _runtime_environment()
        runtime = PodmanIsolationRuntime(
            image_repository=config.image_repository,
            run_as_uid=os.geteuid(),
            command=BoundedCommandRunner(
                _PODMAN, config.podman_limits, environment=podman_environment
            ),
            cgroup_root=Path(
                f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
                f"user@{os.geteuid()}.service"
            ),
            hooks_directory=config.hooks_directory,
            cgroup_remove=SystemdCgroupRemover(
                BoundedCommandRunner(
                    _SYSTEMCTL, config.podman_limits, environment=systemd_environment
                )
            ),
        )
        service = IsolationBrokerService(
            inventory,
            runtime,
            config.policy,
            max_discovered_units=config.max_units,
        )
        dispatcher = BrokerDispatcher(service)
        if config.transport_kind == _UNIX_TRANSPORT:
            if (
                config.socket_path is None
                or type(config.transport_limits) is not UnixTransportLimits
            ):
                _configuration_failure()
            server: UnixBrokerServer | MtlsBrokerServer = UnixBrokerServer(
                config.socket_path,
                expected_client_uid=os.geteuid(),
                principal=config.principal,
                dispatcher=dispatcher,
                limits=config.transport_limits,
                workspace_limits=config.policy.channel_limits,
            )
        elif config.transport_kind == _MTLS_TRANSPORT:
            if (
                config.socket_path is not None
                or type(config.transport_limits) is not MtlsTransportLimits
                or config.mtls_endpoint is None
                or config.mtls_endpoint.port == 0
                or config.mtls_local_identity is None
                or config.mtls_client_identity is None
                or mtls_server_context is None
            ):
                _configuration_failure()
            server = MtlsBrokerServer(
                config.mtls_endpoint,
                local_identity=config.mtls_local_identity,
                client_identity=config.mtls_client_identity,
                dispatcher=dispatcher,
                limits=config.transport_limits,
                workspace_limits=config.policy.channel_limits,
                server_context=mtls_server_context,
            )
        else:
            _configuration_failure()
        server._adopt_authority_lock(authority_lock)
        return server
    except BaseException:
        os.close(authority_lock)
        raise


class BrokerProcess:
    """Own signal-driven serving and an independent hard shutdown deadline."""

    def __init__(
        self,
        server: UnixBrokerServer | MtlsBrokerServer,
        *,
        hard_shutdown_timeout_seconds: float,
        hard_exit: Callable[[int], Never] = os._exit,
    ) -> None:
        if (
            not isinstance(server, (UnixBrokerServer, MtlsBrokerServer))
            or type(hard_shutdown_timeout_seconds) not in {int, float}
            or hard_shutdown_timeout_seconds <= 0
            or not math.isfinite(hard_shutdown_timeout_seconds)
            or not callable(hard_exit)
        ):
            raise ValueError("Broker process is invalid")
        self._server = server
        self._hard_timeout = float(hard_shutdown_timeout_seconds)
        self._hard_exit = hard_exit
        self._shutdown_complete = Event()
        self._shutdown_requested = Event()
        self._watchdog: Thread | None = None
        self._signal_count = 0

    def run(self) -> int:
        """Reconcile, listen, and stop with content-free terminal status."""

        shutdown_proven = False
        previous = {
            number: signal.signal(number, self._handle_signal)
            for number in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            self._server.start()
            if self._shutdown_requested.is_set():
                self._server.request_stop()
            self._server.wait_stopping()
            failed = self._server.failed
            self._arm_watchdog()
            try:
                self._server.stop()
            except BaseException:
                failed = True
            else:
                shutdown_proven = True
            return 1 if failed else 0
        finally:
            if shutdown_proven:
                self._shutdown_complete.set()
                if self._watchdog is not None:
                    self._watchdog.join()
            for number, handler in previous.items():
                signal.signal(number, handler)

    def _handle_signal(self, _number: int, _frame: object) -> None:
        self._signal_count += 1
        if self._signal_count == 1:
            self._shutdown_requested.set()
            self._arm_watchdog()
            self._server.request_stop()
            return
        self._hard_exit(1)

    def _arm_watchdog(self) -> None:
        if self._watchdog is not None:
            return
        self._watchdog = Thread(
            target=self._watch_shutdown, name="broker-hard-watchdog"
        )
        self._watchdog.start()

    def _watch_shutdown(self) -> None:
        if not self._shutdown_complete.wait(self._hard_timeout):
            self._hard_exit(1)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the configured broker without exposing configuration detail."""

    arguments = tuple(argv if argv is not None else sys.argv[1:])
    if len(arguments) != 1:
        os.write(2, b"broker configuration failed\n")
        return 2
    try:
        config = load_broker_process_config(Path(arguments[0]))
        server = build_broker_server(config)
        return BrokerProcess(
            server,
            hard_shutdown_timeout_seconds=config.hard_shutdown_timeout_seconds,
        ).run()
    except BrokerProcessConfigurationError:
        os.write(2, b"broker configuration failed\n")
        return 2
    except BaseException:
        os.write(2, b"broker runtime failed\n")
        return 1


if __name__ == "__main__":  # pragma: no cover - console script boundary
    raise SystemExit(main())
