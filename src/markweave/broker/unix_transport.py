"""Authenticated bounded Linux Unix-socket transport for the isolation broker."""

from __future__ import annotations

import errno
import fcntl
import math
import os
import socket
import stat
import struct
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, Thread, current_thread
from time import monotonic
from typing import Final, cast
from uuid import UUID

from markweave.broker.dispatch import BrokerDispatcher, request_operation
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal, RuntimeChannelLimits
from markweave.broker.protocol import (
    LENGTH_PREFIX_BYTES,
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerRequest,
    BrokerResponse,
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    ProofRequest,
    ProofResponse,
    ReadyRequest,
    ReadyResponse,
    StatusRequest,
    StatusResponse,
    TerminateRequest,
    TerminateResponse,
    decode_length_prefix,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)
from markweave.broker.workspace_protocol import (
    WORKSPACE_PROTOCOL_NAME,
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceOperation,
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
from markweave.reversions.models import ReverseContentLimits

_PEER_CREDENTIALS: Final = struct.Struct("3i")
_PARENT_MODE: Final = 0o700
_SOCKET_MODE: Final = 0o600
_MAX_SOCKET_PATH_BYTES: Final = 107
_LOCK_SUFFIX: Final = ".lock"


@dataclass(frozen=True, slots=True)
class UnixTransportLimits:
    """Deployment-supplied transport bounds, with no product defaults."""

    operation_timeout_seconds: float
    shutdown_timeout_seconds: float
    max_handlers: int
    listen_backlog: int

    def __post_init__(self) -> None:
        if (
            type(self.operation_timeout_seconds) not in {int, float}
            or self.operation_timeout_seconds <= 0
            or not math.isfinite(self.operation_timeout_seconds)
            or type(self.shutdown_timeout_seconds) not in {int, float}
            or self.shutdown_timeout_seconds <= 0
            or not math.isfinite(self.shutdown_timeout_seconds)
            or type(self.max_handlers) is not int
            or self.max_handlers <= 0
            or type(self.listen_backlog) is not int
            or self.listen_backlog <= 0
        ):
            raise ValueError("Broker Unix transport limits are invalid")


def _transport_failure(cause: BaseException | None = None) -> BrokerError:
    error = BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE)
    if cause is not None:
        error.__cause__ = cause
    return error


def _validate_socket_path(path: Path) -> tuple[Path, Path]:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or path.name
        in {
            "",
            ".",
            "..",
        }
    ):
        raise ValueError("Broker Unix socket path must be an absolute file path")
    try:
        encoded_path = os.fsencode(path)
    except UnicodeEncodeError as error:
        raise ValueError("Broker Unix socket path is not encodable") from error
    if b"\0" in encoded_path or len(encoded_path) > _MAX_SOCKET_PATH_BYTES:
        raise ValueError("Broker Unix socket path exceeds the Linux limit")
    parent = path.parent
    try:
        parent_lstat = parent.lstat()
        parent_resolved = parent.resolve(strict=True)
    except OSError as error:
        raise ValueError("Broker Unix socket parent is invalid") from error
    if (
        parent_resolved != parent
        or not stat.S_ISDIR(parent_lstat.st_mode)
        or parent_lstat.st_uid != os.geteuid()
        or stat.S_IMODE(parent_lstat.st_mode) != _PARENT_MODE
    ):
        raise ValueError(
            "Broker Unix socket parent must be a real owner-only directory"
        )
    return path, parent


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
    try:
        raw = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, _PEER_CREDENTIALS.size
        )
        pid, uid, gid = _PEER_CREDENTIALS.unpack(raw)
    except (AttributeError, OSError, struct.error) as error:
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED) from error
    if pid <= 0 or uid < 0 or gid < 0:
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    return pid, uid, gid


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _receive_exact(
    connection: socket.socket,
    size: int,
    deadline: float,
    *,
    eof_category: BrokerErrorCategory = BrokerErrorCategory.PROTOCOL_ERROR,
) -> bytes:
    received = bytearray()
    while len(received) < size:
        connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(received))
        if not chunk:
            raise BrokerError(eof_category)
        received.extend(chunk)
    return bytes(received)


