from __future__ import annotations

import os
import socket
import stat
import struct
from math import inf, nan
from pathlib import Path
from time import monotonic
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnitState,
    TerminationProof,
)
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
    BrokerRequest,
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
)
from markweave.broker.unix_transport import (
    UnixBrokerClient,
    UnixBrokerServer,
    UnixTransportLimits,
    _peer_credentials,
    _receive_exact,
    _remaining,
    _send_all,
    _validate_response_binding,
)

pytestmark = pytest.mark.unit

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000002")
UNIT_ID = UUID("00000000-0000-4000-8000-000000000003")
PROOF_ID = UUID("00000000-0000-4000-8000-000000000004")
PRINCIPAL = AuthenticatedPrincipal(UUID("00000000-0000-4000-8000-000000000005"))
DIGEST = EvidenceDigest("sha256:" + "a" * 64)
PROOF = TerminationProof(
    PROOF_ID,
    ATTEMPT_ID,
    UNIT_ID,
    PRINCIPAL,
    "policy-v1",
    DIGEST,
    DIGEST,
    DIGEST,
)


def _private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "broker"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    return parent


def _limits() -> UnixTransportLimits:
    return UnixTransportLimits(0.5, 0.5, 1, 1)


@pytest.mark.parametrize(
    "arguments",
    [
        (0, 1, 1, 1),
        (1, 0, 1, 1),
        (1, 1, 0, 1),
        (1, 1, 1, 0),
        (True, 1, 1, 1),
        (nan, 1, 1, 1),
        (1, inf, 1, 1),
    ],
)
def test_transport_limits_require_explicit_positive_values(arguments) -> None:
    with pytest.raises(ValueError, match="limits"):
        UnixTransportLimits(*arguments)


def test_path_must_be_absolute_real_owner_only_directory(tmp_path: Path) -> None:
    insecure = tmp_path / "insecure"
    insecure.mkdir(mode=0o755)
    insecure.chmod(0o755)

    with pytest.raises(ValueError, match="absolute"):
        UnixBrokerClient(
            Path("relative.sock"),
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )
    with pytest.raises(ValueError, match="owner-only"):
        UnixBrokerClient(
            insecure / "broker.sock",
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )


def test_path_rejects_symlinked_parent(tmp_path: Path) -> None:
    parent = _private_parent(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)

    with pytest.raises(ValueError, match="real"):
        UnixBrokerClient(
            alias / "broker.sock",
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )


def test_path_rejects_linux_sockaddr_overflow(tmp_path: Path) -> None:
    parent = _private_parent(tmp_path)

    with pytest.raises(ValueError, match="Linux limit"):
        UnixBrokerClient(
            parent / ("x" * 108),
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=1,
        )


def test_server_refuses_to_replace_non_socket(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    path.touch(mode=0o600)
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=_limits(),
    )

    with pytest.raises(RuntimeError, match="occupied"):
        server.start()
    assert path.is_file()
    with pytest.raises(RuntimeError, match="permissions"):
        server._socket_stat()
    server._remove_path_if_identity(None)
    server._accept_loop()


def test_stop_reports_handler_that_cannot_drain(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=mocker.Mock(spec=BrokerDispatcher),
        limits=UnixTransportLimits(0.5, 0.001, 1, 1),
    )
    server._listener = mocker.Mock(spec=socket.socket)
    thread = mocker.Mock()
    server._threads.add(thread)

    with pytest.raises(RuntimeError, match="did not drain"):
        server.stop()

    thread.join.assert_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_client_uid", True),
        ("expected_client_uid", -1),
        ("expected_client_uid", os.geteuid() + 1),
        ("principal", object()),
        ("dispatcher", object()),
        ("limits", object()),
    ],
)
def test_server_rejects_invalid_identity_or_components(
    tmp_path: Path,
    mocker: MockerFixture,
    field: str,
    value: object,
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    arguments: dict[str, Any] = {
        "expected_client_uid": os.geteuid(),
        "principal": PRINCIPAL,
        "dispatcher": mocker.Mock(spec=BrokerDispatcher),
        "limits": _limits(),
    }
    arguments[field] = value

    with pytest.raises(ValueError):
        UnixBrokerServer(path, **arguments)


def test_server_reconciles_before_exposing_socket(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.start.side_effect = lambda: assert_bound_not_listening(path)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=_limits(),
    )

    server.start()
    with pytest.raises(RuntimeError, match="already running"):
        server.start()
    server.stop()
    server.stop()

    dispatcher.start.assert_called_once_with()


def assert_bound_not_listening(path: Path) -> None:
    assert stat.S_ISSOCK(path.lstat().st_mode)
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe,
        pytest.raises(ConnectionRefusedError),
    ):
        probe.connect(str(path))


