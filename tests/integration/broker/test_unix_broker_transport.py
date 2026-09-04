from __future__ import annotations

import os
import socket
from pathlib import Path
from threading import current_thread, main_thread
from typing import Any
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.protocol import ReadyRequest, ReadyResponse, encode_request
from markweave.broker.unix_transport import (
    UnixBrokerClient,
    UnixBrokerServer,
    UnixTransportLimits,
    _peer_credentials,
)

pytestmark = pytest.mark.integration

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
PRINCIPAL = AuthenticatedPrincipal(UUID("00000000-0000-4000-8000-000000000005"))


def _server(
    tmp_path: Path, mocker: MockerFixture
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
        limits=UnixTransportLimits(0.25, 1, 1, 1),
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