def _receive_frame(
    connection: socket.socket,
    deadline: float,
    *,
    eof_category: BrokerErrorCategory = BrokerErrorCategory.PROTOCOL_ERROR,
) -> bytes:
    prefix = _receive_exact(
        connection, LENGTH_PREFIX_BYTES, deadline, eof_category=eof_category
    )
    length = decode_length_prefix(prefix)
    payload = _receive_exact(connection, length, deadline, eof_category=eof_category)
    connection.settimeout(_remaining(deadline))
    if connection.recv(1) != b"":
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return prefix + payload


def _receive_header(connection: socket.socket, deadline: float) -> bytes:
    prefix = _receive_exact(connection, LENGTH_PREFIX_BYTES, deadline)
    length = decode_length_prefix(prefix)
    return prefix + _receive_exact(connection, length, deadline)


def _require_eof(connection: socket.socket, deadline: float) -> None:
    connection.settimeout(_remaining(deadline))
    if connection.recv(1) != b"":
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _send_all(connection: socket.socket, frame: bytes, deadline: float) -> None:
    view = memoryview(frame)
    while view:
        connection.settimeout(_remaining(deadline))
        sent = connection.send(view)
        if sent <= 0:
            raise OSError("socket send returned no progress")
        view = view[sent:]


class UnixBrokerServer:
    """Serve one authenticated request per filesystem Unix connection."""

    def __init__(  # noqa: PLR0913
        self,
        path: Path,
        *,
        expected_client_uid: int,
        principal: AuthenticatedPrincipal,
        dispatcher: BrokerDispatcher,
        limits: UnixTransportLimits,
        workspace_limits: RuntimeChannelLimits | None = None,
    ) -> None:
        self._path, self._parent = _validate_socket_path(path)
        if type(expected_client_uid) is not int or expected_client_uid < 0:
            raise ValueError("Expected broker client UID is invalid")
        if expected_client_uid != os.geteuid():
            raise ValueError("Owner-only broker client UID must match the broker UID")
        if type(principal) is not AuthenticatedPrincipal:
            raise ValueError("Authenticated broker principal is invalid")
        if not isinstance(dispatcher, BrokerDispatcher):
            raise ValueError("Broker dispatcher is invalid")
        if type(limits) is not UnixTransportLimits:
            raise ValueError("Broker Unix transport limits are invalid")
        if (
            workspace_limits is not None
            and type(workspace_limits) is not RuntimeChannelLimits
        ):
            raise ValueError("Broker Unix workspace limits are invalid")
        self._expected_client_uid = expected_client_uid
        self._principal = principal
        self._dispatcher = dispatcher
        self._limits = limits
        self._workspace_limits = workspace_limits
        self._listener: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._lock_fd: int | None = None
        self._authority_lock_fd: int | None = None
        self._stopping = Event()
        self._handlers = BoundedSemaphore(limits.max_handlers)
        self._dispatch_gate = Lock()
        self._threads: set[Thread] = set()
        self._connections: set[socket.socket] = set()
        self._threads_lock = Lock()
        self._accept_thread: Thread | None = None
        self._fatal_error: BaseException | None = None

    @property
    def stopping(self) -> bool:
        """Return the content-free admission-stop signal."""

        return self._stopping.is_set()

    @property
    def failed(self) -> bool:
        """Return whether an internal transport failure requested shutdown."""

        with self._threads_lock:
            return self._fatal_error is not None

    def wait_stopping(self, timeout: float | None = None) -> bool:
        """Wait for requested or fatal shutdown without exposing its cause."""

        if timeout is not None and (
            type(timeout) not in {int, float}
            or timeout < 0
            or not math.isfinite(timeout)
        ):
            raise ValueError("Broker Unix wait timeout is invalid")
        return self._stopping.wait(timeout)

    def request_stop(self) -> None:
        """Stop new admission and wake the serving process."""

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
        """Bind the owner-only socket and start bounded connection handling."""

        if self._listener is not None:
            raise RuntimeError("Broker Unix server is already running")
        self._acquire_lifecycle_lock()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        identity: tuple[int, int] | None = None
        try:
            self._remove_stale_socket()
            listener.bind(str(self._path))
            os.chmod(self._path, _SOCKET_MODE)
            identity = self._socket_stat()
            self._listener = listener
            self._socket_identity = identity
            self._dispatcher.start()
            if not self._stopping.is_set():
                try:
                    listener.listen(self._limits.listen_backlog)
                    listener.settimeout(
                        min(self._limits.operation_timeout_seconds, 0.25)
                    )
                except OSError:
                    if not self._stopping.is_set():
                        raise
        except BaseException:
            listener.close()
            self._remove_path_if_identity(identity)
            self._listener = None
            self._socket_identity = None
            self._release_lifecycle_lock()
            self._release_authority_lock()
            raise
        if self._stopping.is_set():
            return
        thread = Thread(target=self._accept_loop, name="broker-unix-accept")
        self._accept_thread = thread
        try:
            thread.start()
        except BaseException:
            listener.close()
            self._remove_path_if_identity(identity)
            self._listener = None
            self._accept_thread = None
            self._socket_identity = None
            self._release_lifecycle_lock()
            self._release_authority_lock()
            raise

    def stop(self) -> None:
        """Stop accepting, drain bounded handlers, and unlink only our inode."""

        listener = self._listener
        if listener is None:
            self._release_authority_lock()
            return
        self._stopping.set()
        listener.close()
        deadline = monotonic() + self._limits.shutdown_timeout_seconds
        accept_thread = self._accept_thread
        if accept_thread is not None:
            accept_thread.join(max(0.0, deadline - monotonic()))
        with self._threads_lock:
            connections = tuple(self._connections)
        for connection in connections:
            with suppress(OSError):
                connection.shutdown(socket.SHUT_RDWR)
            connection.close()
        while True:
            with self._threads_lock:
                threads = tuple(self._threads)
            if not threads:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            threads[0].join(remaining)
        with self._threads_lock:
            undrained = bool(self._threads) or bool(
                accept_thread is not None and accept_thread.is_alive()
            )
        if undrained:
            raise RuntimeError("Broker Unix handlers did not drain")
        self._remove_path_if_identity(self._socket_identity)
        self._listener = None
        self._accept_thread = None
        self._socket_identity = None
        self._release_lifecycle_lock()
        self._release_authority_lock()
        with self._threads_lock:
            fatal_error = self._fatal_error
            self._fatal_error = None
        if fatal_error is not None:
            raise RuntimeError("Broker Unix server failed") from fatal_error

    def _release_authority_lock(self) -> None:
        descriptor = self._authority_lock_fd
        if descriptor is None:
            return
        self._authority_lock_fd = None
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(descriptor)

    def __enter__(self) -> UnixBrokerServer:
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
                if self._stopping.is_set():
                    return
                self._record_fatal(error)
                return
            if not self._handlers.acquire(blocking=False):
                connection.close()
                continue
            thread = Thread(
                target=self._handle_and_release,
                args=(connection,),
                name="broker-unix-handler",
            )
            with self._threads_lock:
                self._threads.add(thread)
                self._connections.add(connection)
            try:
                thread.start()
            except BaseException as error:
                with self._threads_lock:
                    self._threads.discard(thread)
                    self._connections.discard(connection)
                connection.close()
                self._handlers.release()
                self._record_fatal(error)
                return

    def _handle_and_release(self, connection: socket.socket) -> None:
        current = current_thread()
        try:
            self._handle(connection)
        except BaseException as error:
            self._record_fatal(error)
        finally:
            connection.close()
            self._handlers.release()
            with self._threads_lock:
                self._threads.discard(current)
                self._connections.discard(connection)

    def _handle(self, connection: socket.socket) -> None:  # noqa: PLR0912
        deadline = monotonic() + self._limits.operation_timeout_seconds
        try:
            _, uid, _ = _peer_credentials(connection)
            if uid != self._expected_client_uid:
                raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
            header = _receive_header(connection, deadline)
            workspace = frame_protocol(header) == WORKSPACE_PROTOCOL_NAME
            if workspace:
                if self._workspace_limits is None:
                    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                request = self._receive_workspace_request(
                    connection, header, deadline, self._workspace_limits
                )
            else:
                request = decode_request(header)
                _require_eof(connection, deadline)
        except BrokerError, OSError, TimeoutError:
            return
        try:
            dispatch_timeout = _remaining(deadline)
        except TimeoutError as error:
            self._record_fatal(error)
            return
        if not self._dispatch_gate.acquire(timeout=dispatch_timeout):
            self._record_fatal(TimeoutError("Broker dispatch gate deadline expired"))
            return
        try:
            try:
                response = self._dispatch_before_deadline(request, deadline)
                if response is None:
                    frame = None
                elif workspace:
                    frame = encode_workspace_response(
                        cast(WorkspaceResponse, response),
                        cast(RuntimeChannelLimits, self._workspace_limits),
                    )
                else:
                    frame = encode_response(cast(BrokerResponse, response))
            except BaseException as error:
                self._record_fatal(error)
                return
        finally:
            self._dispatch_gate.release()
        if frame is None:
            return
        try:
            _send_all(connection, frame, deadline)
        except OSError, TimeoutError:
            return

    @staticmethod
    def _receive_workspace_request(
        connection: socket.socket,
        header: bytes,
        deadline: float,
        limits: RuntimeChannelLimits,
    ) -> WorkspaceStageRequest | WorkspaceCollectRequest:
        request: WorkspaceRequestHeader = decode_workspace_request_header(
            header, limits
        )
        if type(request) is WorkspaceStageHeader:
            source = _receive_exact(connection, request.source_length, deadline)
            _require_eof(connection, deadline)
            return bind_workspace_source(request, source)
        if type(request) is not WorkspaceCollectRequest:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        _require_eof(connection, deadline)
        return request

    def _dispatch_before_deadline(
        self,
        request: BrokerRequest | WorkspaceStageRequest | WorkspaceCollectRequest,
        deadline: float,
    ) -> BrokerResponse | WorkspaceResponse | None:
        if self._stopping.is_set():
            return None
        try:
            _remaining(deadline)
        except TimeoutError as error:
            self._record_fatal(error)
            return None
        completed = Event()
        watchdog = Thread(
            target=self._watch_dispatch,
            args=(completed, deadline),
            name="broker-unix-watchdog",
        )
        watchdog.start()
        try:
            if isinstance(request, (WorkspaceStageRequest, WorkspaceCollectRequest)):
                response = self._dispatcher.dispatch_workspace(self._principal, request)
            else:
                response = self._dispatcher.dispatch(self._principal, request)
        finally:
            completed.set()
            watchdog.join()
        with self._threads_lock:
            if self._fatal_error is not None:
                return None
        return response

    def _watch_dispatch(self, completed: Event, deadline: float) -> None:
        try:
            remaining = _remaining(deadline)
        except TimeoutError as error:
            self._record_fatal(error)
            return
        if not completed.wait(remaining):
            self._record_fatal(TimeoutError("Broker dispatch deadline expired"))

    def _record_fatal(self, error: BaseException) -> None:
        with self._threads_lock:
            if self._fatal_error is None:
                self._fatal_error = error
        self._stopping.set()
        listener = self._listener
        if listener is not None:
            listener.close()

    def _socket_stat(self) -> tuple[int, int]:
        socket_stat = self._path.lstat()
        if (
            not stat.S_ISSOCK(socket_stat.st_mode)
            or socket_stat.st_uid != os.geteuid()
            or stat.S_IMODE(socket_stat.st_mode) != _SOCKET_MODE
        ):
            raise RuntimeError("Broker Unix socket permissions are invalid")
        return socket_stat.st_dev, socket_stat.st_ino

    def _remove_stale_socket(self) -> None:
        try:
            socket_stat = self._path.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISSOCK(socket_stat.st_mode)
            or socket_stat.st_uid != os.geteuid()
            or stat.S_IMODE(socket_stat.st_mode) != _SOCKET_MODE
        ):
            raise RuntimeError("Broker Unix socket path is occupied")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(min(self._limits.operation_timeout_seconds, 0.1))
            probe.connect(str(self._path))
        except OSError as error:
            if error.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                raise RuntimeError(
                    "Broker Unix socket state cannot be proven stale"
                ) from error
        else:
            raise RuntimeError("Broker Unix socket already has an active listener")
        finally:
            probe.close()
        self._remove_path_if_identity((socket_stat.st_dev, socket_stat.st_ino))

    def _remove_path_if_identity(self, identity: tuple[int, int] | None) -> None:
        if identity is None:
            return
        try:
            current = self._path.lstat()
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) != identity or not stat.S_ISSOCK(
            current.st_mode
        ):
            return
        self._path.unlink()

    def _acquire_lifecycle_lock(self) -> None:
        lock_path = Path(f"{self._path}{_LOCK_SUFFIX}")
        flags = os.O_CLOEXEC | os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR
        try:
            lock_fd = os.open(lock_path, flags, _SOCKET_MODE)
        except OSError as error:
            raise RuntimeError("Broker Unix lifecycle lock is invalid") from error
        try:
            lock_stat = os.fstat(lock_fd)
            path_stat = lock_path.lstat()
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_uid != os.geteuid()
                or stat.S_IMODE(lock_stat.st_mode) != _SOCKET_MODE
                or lock_stat.st_nlink != 1
                or (lock_stat.st_dev, lock_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                raise RuntimeError("Broker Unix lifecycle lock is invalid")
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(lock_fd)
            raise RuntimeError("Broker Unix server is already active") from error
        except BaseException:
            os.close(lock_fd)
            raise
        self._lock_fd = lock_fd

    def _release_lifecycle_lock(self) -> None:
        lock_fd = self._lock_fd
        if lock_fd is None:
            return
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        self._lock_fd = None


class UnixBrokerClient:
    """Send one bounded request after authenticating the Unix server peer."""

    def __init__(
        self,
        path: Path,
        *,
        expected_server_uid: int,
        expected_principal: AuthenticatedPrincipal,
        operation_timeout_seconds: float,
        workspace_limits: RuntimeChannelLimits | None = None,
    ) -> None:
        self._path, _ = _validate_socket_path(path)
        if type(expected_server_uid) is not int or expected_server_uid < 0:
            raise ValueError("Expected broker server UID is invalid")
        if expected_server_uid != os.geteuid():
            raise ValueError("Owner-only broker server UID must match the client UID")
        if type(expected_principal) is not AuthenticatedPrincipal:
            raise ValueError("Expected broker principal is invalid")
        if (
            type(operation_timeout_seconds) not in {int, float}
            or operation_timeout_seconds <= 0
            or not math.isfinite(operation_timeout_seconds)
        ):
            raise ValueError("Broker Unix client timeout is invalid")
        if (
            workspace_limits is not None
            and type(workspace_limits) is not RuntimeChannelLimits
        ):
            raise ValueError("Broker Unix workspace limits are invalid")
        self._expected_server_uid = expected_server_uid
        self._expected_principal = expected_principal
        self._operation_timeout_seconds = float(operation_timeout_seconds)
        self._workspace_limits = workspace_limits

    def request(self, request: BrokerRequest) -> BrokerResponse:
        """Perform one request/response exchange under one absolute deadline."""

        deadline = monotonic() + self._operation_timeout_seconds
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._verify_socket_leaf()
            connection.settimeout(_remaining(deadline))
            connection.connect(str(self._path))
            _, uid, _ = _peer_credentials(connection)
            if uid != self._expected_server_uid:
                raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
            _send_all(connection, encode_request(request), deadline)
            connection.shutdown(socket.SHUT_WR)
            response = decode_response(
                _receive_frame(
                    connection,
                    deadline,
                    eof_category=BrokerErrorCategory.TRANSPORT_FAILURE,
                )
            )
            _validate_response_binding(request, response, self._expected_principal)
            return response
        except BrokerError:
            raise
        except (OSError, TimeoutError) as error:
            raise _transport_failure(error) from error
        finally:
            connection.close()

    def _verify_socket_leaf(self) -> None:
        try:
            socket_stat = self._path.lstat()
        except OSError as error:
            raise _transport_failure(error) from error
        if (
            not stat.S_ISSOCK(socket_stat.st_mode)
            or stat.S_IMODE(socket_stat.st_mode) != _SOCKET_MODE
        ):
            raise _transport_failure()
        if socket_stat.st_uid != self._expected_server_uid:
            raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)

    def stage_workspace(
        self, request: WorkspaceStageRequest
    ) -> WorkspaceStageReceipt | WorkspaceErrorResponse:
        """Transfer one bounded source and receive a header-only receipt."""

        response = self._workspace_exchange(request)
        if isinstance(response, (WorkspaceStageReceipt, WorkspaceErrorResponse)):
            return response
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)

    def collect_workspace(self, request: WorkspaceCollectRequest) -> WorkspaceResponse:
        """Collect one pending, failure, or bounded success response."""

        response = self._workspace_exchange(request)
        if type(response) is WorkspaceStageReceipt:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return response

    def _workspace_exchange(
        self, request: WorkspaceStageRequest | WorkspaceCollectRequest
    ) -> WorkspaceResponse:
        limits = self._workspace_limits
        if limits is None:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        if type(request) is WorkspaceStageRequest:
            source = getattr(request, "source", None)
            declared = getattr(request, "limits", None)
            values = tuple(
                getattr(declared, name, None)
                for name in ReverseContentLimits.__dataclass_fields__
            )
            if any(type(value) is not int for value in values):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            try:
                validated = ReverseContentLimits(*cast("tuple[int, ...]", values))
            except TypeError, ValueError:
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from None
            if (
                type(source) is not bytes
                or not source
                or len(source) > limits.max_input_bytes
                or type(declared) is not ReverseContentLimits
                or len(source) > validated.max_input_bytes
                or validated.max_input_bytes > limits.max_input_bytes
                or validated.max_output_bytes > limits.max_output_bytes
            ):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        encoded = encode_workspace_request(request)
        deadline = monotonic() + self._operation_timeout_seconds
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._verify_socket_leaf()
            connection.settimeout(_remaining(deadline))
            connection.connect(str(self._path))
            _, uid, _ = _peer_credentials(connection)
            if uid != self._expected_server_uid:
                raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
            _send_all(connection, encoded, deadline)
            connection.shutdown(socket.SHUT_WR)
            header = _receive_header(connection, deadline)
            response, length, digest = decode_workspace_response_header(header, limits)
            payload = _receive_exact(
                connection,
                length,
                deadline,
                eof_category=BrokerErrorCategory.TRANSPORT_FAILURE,
            )
            _require_eof(connection, deadline)
            response = bind_workspace_result(response, payload, digest)
            _validate_workspace_response_binding(request, response)
            return response
        except BrokerError:
            raise
        except (OSError, TimeoutError) as error:
            raise _transport_failure(error) from error
        finally:
            connection.close()