def test_server_removes_only_its_bound_inode_on_stop(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=_limits(),
    )
    server.start()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.unlink()
    path.touch(mode=0o600)

    server.stop()

    assert path.is_file()


def test_server_cleans_owned_stale_socket(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    path.chmod(0o600)
    stale.close()
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=_limits(),
    )

    server.start()
    server.stop()

    assert not path.exists()


def test_server_refuses_insecure_stale_socket(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    path.chmod(0o666)
    stale.close()
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=mocker.Mock(spec=BrokerDispatcher),
        limits=_limits(),
    )

    with pytest.raises(RuntimeError, match="occupied"):
        server.start()
    path.unlink()


def test_server_refuses_active_socket(tmp_path: Path, mocker: MockerFixture) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    active = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active.bind(str(path))
    path.chmod(0o600)
    active.listen(1)
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=dispatcher,
        limits=_limits(),
    )

    with pytest.raises(RuntimeError, match="active"):
        server.start()
    active.close()
    path.unlink()


def test_peer_credentials_fail_closed_when_kernel_lookup_fails(
    mocker: MockerFixture,
) -> None:
    connection = mocker.Mock(spec=socket.socket)
    connection.getsockopt.side_effect = OSError

    with pytest.raises(BrokerError) as captured:
        _peer_credentials(connection)
    assert captured.value.category is BrokerErrorCategory.AUTHENTICATION_FAILED


@pytest.mark.parametrize("credentials", [(0, 1, 1), (1, -1, 1), (1, 1, -1)])
def test_peer_credentials_reject_invalid_kernel_identity(
    mocker: MockerFixture, credentials: tuple[int, int, int]
) -> None:
    connection = mocker.Mock(spec=socket.socket)
    connection.getsockopt.return_value = struct.pack("3i", *credentials)

    with pytest.raises(BrokerError) as captured:
        _peer_credentials(connection)
    assert captured.value.category is BrokerErrorCategory.AUTHENTICATION_FAILED


def test_io_helpers_reject_expired_deadline_eof_and_zero_send(
    mocker: MockerFixture,
) -> None:
    connection = mocker.Mock(spec=socket.socket)
    connection.recv.return_value = b""

    with pytest.raises(TimeoutError):
        _remaining(monotonic() - 1)
    with pytest.raises(BrokerError) as captured:
        _receive_exact(connection, 1, monotonic() + 1)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR
    connection.send.return_value = 0
    with pytest.raises(OSError, match="no progress"):
        _send_all(connection, b"x", monotonic() + 1)


def test_client_maps_connect_failure_to_stable_transport_error(tmp_path: Path) -> None:
    path = _private_parent(tmp_path) / "missing.sock"
    client = UnixBrokerClient(
        path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=0.1,
    )

    with pytest.raises(BrokerError) as captured:
        client.request(cast("BrokerRequest", object()))
    assert captured.value.category is BrokerErrorCategory.TRANSPORT_FAILURE


def test_client_rejects_non_socket_leaf(tmp_path: Path) -> None:
    path = _private_parent(tmp_path) / "broker.sock"
    path.touch(mode=0o600)
    client = UnixBrokerClient(
        path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=1,
    )

    with pytest.raises(BrokerError) as captured:
        client.request(ReadyRequest(REQUEST_ID, 1))
    assert captured.value.category is BrokerErrorCategory.TRANSPORT_FAILURE


