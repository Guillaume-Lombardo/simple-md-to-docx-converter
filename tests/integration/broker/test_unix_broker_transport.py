from __future__ import annotations

import os
import socket
from contextlib import suppress
from pathlib import Path
from threading import Event, Thread, current_thread, main_thread
from time import monotonic, sleep
from typing import Any
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.process import BrokerProcess
from markweave.broker.protocol import ReadyRequest, ReadyResponse, encode_request
from markweave.broker.unix_transport import (
    UnixBrokerClient,
    UnixBrokerServer,
    UnixTransportLimits,
    _peer_credentials,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
PRINCIPAL = AuthenticatedPrincipal(UUID("00000000-0000-4000-8000-000000000005"))


def _server(
    tmp_path: Path,
    mocker: MockerFixture,
    *,
    limits: UnixTransportLimits | None = None,
) -> tuple[Path, UnixBrokerServer, Any]:
    parent = tmp_path / "broker"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    path = parent / "broker.sock"
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=limits or UnixTransportLimits(0.25, 1, 1, 1),
    )
    return path, server, dispatcher


def test_real_unix_exchange_authenticates_both_peers_and_dispatches_once(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    request = ReadyRequest(REQUEST_ID, 1)

    with server:
        client = UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )
        response = client.request(request)

    assert response == ReadyResponse(REQUEST_ID, True)
    dispatcher.dispatch.assert_called_once_with(PRINCIPAL, request)
    assert not path.exists()


def test_shutdown_during_real_reconciliation_never_opens_admission(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    hard_exit = mocker.Mock()
    process = BrokerProcess(
        server, hard_shutdown_timeout_seconds=1, hard_exit=hard_exit
    )

    def request_shutdown_during_reconciliation() -> None:
        process._handle_signal(15, None)
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection,
            pytest.raises(OSError),
        ):
            connection.connect(str(path))

    dispatcher.start.side_effect = request_shutdown_during_reconciliation

    assert process.run() == 0

    assert server.stopping
    assert not path.exists()
    dispatcher.dispatch.assert_not_called()
    hard_exit.assert_not_called()


def test_shutdown_immediately_before_real_start_never_opens_admission(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    hard_exit = mocker.Mock()
    process = BrokerProcess(
        server, hard_shutdown_timeout_seconds=1, hard_exit=hard_exit
    )
    process._handle_signal(15, None)

    def assert_bound_socket_is_not_listening() -> None:
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection,
            pytest.raises(OSError),
        ):
            connection.connect(str(path))

    dispatcher.start.side_effect = assert_bound_socket_is_not_listening

    assert process.run() == 0

    assert server.stopping
    assert not path.exists()
    dispatcher.dispatch.assert_not_called()
    hard_exit.assert_not_called()


def test_server_authenticates_peer_before_read_or_dispatch(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    mocker.patch(
        "markweave.broker.unix_transport._peer_credentials",
        return_value=(1, os.geteuid() + 1, os.getegid()),
    )

    with server, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(path))
        assert connection.recv(1) == b""

    dispatcher.dispatch.assert_not_called()


@pytest.mark.parametrize("trailing", [b"x", b""])
def test_server_requires_exact_frame_and_client_eof_before_dispatch(
    tmp_path: Path,
    mocker: MockerFixture,
    trailing: bytes,
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)

    with server, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(path))
        connection.sendall(encode_request(ReadyRequest(REQUEST_ID, 1)) + trailing)
        if trailing:
            connection.shutdown(socket.SHUT_WR)
        assert connection.recv(1) == b""

    dispatcher.dispatch.assert_not_called()


def test_client_rejects_unexpected_kernel_server_uid_without_sending(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)

    def peer_credentials(connection: socket.socket) -> tuple[int, int, int]:
        pid, uid, gid = _peer_credentials(connection)
        if current_thread() is main_thread():
            return pid, uid + 1, gid
        return pid, uid, gid

    with server:
        mocker.patch(
            "markweave.broker.unix_transport._peer_credentials",
            side_effect=peer_credentials,
        )
        client = UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )
        with pytest.raises(BrokerError) as captured:
            client.request(ReadyRequest(REQUEST_ID, 1))

    assert captured.value.category is BrokerErrorCategory.AUTHENTICATION_FAILED
    dispatcher.dispatch.assert_not_called()


def test_unexpected_dispatch_failure_stops_server_fail_closed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    dispatcher.dispatch.side_effect = ValueError("private runtime detail")
    server.start()
    client = UnixBrokerClient(
        path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=1,
    )

    with pytest.raises(BrokerError) as captured:
        client.request(ReadyRequest(REQUEST_ID, 1))
    with pytest.raises(RuntimeError, match="server failed") as stopped:
        server.stop()

    assert captured.value.category is BrokerErrorCategory.TRANSPORT_FAILURE
    assert isinstance(stopped.value.__cause__, ValueError)


def test_lifecycle_lock_prevents_replacement_during_reconciliation(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, first, first_dispatcher = _server(tmp_path, mocker)
    entered = Event()
    release = Event()

    def reconcile() -> None:
        entered.set()
        assert release.wait(1)

    first_dispatcher.start.side_effect = reconcile
    starter = Thread(target=first.start)
    starter.start()
    assert entered.wait(1)
    second = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=mocker.Mock(spec=BrokerDispatcher),
        limits=UnixTransportLimits(0.25, 1, 1, 1),
    )

    with pytest.raises(RuntimeError, match="already active"):
        second.start()

    release.set()
    starter.join(1)
    assert not starter.is_alive()
    first.stop()