def _validate_response_binding(
    request: BrokerRequest,
    response: BrokerResponse,
    principal: AuthenticatedPrincipal,
) -> None:
    """Reject a canonical response that is not bound to the exact request."""

    if response.request_id != request.request_id:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    if isinstance(response, ErrorResponse):
        if response.operation is not request_operation(request):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    valid = False
    match request, response:
        case CreateRequest(attempt_id=attempt), CreateResponse(attempt_id=answer):
            valid = answer == attempt
        case (
            StatusRequest(attempt_id=attempt, unit_id=unit),
            StatusResponse(attempt_id=answer_attempt, unit_id=answer_unit),
        ):
            valid = (answer_attempt, answer_unit) == (attempt, unit)
        case (
            TerminateRequest(attempt_id=attempt, unit_id=unit),
            TerminateResponse(proof=proof),
        ) | (
            ProofRequest(attempt_id=attempt, unit_id=unit),
            ProofResponse(proof=proof),
        ):
            valid = (proof.attempt_id, proof.unit_id, proof.principal) == (
                attempt,
                unit,
                principal,
            )
        case (
            AcknowledgeRequest(attempt_id=attempt, unit_id=unit, proof_id=proof_id),
            AcknowledgeResponse(
                attempt_id=answer_attempt,
                unit_id=answer_unit,
                proof_id=answer_proof,
                acknowledged=True,
            ),
        ):
            valid = (answer_attempt, answer_unit, answer_proof) == (
                attempt,
                unit,
                proof_id,
            )
        case ReadyRequest(), ReadyResponse():
            valid = True
    if not valid:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _validate_workspace_response_binding(
    request: WorkspaceStageRequest | WorkspaceCollectRequest,
    response: WorkspaceResponse,
) -> None:
    if response.request_id != request.request_id:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    operation = (
        WorkspaceOperation.STAGE
        if type(request) is WorkspaceStageRequest
        else WorkspaceOperation.COLLECT
    )
    if type(response) is WorkspaceErrorResponse:
        if response.operation is not operation:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    if isinstance(request, WorkspaceStageRequest):
        if (
            type(response) is not WorkspaceStageReceipt
            or response.request_id != request.request_id
            or response.stage_sequence != request.sequence
            or response.attempt_id != request.attempt_id
            or response.unit_id != request.unit_id
            or response.create_sequence != request.create_sequence
            or type(response.incarnation_id) is not UUID
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    if not isinstance(request, WorkspaceCollectRequest):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    receipt = getattr(response, "receipt", None)
    expected = WorkspaceStageReceipt(
        request.receipt_request_id,
        request.stage_sequence,
        request.attempt_id,
        request.unit_id,
        request.create_sequence,
        request.incarnation_id,
    )
    if receipt != expected:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