@pytest.mark.parametrize(
    ("uid", "principal", "timeout"),
    [
        (True, PRINCIPAL, 1),
        (-1, PRINCIPAL, 1),
        (os.geteuid() + 1, PRINCIPAL, 1),
        (0, object(), 1),
        (0, PRINCIPAL, 0),
        (0, PRINCIPAL, nan),
    ],
)
def test_client_rejects_invalid_configuration(
    tmp_path: Path, uid: object, principal: object, timeout: object
) -> None:
    path = _private_parent(tmp_path) / "broker.sock"

    with pytest.raises(ValueError):
        UnixBrokerClient(
            path,
            expected_server_uid=uid,  # ty: ignore[invalid-argument-type]
            expected_principal=principal,  # ty: ignore[invalid-argument-type]
            operation_timeout_seconds=timeout,  # ty: ignore[invalid-argument-type]
        )


@pytest.mark.parametrize(
    "response",
    [
        ReadyResponse(UUID("00000000-0000-4000-8000-000000000099"), True),
        ErrorResponse(
            REQUEST_ID, BrokerOperation.CREATE, BrokerErrorCategory.PROTOCOL_ERROR
        ),
    ],
)
def test_response_binding_rejects_replayed_or_wrong_operation(response) -> None:
    with pytest.raises(BrokerError) as captured:
        _validate_response_binding(ReadyRequest(REQUEST_ID, 1), response, PRINCIPAL)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_response_binding_accepts_exact_success_and_error() -> None:
    request = ReadyRequest(REQUEST_ID, 1)

    _validate_response_binding(request, ReadyResponse(REQUEST_ID, True), PRINCIPAL)
    _validate_response_binding(
        request,
        ErrorResponse(
            REQUEST_ID, BrokerOperation.READY, BrokerErrorCategory.RUNTIME_FAILURE
        ),
        PRINCIPAL,
    )


@pytest.mark.parametrize(
    ("broker_request", "response"),
    [
        (
            CreateRequest(REQUEST_ID, 1, ATTEMPT_ID),
            CreateResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED),
        ),
        (
            StatusRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID),
            StatusResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED),
        ),
        (
            TerminateRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID),
            TerminateResponse(REQUEST_ID, PROOF),
        ),
        (
            ProofRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID),
            ProofResponse(REQUEST_ID, PROOF),
        ),
        (
            AcknowledgeRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID, PROOF_ID),
            AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True),
        ),
    ],
)
def test_response_binding_accepts_every_exact_identity(
    broker_request: BrokerRequest, response
) -> None:
    _validate_response_binding(broker_request, response, PRINCIPAL)


@pytest.mark.parametrize(
    ("broker_request", "response"),
    [
        (
            CreateRequest(REQUEST_ID, 1, ATTEMPT_ID),
            CreateResponse(REQUEST_ID, UNIT_ID, UNIT_ID, ManagedUnitState.CREATED),
        ),
        (
            StatusRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID),
            StatusResponse(REQUEST_ID, ATTEMPT_ID, PROOF_ID, ManagedUnitState.CREATED),
        ),
        (
            TerminateRequest(REQUEST_ID, 1, ATTEMPT_ID, PROOF_ID),
            TerminateResponse(REQUEST_ID, PROOF),
        ),
        (
            AcknowledgeRequest(REQUEST_ID, 1, ATTEMPT_ID, UNIT_ID, UUID(int=0)),
            AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True),
        ),
        (
            ReadyRequest(REQUEST_ID, 1),
            CreateResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED),
        ),
    ],
)
def test_response_binding_rejects_every_identity_mismatch(
    broker_request: BrokerRequest, response
) -> None:
    with pytest.raises(BrokerError) as captured:
        _validate_response_binding(broker_request, response, PRINCIPAL)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR
