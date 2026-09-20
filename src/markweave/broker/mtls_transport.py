"""Bounded paired-channel mTLS transport for the isolation broker."""

from __future__ import annotations

import fcntl
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
from uuid import UUID

import markweave.broker.mtls_control as _mtls_control
import markweave.broker.mtls_identity as _mtls_identity
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
from markweave.broker.reconciliation_protocol import (
    PROTOCOL_NAME as RECONCILIATION_PROTOCOL_NAME,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationErrorResponse,
    ReconciliationRequest,
    ReconciliationResponse,
    ReconciliationResult,
)
from markweave.broker.reconciliation_protocol import (
    decode_request as decode_reconciliation_request,
)
from markweave.broker.reconciliation_protocol import (
    decode_response as decode_reconciliation_response,
)
from markweave.broker.reconciliation_protocol import (
    encode_request as encode_reconciliation_request,
)
from markweave.broker.reconciliation_protocol import (
    encode_response as encode_reconciliation_response,
)
from markweave.broker.response_binding import (
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

_DIGEST_PREFIX = _mtls_identity._DIGEST_PREFIX
_PIN_LENGTH = _mtls_identity._PIN_LENGTH
_sha256 = _mtls_identity._sha256
_valid_digest = _mtls_identity._valid_digest
MTLS_ALPN = _mtls_identity.MTLS_ALPN
_IPV4_VERSION = _mtls_identity._IPV4_VERSION
_MAX_PORT = _mtls_identity._MAX_PORT
_MAX_PINS = _mtls_identity._MAX_PINS
_MAX_URI_LENGTH = _mtls_identity._MAX_URI_LENGTH
_SERVER_CONTEXT_TOKEN = _mtls_identity._SERVER_CONTEXT_TOKEN
MtlsEndpoint = _mtls_identity.MtlsEndpoint
MtlsTransportLimits = _mtls_identity.MtlsTransportLimits
MtlsLocalIdentity = _mtls_identity.MtlsLocalIdentity
MtlsPeerIdentity = _mtls_identity.MtlsPeerIdentity
MtlsServerContext = _mtls_identity.MtlsServerContext
_valid_uri_san = _mtls_identity._valid_uri_san
leaf_certificate_sha256 = _mtls_identity.leaf_certificate_sha256
_tls_context = _mtls_identity._tls_context
_authenticate_peer = _mtls_identity._authenticate_peer

_ACK_KEYS = _mtls_control._ACK_KEYS

_CONTROL_PAYLOAD_MAX = _mtls_control._CONTROL_PAYLOAD_MAX

_EXCHANGE_HEX_LENGTH = _mtls_control._EXCHANGE_HEX_LENGTH

_RESERVATION_KEYS = _mtls_control._RESERVATION_KEYS

_RESPONSE_KEYS = _mtls_control._RESPONSE_KEYS

_SUBMIT_KEYS = _mtls_control._SUBMIT_KEYS

MTLS_PROTOCOL_NAME = _mtls_control.MTLS_PROTOCOL_NAME

MTLS_PROTOCOL_VERSION = _mtls_control.MTLS_PROTOCOL_VERSION

_ack_mapping = _mtls_control._ack_mapping

_canonical_json = _mtls_control._canonical_json

_control_frame = _mtls_control._control_frame

_decode_control = _mtls_control._decode_control

_exchange_id = _mtls_control._exchange_id

_positive_length = _mtls_control._positive_length

_reservation_mapping = _mtls_control._reservation_mapping

_response_mapping = _mtls_control._response_mapping

_submit_mapping = _mtls_control._submit_mapping

_unique_object = _mtls_control._unique_object

_uuid = _mtls_control._uuid

_LIFECYCLE_FRAME_MAX: Final = LENGTH_PREFIX_BYTES + 4096

_TLS_MATERIAL_COUNT: Final = 3

_TLS_MATERIAL_MAX_BYTES: Final = 65_536


@dataclass(slots=True)
class _Reservation:
    request_id: UUID
    principal: AuthenticatedPrincipal
    leaf_certificate_sha256: str
    deadline: float
    response_ready: Event
    response_frame: bytes | None = None
    claimed: bool = False


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


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


def build_mtls_server_context_from_material(
    local: MtlsLocalIdentity,
    material: tuple[bytes, bytes, bytes],
) -> MtlsServerContext:
    """Load one immutable material snapshot and bind it to its declared identity."""

    if (
        type(local) is not MtlsLocalIdentity
        or type(material) is not tuple
        or len(material) != _TLS_MATERIAL_COUNT
        or any(
            type(value) is not bytes or not 0 < len(value) <= _TLS_MATERIAL_MAX_BYTES
            for value in material
        )
    ):
        raise ValueError("Broker mTLS certificate material is invalid")
    descriptors: list[int] = []
    try:
        for value in material:
            descriptor = os.memfd_create("markweave-tls-material", os.MFD_CLOEXEC)
            descriptors.append(descriptor)
            offset = 0
            while offset < len(value):
                written = os.write(descriptor, value[offset:])
                if written <= 0:
                    raise ValueError("Broker mTLS certificate material is invalid")
                offset += written
            os.lseek(descriptor, 0, os.SEEK_SET)
        loaded = MtlsLocalIdentity(
            Path(f"/proc/self/fd/{descriptors[0]}"),
            Path(f"/proc/self/fd/{descriptors[1]}"),
            Path(f"/proc/self/fd/{descriptors[2]}"),
            local.uri_san,
            local.principal,
        )
        return MtlsServerContext(
            _tls_context(loaded, server=True), local, _SERVER_CONTEXT_TOKEN
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


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
) -> (
    BrokerRequest
    | WorkspaceStageRequest
    | WorkspaceCollectRequest
    | ReconciliationRequest
):
    if len(frame) < LENGTH_PREFIX_BYTES:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    header_length = decode_length_prefix(frame[:LENGTH_PREFIX_BYTES])
    header_end = LENGTH_PREFIX_BYTES + header_length
    if header_end > len(frame):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    header = frame[:header_end]
    protocol_name = frame_protocol(header)
    if protocol_name == RECONCILIATION_PROTOCOL_NAME:
        return decode_reconciliation_request(frame)
    if protocol_name != WORKSPACE_PROTOCOL_NAME:
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
    request: BrokerRequest
    | WorkspaceStageRequest
    | WorkspaceCollectRequest
    | ReconciliationRequest,
    workspace_limits: RuntimeChannelLimits | None,
    principal: AuthenticatedPrincipal,
) -> BrokerResponse | WorkspaceResponse | ReconciliationResult:
    if type(request) is ReconciliationRequest:
        response = decode_reconciliation_response(frame)
        if response.request_id != request.request_id:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        if type(response) is ReconciliationErrorResponse:
            return response
        success = cast(ReconciliationResponse, response)
        if (
            success.principal_id != principal.principal_id
            or success.after_create_sequence != request.after_create_sequence
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return success
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
    _validate_response_binding(cast(BrokerRequest, request), response, principal)
    return response


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

    def _handle_request_channel(  # noqa: PLR0912 - closed protocol multiplexing
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
                elif type(request) is ReconciliationRequest:
                    response_frame = encode_reconciliation_response(
                        cast(ReconciliationResult, response)
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
        request: BrokerRequest
        | WorkspaceStageRequest
        | WorkspaceCollectRequest
        | ReconciliationRequest,
        principal: AuthenticatedPrincipal,
        deadline: float,
    ) -> BrokerResponse | WorkspaceResponse | ReconciliationResult | None:
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
            if type(request) is ReconciliationRequest:
                return self._dispatcher.dispatch_reconciliation(principal, request)
            if isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
                return self._dispatcher.dispatch_workspace(principal, request)
            return self._dispatcher.dispatch(principal, cast(BrokerRequest, request))
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

    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult:
        return cast(ReconciliationResult, self._exchange(request))

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
        self,
        request: BrokerRequest
        | WorkspaceStageRequest
        | WorkspaceCollectRequest
        | ReconciliationRequest,
    ) -> BrokerResponse | WorkspaceResponse | ReconciliationResult:
        deadline = monotonic() + self._operation_timeout_seconds
        try:
            if type(request) is ReconciliationRequest:
                frame = encode_reconciliation_request(request)
            elif isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
                limits = self._workspace_limits
                if limits is None:
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                frame = encode_workspace_request(request)
            else:
                frame = encode_request(cast(BrokerRequest, request))
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