def test_dispatch_deadline_stops_admission_and_blocks_restart_until_drained(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(
        tmp_path, mocker, limits=UnixTransportLimits(0.05, 0.05, 1, 1)
    )
    release = Event()
    exited = Event()

    def blocked_dispatch(*_args: object) -> ReadyResponse:
        try:
            assert release.wait(1)
            return ReadyResponse(REQUEST_ID, True)
        finally:
            exited.set()

    dispatcher.dispatch.side_effect = blocked_dispatch
    server.start()
    client = UnixBrokerClient(
        path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=0.2,
    )
    with pytest.raises(BrokerError) as captured:
        client.request(ReadyRequest(REQUEST_ID, 1))
    assert captured.value.category is BrokerErrorCategory.TRANSPORT_FAILURE
    with pytest.raises(RuntimeError, match="did not drain"):
        server.stop()

    replacement = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=mocker.Mock(spec=BrokerDispatcher),
        limits=UnixTransportLimits(0.25, 1, 1, 1),
    )
    with pytest.raises(RuntimeError, match="already active"):
        replacement.start()

    release.set()
    assert exited.wait(1)
    with pytest.raises(RuntimeError, match="server failed") as stopped:
        server.stop()
    assert isinstance(stopped.value.__cause__, TimeoutError)


def test_expired_queued_dispatch_never_enters_service_after_active_request(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(
        tmp_path, mocker, limits=UnixTransportLimits(0.1, 1, 2, 2)
    )
    entered = Event()
    release = Event()
    sequences: list[int] = []

    def serial_dispatch(
        _principal: AuthenticatedPrincipal, request: ReadyRequest
    ) -> ReadyResponse:
        sequences.append(request.sequence)
        if request.sequence == 1:
            entered.set()
            assert release.wait(1)
        return ReadyResponse(request.request_id, True)

    dispatcher.dispatch.side_effect = serial_dispatch
    server.start()

    def request(sequence: int) -> None:
        client = UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=0.3,
        )
        with suppress(BrokerError):
            client.request(ReadyRequest(UUID(int=sequence), sequence))

    active = Thread(target=request, args=(1,))
    queued = Thread(target=request, args=(2,))
    active.start()
    assert entered.wait(1)
    queued.start()
    assert server._stopping.wait(1)
    release.set()
    active.join(1)
    queued.join(1)
    assert not active.is_alive()
    assert not queued.is_alive()
    assert sequences == [1]
    with pytest.raises(RuntimeError, match="server failed"):
        server.stop()


def test_unexpected_dispatch_failure_fences_queued_request_before_gate_release(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(
        tmp_path, mocker, limits=UnixTransportLimits(0.5, 1, 2, 2)
    )
    entered = Event()
    release = Event()
    calls: list[int] = []

    def failing_dispatch(
        _principal: AuthenticatedPrincipal, request: ReadyRequest
    ) -> ReadyResponse:
        calls.append(request.sequence)
        if request.sequence == 1:
            entered.set()
            assert release.wait(1)
            raise ValueError("private runtime detail")
        return ReadyResponse(request.request_id, True)

    dispatcher.dispatch.side_effect = failing_dispatch
    server.start()

    def request(sequence: int) -> None:
        client = UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )
        with suppress(BrokerError):
            client.request(ReadyRequest(UUID(int=sequence), sequence))

    first = Thread(target=request, args=(1,))
    second = Thread(target=request, args=(2,))
    first.start()
    assert entered.wait(1)
    second.start()
    deadline = monotonic() + 1
    while len(server._connections) < 2 and monotonic() < deadline:
        sleep(0.001)
    assert len(server._connections) == 2

    server._threads_lock.acquire()
    try:
        release.set()
        sleep(0.05)
        assert calls == [1]
    finally:
        server._threads_lock.release()

    first.join(1)
    second.join(1)
    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == [1]
    with pytest.raises(RuntimeError, match="server failed") as stopped:
        server.stop()
    assert isinstance(stopped.value.__cause__, ValueError)


@pytest.mark.parametrize(
    "invalid_frame",
    [
        (4097).to_bytes(4, "big"),
        (2).to_bytes(4, "big") + b"x",
    ],
)
def test_real_unix_rejects_oversized_or_truncated_frame_before_dispatch(
    tmp_path: Path, mocker: MockerFixture, invalid_frame: bytes
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)

    with server, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(path))
        connection.sendall(invalid_frame)
        connection.shutdown(socket.SHUT_WR)
        assert connection.recv(1) == b""

    dispatcher.dispatch.assert_not_called()


def test_real_unix_handler_saturation_rejects_excess_connection(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)

    with (
        server,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as occupied,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as excess,
    ):
        occupied.settimeout(1)
        occupied.connect(str(path))
        deadline = monotonic() + 1
        while not server._connections and monotonic() < deadline:
            sleep(0.001)
        assert server._connections
        excess.settimeout(1)
        excess.connect(str(path))
        excess.sendall(encode_request(ReadyRequest(REQUEST_ID, 1)))
        excess.shutdown(socket.SHUT_WR)
        try:
            rejected = excess.recv(1)
        except ConnectionResetError:
            rejected = b""
        assert rejected == b""

    dispatcher.dispatch.assert_not_called()


def test_client_preserves_canonical_response_binding_failure(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path, server, dispatcher = _server(tmp_path, mocker)
    dispatcher.dispatch.return_value = ReadyResponse(UUID(int=0), True)

    with server:
        client = UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )
        with pytest.raises(BrokerError) as captured:
            client.request(ReadyRequest(REQUEST_ID, 1))

    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR
