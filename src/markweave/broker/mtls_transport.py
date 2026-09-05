"""Bounded paired-channel mTLS transport for the isolation broker."""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import secrets
import socket
import ssl
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal, RuntimeChannelLimits
from markweave.broker.protocol import (
    LENGTH_PREFIX_BYTES,
    BrokerRequest,
    BrokerResponse,
    decode_length_prefix,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)
from markweave.broker.unix_transport import (
    _validate_response_binding,
    _validate_workspace_response_binding,
)
from markweave.broker.workspace_protocol import (
    WORKSPACE_PROTOCOL_NAME,
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceRequestHeader,
    WorkspaceResponse,
    WorkspaceStageHeader,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    bind_workspace_result,
    bind_workspace_source,
    decode_workspace_request_header,
    decode_workspace_response_header,
    encode_workspace_request,
    encode_workspace_response,
    frame_protocol,
)

MTLS_PROTOCOL_NAME: Final = "markweave-reverse-broker-mtls"
MTLS_PROTOCOL_VERSION: Final = 1
MTLS_ALPN: Final = "markweave-reverse-broker-mtls/1"
_CONTROL_PAYLOAD_MAX: Final = 4096
_LIFECYCLE_FRAME_MAX: Final = LENGTH_PREFIX_BYTES + 4096
_DIGEST_PREFIX: Final = "sha256:"
_PIN_LENGTH: Final = len(_DIGEST_PREFIX) + 64
_EXCHANGE_HEX_LENGTH: Final = 64
_IPV4_VERSION: Final = 4
_MAX_PORT: Final = 65535
_MAX_PINS: Final = 2
_MAX_URI_LENGTH: Final = 255
_SERVER_CONTEXT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class MtlsEndpoint:
    """Explicit canonical IPv4 endpoint for one inert mTLS boundary."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if type(self.host) is not str:
            raise ValueError("Broker mTLS endpoint is invalid")
        try:
            address = ipaddress.ip_address(self.host)
        except (TypeError, ValueError) as error:
            raise ValueError("Broker mTLS endpoint is invalid") from error
        if (
            type(self.host) is not str
            or address.version != _IPV4_VERSION
            or str(address) != self.host
            or type(self.port) is not int
            or not 0 <= self.port <= _MAX_PORT
        ):
            raise ValueError("Broker mTLS endpoint is invalid")


@dataclass(frozen=True, slots=True)
class MtlsTransportLimits:
    """Deployment-supplied mTLS bounds, with no production defaults."""

    operation_timeout_seconds: float
    shutdown_timeout_seconds: float
    max_handshakes: int
    max_pending_exchanges: int
    max_handlers: int
    listen_backlog: int

    def __post_init__(self) -> None:
        numbers = (self.operation_timeout_seconds, self.shutdown_timeout_seconds)
        counts = (
            self.max_handshakes,
            self.max_pending_exchanges,
            self.max_handlers,
            self.listen_backlog,
        )
        if any(
            type(value) not in {int, float} or value <= 0 or not math.isfinite(value)
            for value in numbers
        ) or any(type(value) is not int or value <= 0 for value in counts):
            raise ValueError("Broker mTLS transport limits are invalid")


@dataclass(frozen=True, slots=True)
class MtlsLocalIdentity:
    """Required local certificate material and its stable protocol principal."""

    ca_certificate: Path
    certificate_chain: Path
    private_key: Path
    uri_san: str
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(path, Path) and path.is_absolute()
                for path in (
                    self.ca_certificate,
                    self.certificate_chain,
                    self.private_key,
                )
            )
            or not _valid_uri_san(self.uri_san)
            or type(self.principal) is not AuthenticatedPrincipal
        ):
            raise ValueError("Broker mTLS local identity is invalid")


@dataclass(frozen=True, slots=True)
class MtlsPeerIdentity:
    """Exact peer role and current/next leaf-certificate pins."""

    uri_san: str
    leaf_certificate_sha256: tuple[str, ...]
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        pins = self.leaf_certificate_sha256
        if (
            not _valid_uri_san(self.uri_san)
            or type(pins) is not tuple
            or not 1 <= len(pins) <= _MAX_PINS
            or len(set(pins)) != len(pins)
            or any(not _valid_digest(pin) for pin in pins)
            or type(self.principal) is not AuthenticatedPrincipal
        ):
            raise ValueError("Broker mTLS peer identity is invalid")


class MtlsServerContext:
    """Opaque, fully loaded server TLS context safe to retain after FD closure."""

    __slots__ = ("_context", "_local_identity")

    def __init__(
        self, context: ssl.SSLContext, local_identity: MtlsLocalIdentity, token: object
    ) -> None:
        if token is not _SERVER_CONTEXT_TOKEN:
            raise ValueError("Broker mTLS server context is invalid")
        self._context = context
        self._local_identity = local_identity


@dataclass(slots=True)
class _Reservation:
    request_id: UUID
    principal: AuthenticatedPrincipal
    leaf_certificate_sha256: str
    deadline: float
    response_ready: Event
    response_frame: bytes | None = None
    claimed: bool = False


def _valid_uri_san(value: object) -> bool:
    if type(value) is not str or not value.isascii() or len(value) > _MAX_URI_LENGTH:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "spiffe"
            and bool(parsed.hostname)
            and parsed.netloc == parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and parsed.path.startswith("/")
            and parsed.path != "/"
            and "//" not in parsed.path
            and all(
                character.isalnum() or character in "/._-" for character in parsed.path
            )
            and not parsed.query
            and not parsed.fragment
            and urlunsplit(parsed) == value
        )
    except ValueError:
        return False


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _PIN_LENGTH
        and value.startswith(_DIGEST_PREFIX)
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def leaf_certificate_sha256(certificate_der: bytes) -> str:
    """Return the canonical exact-leaf certificate pin."""

    if type(certificate_der) is not bytes or not certificate_der:
        raise ValueError("Broker mTLS leaf certificate is invalid")
    return _sha256(certificate_der)


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    if not encoded or len(encoded) > _CONTROL_PAYLOAD_MAX:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return encoded


def _control_frame(value: dict[str, object]) -> bytes:
    payload = _canonical_json(value)
    return len(payload).to_bytes(LENGTH_PREFIX_BYTES, "big") + payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _decode_control(
    frame: bytes, expected: set[str] | tuple[set[str], ...]
) -> dict[str, object]:
    if type(frame) is not bytes or len(frame) < LENGTH_PREFIX_BYTES:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    length = int.from_bytes(frame[:LENGTH_PREFIX_BYTES], "big")
    payload = frame[LENGTH_PREFIX_BYTES:]
    if length != len(payload) or not 0 < length <= _CONTROL_PAYLOAD_MAX:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    expected_schemas = (expected,) if type(expected) is set else expected
    if (
        type(value) is not dict
        or not any(set(value) == schema for schema in expected_schemas)
        or value.get("protocol") != MTLS_PROTOCOL_NAME
        or value.get("version") != MTLS_PROTOCOL_VERSION
        or _canonical_json(value) != payload
    ):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _exchange_id(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != _EXCHANGE_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    if str(parsed) != value:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return parsed


def _positive_length(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _receive_exact(connection: ssl.SSLSocket, size: int, deadline: float) -> bytes:
    received = bytearray()
    while len(received) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(received))
        if not chunk:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        received.extend(chunk)
    return bytes(received)


def _receive_control(connection: ssl.SSLSocket, deadline: float) -> bytes:
    prefix = _receive_exact(connection, LENGTH_PREFIX_BYTES, deadline)
    length = int.from_bytes(prefix, "big")
    if not 0 < length <= _CONTROL_PAYLOAD_MAX:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return prefix + _receive_exact(connection, length, deadline)


def _send_all(connection: ssl.SSLSocket, payload: bytes, deadline: float) -> None:
    view = memoryview(payload)
    while view:
        connection.settimeout(_remaining(deadline))
        written = connection.send(view)
        if written <= 0:
            raise OSError("mTLS send made no progress")
        view = view[written:]


def _authenticated_eof(connection: ssl.SSLSocket, deadline: float) -> None:
    connection.settimeout(_remaining(deadline))
    if connection.recv(1) != b"":
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    connection.settimeout(_remaining(deadline))
    underlying = connection.unwrap()
    underlying.close()


def _full_tls_close(connection: ssl.SSLSocket, deadline: float) -> None:
    connection.settimeout(_remaining(deadline))
    underlying = connection.unwrap()
    underlying.close()


def _tls_context(local: MtlsLocalIdentity, *, server: bool) -> ssl.SSLContext:
    protocol = ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    context = ssl.SSLContext(protocol)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    if not server:
        context.check_hostname = False
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
    if server:
        context.num_tickets = 0
    try:
        context.load_verify_locations(cafile=str(local.ca_certificate))
        context.load_cert_chain(
            certfile=str(local.certificate_chain), keyfile=str(local.private_key)
        )
        context.set_alpn_protocols([MTLS_ALPN])
    except (OSError, ssl.SSLError, ValueError) as error:
        raise ValueError("Broker mTLS certificate material is invalid") from error
    return context


def build_mtls_server_context(
    local: MtlsLocalIdentity,
    *,
    declared_identity: MtlsLocalIdentity | None = None,
) -> MtlsServerContext:
    """Load one exact server identity into an opaque, memory-resident context."""

    binding = local if declared_identity is None else declared_identity
    if (
        type(local) is not MtlsLocalIdentity
        or type(binding) is not MtlsLocalIdentity
        or local.uri_san != binding.uri_san
        or local.principal != binding.principal
    ):
        raise ValueError("Broker mTLS local identity is invalid")
    if local != binding:
        try:
            for loaded_path, declared_path in zip(
                (
                    local.ca_certificate,
                    local.certificate_chain,
                    local.private_key,
                ),
                (
                    binding.ca_certificate,
                    binding.certificate_chain,
                    binding.private_key,
                ),
                strict=True,
            ):
                loaded = os.stat(loaded_path)
                declared = os.stat(declared_path, follow_symlinks=False)
                if (loaded.st_dev, loaded.st_ino) != (
                    declared.st_dev,
                    declared.st_ino,
                ):
                    raise ValueError("Broker mTLS local identity is invalid")
        except OSError as error:
            raise ValueError("Broker mTLS local identity is invalid") from error
    return MtlsServerContext(
        _tls_context(local, server=True), binding, _SERVER_CONTEXT_TOKEN
    )


def _authenticate_peer(
    connection: ssl.SSLSocket, expected: MtlsPeerIdentity
) -> tuple[AuthenticatedPrincipal, str]:
    if (
        connection.version() != "TLSv1.3"
        or connection.selected_alpn_protocol() != MTLS_ALPN
    ):
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    certificate = connection.getpeercert()
    certificate_der = connection.getpeercert(binary_form=True)
    if (
        type(certificate) is not dict
        or certificate.get("subjectAltName") != (("URI", expected.uri_san),)
        or type(certificate_der) is not bytes
    ):
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    digest = leaf_certificate_sha256(certificate_der)
    if digest not in expected.leaf_certificate_sha256:
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    return expected.principal, digest


def _request_frame_max(workspace_limits: RuntimeChannelLimits | None) -> int:
    if workspace_limits is None:
        return _LIFECYCLE_FRAME_MAX
    return max(
        _LIFECYCLE_FRAME_MAX,
        LENGTH_PREFIX_BYTES + 4096 + workspace_limits.max_input_bytes,
    )


def _response_frame_max(workspace_limits: RuntimeChannelLimits | None) -> int:
    if workspace_limits is None:
        return _LIFECYCLE_FRAME_MAX
    return max(
        _LIFECYCLE_FRAME_MAX,
        LENGTH_PREFIX_BYTES + 4096 + workspace_limits.max_output_bytes,
    )


def _decode_existing_request(
    frame: bytes, workspace_limits: RuntimeChannelLimits | None
) -> BrokerRequest | WorkspaceStageRequest | WorkspaceCollectRequest:
    if len(frame) < LENGTH_PREFIX_BYTES:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    header_length = decode_length_prefix(frame[:LENGTH_PREFIX_BYTES])
    header_end = LENGTH_PREFIX_BYTES + header_length
    if header_end > len(frame):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    header = frame[:header_end]
    if frame_protocol(header) != WORKSPACE_PROTOCOL_NAME:
        return decode_request(frame)
    if workspace_limits is None:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    request: WorkspaceRequestHeader = decode_workspace_request_header(
        header, workspace_limits
    )
    payload = frame[header_end:]
    if type(request) is WorkspaceStageHeader:
        return bind_workspace_source(request, payload)
    if type(request) is not WorkspaceCollectRequest or payload:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return request


def _decode_existing_response(
    frame: bytes,
    request: BrokerRequest | WorkspaceStageRequest | WorkspaceCollectRequest,
    workspace_limits: RuntimeChannelLimits | None,
    principal: AuthenticatedPrincipal,
) -> BrokerResponse | WorkspaceResponse:
    if isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
        if workspace_limits is None or len(frame) < LENGTH_PREFIX_BYTES:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        header_length = decode_length_prefix(frame[:LENGTH_PREFIX_BYTES])
        header_end = LENGTH_PREFIX_BYTES + header_length
        if header_end > len(frame):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        response, length, digest = decode_workspace_response_header(
            frame[:header_end], workspace_limits
        )
        if len(frame) != header_end + length:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        bound = bind_workspace_result(response, frame[header_end:], digest)
        _validate_workspace_response_binding(request, bound)
        return bound
    response = decode_response(frame)
    _validate_response_binding(request, response, principal)
    return response


def _reservation_mapping(request_id: UUID) -> dict[str, object]:
    return {
        "channel": "response",
        "operation": "RESERVE",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _submit_mapping(exchange: str, request_id: UUID, frame: bytes) -> dict[str, object]:
    return {
        "channel": "request",
        "exchange_id": exchange,
        "frame_length": len(frame),
        "frame_sha256": _sha256(frame),
        "operation": "SUBMIT",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _ack_mapping(exchange: str, request_id: UUID) -> dict[str, object]:
    return {
        "exchange_id": exchange,
        "operation": "RESERVED",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _response_mapping(
    exchange: str, request_id: UUID, frame: bytes
) -> dict[str, object]:
    return {
        "exchange_id": exchange,
        "frame_length": len(frame),
        "frame_sha256": _sha256(frame),
        "operation": "RESPONSE",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


_RESERVATION_KEYS = {
    "channel",
    "operation",
    "protocol",
    "request_id",
    "version",
}
_SUBMIT_KEYS = _RESERVATION_KEYS | {
    "exchange_id",
    "frame_length",
    "frame_sha256",
}
_ACK_KEYS = {"exchange_id", "operation", "protocol", "request_id", "version"}
_RESPONSE_KEYS = _ACK_KEYS | {"frame_length", "frame_sha256"}


class MtlsBrokerServer:
    """Serve bounded requests over paired, mutually authenticated TLS channels."""

    def __init__(  # noqa: PLR0913
        self,
        endpoint: MtlsEndpoint,
        *,
        local_identity: MtlsLocalIdentity,
        client_identity: MtlsPeerIdentity,
        dispatcher: BrokerDispatcher,
        limits: MtlsTransportLimits,
        workspace_limits: RuntimeChannelLimits | None = None,
        server_context: MtlsServerContext | None = None,
    ) -> None:
        if (
            type(endpoint) is not MtlsEndpoint
            or type(local_identity) is not MtlsLocalIdentity
            or type(client_identity) is not MtlsPeerIdentity
            or not isinstance(dispatcher, BrokerDispatcher)
            or type(limits) is not MtlsTransportLimits
            or (
                workspace_limits is not None
                and type(workspace_limits) is not RuntimeChannelLimits
            )
            or (
                server_context is not None
                and (
                    type(server_context) is not MtlsServerContext
                    or server_context._local_identity != local_identity
                )
            )
        ):
            raise ValueError("Broker mTLS server configuration is invalid")
        self._endpoint = endpoint
        self._local_identity = local_identity
        self._client_identity = client_identity
        self._dispatcher = dispatcher
        self._limits = limits
        self._workspace_limits = workspace_limits
        self._context = (
            _tls_context(local_identity, server=True)
            if server_context is None
            else server_context._context
        )
        self._listener: socket.socket | None = None
        self._accept_thread: Thread | None = None
        self._stopping = Event()
        self._handshakes = BoundedSemaphore(limits.max_handshakes)
        self._exchanges = BoundedSemaphore(limits.max_pending_exchanges)
        self._handlers = BoundedSemaphore(limits.max_handlers)
        self._connections = BoundedSemaphore(
            limits.max_handshakes + limits.max_pending_exchanges + limits.max_handlers
        )
        self._dispatch_gate = Lock()
        self._state_lock = Lock()
        self._threads: set[Thread] = set()
        self._sockets: set[socket.socket] = set()
        self._reservations: dict[str, _Reservation] = {}
        self._fatal_error: BaseException | None = None
        self._authority_lock_fd: int | None = None

    @property
    def endpoint(self) -> MtlsEndpoint:
        """Return the actual bound endpoint after startup."""

        listener = self._listener
        if listener is None:
            return self._endpoint
        host, port = listener.getsockname()
        return MtlsEndpoint(host, port)

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def failed(self) -> bool:
        with self._state_lock:
            return self._fatal_error is not None

    def wait_stopping(self, timeout: float | None = None) -> bool:
        if timeout is not None and (
            type(timeout) not in {int, float}
            or timeout < 0
            or not math.isfinite(timeout)
        ):
            raise ValueError("Broker mTLS wait timeout is invalid")
        return self._stopping.wait(timeout)

    def request_stop(self) -> None:
        self._stopping.set()
        listener = self._listener
        if listener is not None:
            listener.close()

    def _adopt_authority_lock(self, descriptor: int) -> None:
        if (
            type(descriptor) is not int
            or descriptor < 0
            or self._authority_lock_fd is not None
        ):
            raise ValueError("Broker authority lock is invalid")
        self._authority_lock_fd = descriptor

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("Broker mTLS server is already running")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self._endpoint.host, self._endpoint.port))
            self._dispatcher.start()
            if self._stopping.is_set():
                listener.close()
                self._release_authority_lock()
                return
            listener.listen(self._limits.listen_backlog)
            listener.settimeout(min(self._limits.operation_timeout_seconds, 0.25))
            self._listener = listener
            thread = Thread(target=self._accept_loop, name="broker-mtls-accept")
            self._accept_thread = thread
            thread.start()
        except BaseException:
            listener.close()
            self._listener = None
            self._accept_thread = None
            self._release_authority_lock()
            raise

    def stop(self) -> None:
        self._stopping.set()
        listener = self._listener
        if listener is None:
            self._release_authority_lock()
            return
        listener.close()
        deadline = monotonic() + self._limits.shutdown_timeout_seconds
        accept_thread = self._accept_thread
        if accept_thread is not None:
            accept_thread.join(max(0.0, deadline - monotonic()))
        with self._state_lock:
            sockets = tuple(self._sockets)
            reservations = tuple(self._reservations.values())
        for reservation in reservations:
            reservation.response_ready.set()
        for connection in sockets:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        while True:
            with self._state_lock:
                threads = tuple(self._threads)
            if not threads:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            threads[0].join(remaining)
        with self._state_lock:
            undrained = bool(self._threads) or bool(
                accept_thread is not None and accept_thread.is_alive()
            )
            fatal = self._fatal_error
            if not undrained:
                self._reservations.clear()
                self._fatal_error = None
        if undrained:
            raise RuntimeError("Broker mTLS handlers did not drain")
        self._listener = None
        self._accept_thread = None
        self._release_authority_lock()
        if fatal is not None:
            raise RuntimeError("Broker mTLS server failed") from fatal

    def _release_authority_lock(self) -> None:
        descriptor = self._authority_lock_fd
        if descriptor is None:
            return
        self._authority_lock_fd = None
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(descriptor)

    def __enter__(self) -> MtlsBrokerServer:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()

    def _accept_loop(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._stopping.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError as error:
                if not self._stopping.is_set():
                    self._record_fatal(error)
                return
            if not self._connections.acquire(blocking=False):
                connection.close()
                continue
            thread = Thread(
                target=self._handle_and_release,
                args=(connection,),
                name="broker-mtls-handler",
            )
            with self._state_lock:
                self._threads.add(thread)
                self._sockets.add(connection)
            try:
                thread.start()
            except BaseException as error:
                with self._state_lock:
                    self._threads.discard(thread)
                    self._sockets.discard(connection)
                connection.close()
                self._connections.release()
                self._record_fatal(error)
                return

    def _handle_and_release(self, raw: socket.socket) -> None:
        current = current_thread()
        connection: ssl.SSLSocket | None = None
        try:
            if not self._handshakes.acquire(blocking=False):
                return
            try:
                deadline = monotonic() + self._limits.operation_timeout_seconds
                raw.settimeout(_remaining(deadline))
                connection = self._context.wrap_socket(
                    raw, server_side=True, suppress_ragged_eofs=False
                )
                with self._state_lock:
                    self._sockets.discard(raw)
                    self._sockets.add(connection)
                principal, digest = _authenticate_peer(
                    connection, self._client_identity
                )
                control = _receive_control(connection, deadline)
            finally:
                self._handshakes.release()
            value = _decode_control(control, (_RESERVATION_KEYS, _SUBMIT_KEYS))
            if value.get("operation") == "RESERVE":
                self._handle_response_channel(
                    connection, value, principal, digest, deadline
                )
            elif value.get("operation") == "SUBMIT":
                self._handle_request_channel(
                    connection, value, principal, digest, deadline
                )
            else:
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        except BrokerError, OSError, TimeoutError, ssl.SSLError, ValueError:
            return
        except BaseException as error:
            self._record_fatal(error)
        finally:
            if connection is not None:
                connection.close()
            else:
                raw.close()
            self._connections.release()
            with self._state_lock:
                self._threads.discard(current)
                self._sockets.discard(raw)
                if connection is not None:
                    self._sockets.discard(connection)

    def _handle_response_channel(
        self,
        connection: ssl.SSLSocket,
        value: dict[str, object],
        principal: AuthenticatedPrincipal,
        digest: str,
        deadline: float,
    ) -> None:
        if value.get("channel") != "response":
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        request_id = _uuid(value.get("request_id"))
        if not self._exchanges.acquire(blocking=False):
            return
        exchange = secrets.token_hex(32)
        reservation = _Reservation(request_id, principal, digest, deadline, Event())
        inserted = False
        try:
            with self._state_lock:
                if exchange in self._reservations or self._stopping.is_set():
                    return
                self._reservations[exchange] = reservation
                inserted = True
            _send_all(
                connection, _control_frame(_ack_mapping(exchange, request_id)), deadline
            )
            if not reservation.response_ready.wait(_remaining(deadline)):
                return
            frame = reservation.response_frame
            if frame is None or self._stopping.is_set():
                return
            response_control = _control_frame(
                _response_mapping(exchange, request_id, frame)
            )
            _send_all(connection, response_control + frame, deadline)
            _full_tls_close(connection, deadline)
        finally:
            if inserted:
                with self._state_lock:
                    if self._reservations.get(exchange) is reservation:
                        self._reservations.pop(exchange, None)
            self._exchanges.release()

    def _handle_request_channel(
        self,
        connection: ssl.SSLSocket,
        value: dict[str, object],
        principal: AuthenticatedPrincipal,
        digest: str,
        deadline: float,
    ) -> None:
        if value.get("channel") != "request":
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        exchange = _exchange_id(value.get("exchange_id"))
        request_id = _uuid(value.get("request_id"))
        length = _positive_length(
            value.get("frame_length"), _request_frame_max(self._workspace_limits)
        )
        expected_digest = value.get("frame_sha256")
        if not _valid_digest(expected_digest) or not self._handlers.acquire(
            blocking=False
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        try:
            with self._state_lock:
                expected_reservation = self._reservations.get(exchange)
                if (
                    expected_reservation is None
                    or expected_reservation.claimed
                    or expected_reservation.request_id != request_id
                    or expected_reservation.principal != principal
                    or expected_reservation.leaf_certificate_sha256 != digest
                    or self._stopping.is_set()
                ):
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                deadline = min(deadline, expected_reservation.deadline)
            frame = _receive_exact(connection, length, deadline)
            if _sha256(frame) != expected_digest:
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            _authenticated_eof(connection, deadline)
            request = _decode_existing_request(frame, self._workspace_limits)
            if request.request_id != request_id:
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            with self._state_lock:
                reservation = self._reservations.get(exchange)
                if (
                    reservation is not expected_reservation
                    or reservation.claimed
                    or reservation.deadline <= monotonic()
                    or self._stopping.is_set()
                ):
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                reservation.claimed = True
            response = self._dispatch(request, principal, deadline)
            if response is None:
                return
            try:
                if isinstance(
                    request, (WorkspaceStageRequest, WorkspaceCollectRequest)
                ):
                    response_frame = encode_workspace_response(
                        cast(WorkspaceResponse, response),
                        cast(RuntimeChannelLimits, self._workspace_limits),
                    )
                else:
                    response_frame = encode_response(cast(BrokerResponse, response))
                if len(response_frame) > _response_frame_max(self._workspace_limits):
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            except BaseException as error:
                self._record_fatal(error)
                return
            reservation.response_frame = response_frame
            reservation.response_ready.set()
        finally:
            self._handlers.release()

    def _dispatch(
        self,
        request: BrokerRequest | WorkspaceStageRequest | WorkspaceCollectRequest,
        principal: AuthenticatedPrincipal,
        deadline: float,
    ) -> BrokerResponse | WorkspaceResponse | None:
        if not self._dispatch_gate.acquire(timeout=_remaining(deadline)):
            self._record_fatal(TimeoutError("Broker mTLS dispatch gate expired"))
            return None
        completed = Event()
        watchdog = Thread(
            target=self._watch_dispatch,
            args=(completed, deadline),
            name="broker-mtls-watchdog",
        )
        watchdog.start()
        try:
            if isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
                return self._dispatcher.dispatch_workspace(principal, request)
            return self._dispatcher.dispatch(principal, request)
        except BaseException as error:
            self._record_fatal(error)
            return None
        finally:
            completed.set()
            watchdog.join()
            self._dispatch_gate.release()

    def _watch_dispatch(self, completed: Event, deadline: float) -> None:
        try:
            remaining = _remaining(deadline)
        except TimeoutError as error:
            self._record_fatal(error)
            return
        if not completed.wait(remaining):
            self._record_fatal(TimeoutError("Broker mTLS dispatch deadline expired"))

    def _record_fatal(self, error: BaseException) -> None:
        with self._state_lock:
            if self._fatal_error is None:
                self._fatal_error = error
        self.request_stop()


class MtlsBrokerClient:
    """Perform one exchange over paired, mutually authenticated TLS channels."""

    def __init__(
        self,
        endpoint: MtlsEndpoint,
        *,
        local_identity: MtlsLocalIdentity,
        server_identity: MtlsPeerIdentity,
        operation_timeout_seconds: float,
        workspace_limits: RuntimeChannelLimits | None = None,
    ) -> None:
        if (
            type(endpoint) is not MtlsEndpoint
            or type(local_identity) is not MtlsLocalIdentity
            or type(server_identity) is not MtlsPeerIdentity
            or type(operation_timeout_seconds) not in {int, float}
            or operation_timeout_seconds <= 0
            or not math.isfinite(operation_timeout_seconds)
            or (
                workspace_limits is not None
                and type(workspace_limits) is not RuntimeChannelLimits
            )
        ):
            raise ValueError("Broker mTLS client configuration is invalid")
        self._endpoint = endpoint
        self._local_identity = local_identity
        self._server_identity = server_identity
        self._operation_timeout_seconds = float(operation_timeout_seconds)
        self._workspace_limits = workspace_limits
        self._context = _tls_context(local_identity, server=False)

    def request(self, request: BrokerRequest) -> BrokerResponse:
        return cast(BrokerResponse, self._exchange(request))

    def stage_workspace(
        self, request: WorkspaceStageRequest
    ) -> WorkspaceStageReceipt | WorkspaceErrorResponse:
        response = cast(WorkspaceResponse, self._exchange(request))
        if type(response) not in {WorkspaceStageReceipt, WorkspaceErrorResponse}:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return cast(WorkspaceStageReceipt | WorkspaceErrorResponse, response)

    def collect_workspace(self, request: WorkspaceCollectRequest) -> WorkspaceResponse:
        return cast(WorkspaceResponse, self._exchange(request))

    def _connect(
        self, deadline: float
    ) -> tuple[ssl.SSLSocket, AuthenticatedPrincipal, str]:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection: ssl.SSLSocket | None = None
        try:
            raw.settimeout(_remaining(deadline))
            raw.connect((self._endpoint.host, self._endpoint.port))
            raw.settimeout(_remaining(deadline))
            connection = self._context.wrap_socket(
                raw,
                server_hostname=None,
                suppress_ragged_eofs=False,
            )
            principal, digest = _authenticate_peer(connection, self._server_identity)
            return connection, principal, digest
        except BaseException:
            if connection is not None:
                connection.close()
            else:
                raw.close()
            raise

    def _exchange(  # noqa: PLR0912, PLR0915
        self, request: BrokerRequest | WorkspaceStageRequest | WorkspaceCollectRequest
    ) -> BrokerResponse | WorkspaceResponse:
        deadline = monotonic() + self._operation_timeout_seconds
        try:
            if isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
                limits = self._workspace_limits
                if limits is None:
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                frame = encode_workspace_request(request)
            else:
                frame = encode_request(request)
            if len(frame) > _request_frame_max(self._workspace_limits):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            _remaining(deadline)
        except BrokerError:
            raise
        except (OSError, TimeoutError, ssl.SSLError) as error:
            raise BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE) from error
        response_connection: ssl.SSLSocket | None = None
        request_connection: ssl.SSLSocket | None = None
        try:
            response_connection, server_principal, response_pin = self._connect(
                deadline
            )
            _send_all(
                response_connection,
                _control_frame(_reservation_mapping(request.request_id)),
                deadline,
            )
            acknowledgement = _decode_control(
                _receive_control(response_connection, deadline), _ACK_KEYS
            )
            exchange = _exchange_id(acknowledgement.get("exchange_id"))
            if acknowledgement != _ack_mapping(exchange, request.request_id):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            request_connection, request_server_principal, request_pin = self._connect(
                deadline
            )
            if (request_server_principal, request_pin) != (
                server_principal,
                response_pin,
            ):
                raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
            _send_all(
                request_connection,
                _control_frame(_submit_mapping(exchange, request.request_id, frame))
                + frame,
                deadline,
            )
            _full_tls_close(request_connection, deadline)
            request_connection = None
            response_header = _decode_control(
                _receive_control(response_connection, deadline), _RESPONSE_KEYS
            )
            length = _positive_length(
                response_header.get("frame_length"),
                _response_frame_max(self._workspace_limits),
            )
            if (
                response_header.get("operation") != "RESPONSE"
                or _exchange_id(response_header.get("exchange_id")) != exchange
                or _uuid(response_header.get("request_id")) != request.request_id
                or not _valid_digest(response_header.get("frame_sha256"))
            ):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            response_frame = _receive_exact(response_connection, length, deadline)
            if _sha256(response_frame) != response_header.get("frame_sha256"):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            _full_tls_close(response_connection, deadline)
            response_connection = None
            return _decode_existing_response(
                response_frame,
                request,
                self._workspace_limits,
                self._local_identity.principal,
            )
        except BrokerError:
            raise
        except (OSError, TimeoutError, ssl.SSLError) as error:
            raise BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE) from error
        finally:
            if request_connection is not None:
                request_connection.close()
            if response_connection is not None:
                response_connection.close()
