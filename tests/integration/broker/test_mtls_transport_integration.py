"""Real loopback mTLS boundary tests for the paired broker transport."""

from __future__ import annotations

import socket
import ssl
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker import mtls_transport
from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    ManagedUnitState,
    RuntimeChannelLimits,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerClient,
    MtlsBrokerServer,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    MtlsTransportLimits,
    leaf_certificate_sha256,
)
from markweave.broker.protocol import (
    CreateRequest,
    CreateResponse,
    ReadyRequest,
    ReadyResponse,
)
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceFailureResponse,
    WorkspacePendingResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import ReverseContentLimits, ReverseOutputMode

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

REQUEST_ID = UUID("10000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("10000000-0000-4000-8000-000000000002")
UNIT_ID = UUID("10000000-0000-4000-8000-000000000003")
INCARNATION_ID = UUID("10000000-0000-4000-8000-000000000004")
CLIENT_PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000005"))
SERVER_PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000006"))
CLIENT_URI = "spiffe://markweave.test/worker"
SERVER_URI = "spiffe://markweave.test/broker"
CHANNEL = RuntimeChannelLimits(1000, 2000)
CONTENT_LIMITS = ReverseContentLimits(
    1000, 2000, 100, 10, 10, 100, 10, 5, 2, 100, 200, 500, 1000
)


@dataclass(frozen=True)
class CertificateSet:
    ca: Path
    server_certificate: Path
    server_key: Path
    client_certificate: Path
    client_key: Path
    wrong_ca: Path
    wrong_client_certificate: Path
    wrong_client_key: Path
    extra_san_certificate: Path
    extra_san_key: Path
    next_client_certificate: Path
    next_client_key: Path
    extra_san_server_certificate: Path
    extra_san_server_key: Path
    expired_server_certificate: Path
    expired_server_key: Path
    expired_client_certificate: Path
    expired_client_key: Path


def _run(*arguments: str) -> None:
    subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _issue(  # noqa: PLR0913
    root: Path,
    *,
    name: str,
    ca_name: str,
    uri: str,
    eku: str,
    extra_san: bool = False,
    expired: bool = False,
) -> tuple[Path, Path]:
    certificate = root / f"{name}.crt"
    key = root / f"{name}.key"
    request = root / f"{name}.csr"
    extensions = root / f"{name}.ext"
    san = f"URI:{uri}"
    if extra_san:
        san += ",DNS:unexpected.example"
    extensions.write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
        f"extendedKeyUsage={eku}\n"
        f"subjectAltName={san}\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n",
        encoding="ascii",
    )
    _run(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        f"/CN={name}",
        "-keyout",
        str(key),
        "-out",
        str(request),
    )
    validity = (
        ("-not_before", "20000101000000Z", "-not_after", "20010101000000Z")
        if expired
        else ("-days", "1")
    )
    _run(
        "openssl",
        "x509",
        "-req",
        *validity,
        "-in",
        str(request),
        "-CA",
        str(root / f"{ca_name}.crt"),
        "-CAkey",
        str(root / f"{ca_name}.key"),
        "-CAcreateserial",
        "-extfile",
        str(extensions),
        "-out",
        str(certificate),
    )
    return certificate, key


def _ca(root: Path, name: str) -> Path:
    certificate = root / f"{name}.crt"
    _run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        f"/CN={name}",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-addext",
        "subjectKeyIdentifier=hash",
        "-keyout",
        str(root / f"{name}.key"),
        "-out",
        str(certificate),
    )
    return certificate


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> CertificateSet:
    root = tmp_path_factory.mktemp("mtls-certificates")
    ca = _ca(root, "ca")
    wrong_ca = _ca(root, "wrong-ca")
    server_certificate, server_key = _issue(
        root,
        name="server",
        ca_name="ca",
        uri=SERVER_URI,
        eku="serverAuth",
    )
    client_certificate, client_key = _issue(
        root,
        name="client",
        ca_name="ca",
        uri=CLIENT_URI,
        eku="clientAuth",
    )
    wrong_client_certificate, wrong_client_key = _issue(
        root,
        name="wrong-client-eku",
        ca_name="ca",
        uri=CLIENT_URI,
        eku="serverAuth",
    )
    extra_san_certificate, extra_san_key = _issue(
        root,
        name="extra-san-client",
        ca_name="ca",
        uri=CLIENT_URI,
        eku="clientAuth",
        extra_san=True,
    )
    next_client_certificate, next_client_key = _issue(
        root,
        name="next-client",
        ca_name="ca",
        uri=CLIENT_URI,
        eku="clientAuth",
    )
    extra_san_server_certificate, extra_san_server_key = _issue(
        root,
        name="extra-san-server",
        ca_name="ca",
        uri=SERVER_URI,
        eku="serverAuth",
        extra_san=True,
    )
    expired_server_certificate, expired_server_key = _issue(
        root,
        name="expired-server",
        ca_name="ca",
        uri=SERVER_URI,
        eku="serverAuth",
        expired=True,
    )
    expired_client_certificate, expired_client_key = _issue(
        root,
        name="expired-client",
        ca_name="ca",
        uri=CLIENT_URI,
        eku="clientAuth",
        expired=True,
    )
    return CertificateSet(
        ca,
        server_certificate,
        server_key,
        client_certificate,
        client_key,
        wrong_ca,
        wrong_client_certificate,
        wrong_client_key,
        extra_san_certificate,
        extra_san_key,
        next_client_certificate,
        next_client_key,
        extra_san_server_certificate,
        extra_san_server_key,
        expired_server_certificate,
        expired_server_key,
        expired_client_certificate,
        expired_client_key,
    )


def _pin(path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    return leaf_certificate_sha256(der)


def _identities(
    certificates: CertificateSet,
) -> tuple[MtlsLocalIdentity, MtlsPeerIdentity, MtlsLocalIdentity, MtlsPeerIdentity]:
    server_local = MtlsLocalIdentity(
        certificates.ca,
        certificates.server_certificate,
        certificates.server_key,
        SERVER_URI,
        SERVER_PRINCIPAL,
    )
    client_peer = MtlsPeerIdentity(
        CLIENT_URI, (_pin(certificates.client_certificate),), CLIENT_PRINCIPAL
    )
    client_local = MtlsLocalIdentity(
        certificates.ca,
        certificates.client_certificate,
        certificates.client_key,
        CLIENT_URI,
        CLIENT_PRINCIPAL,
    )
    server_peer = MtlsPeerIdentity(
        SERVER_URI, (_pin(certificates.server_certificate),), SERVER_PRINCIPAL
    )
    return server_local, client_peer, client_local, server_peer


def _server(
    certificates: CertificateSet,
    dispatcher: BrokerDispatcher,
    *,
    timeout: float = 2,
    workspace: bool = False,
) -> tuple[MtlsBrokerServer, MtlsLocalIdentity, MtlsPeerIdentity]:
    server_local, client_peer, client_local, server_peer = _identities(certificates)
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(timeout, timeout, 4, 4, 4, 4),
        workspace_limits=CHANNEL if workspace else None,
    )
    return server, client_local, server_peer


def _client(
    server: MtlsBrokerServer,
    local: MtlsLocalIdentity,
    peer: MtlsPeerIdentity,
    *,
    timeout: float = 2,
    workspace: bool = False,
) -> MtlsBrokerClient:
    return MtlsBrokerClient(
        server.endpoint,
        local_identity=local,
        server_identity=peer,
        operation_timeout_seconds=timeout,
        workspace_limits=CHANNEL if workspace else None,
    )


def _reserve(
    client: MtlsBrokerClient,
    *,
    request_id: UUID = REQUEST_ID,
    timeout: float = 2,
) -> tuple[ssl.SSLSocket, float, str]:
    deadline = time.monotonic() + timeout
    connection, _, _ = client._connect(deadline)
    try:
        mtls_transport._send_all(
            connection,
            mtls_transport._control_frame(
                mtls_transport._reservation_mapping(request_id)
            ),
            deadline,
        )
        acknowledgement = mtls_transport._decode_control(
            mtls_transport._receive_control(connection, deadline),
            mtls_transport._ACK_KEYS,
        )
        exchange = mtls_transport._exchange_id(acknowledgement.get("exchange_id"))
        assert acknowledgement == mtls_transport._ack_mapping(exchange, request_id)
        return connection, deadline, exchange
    except BaseException:
        connection.close()
        raise


def _submit(  # noqa: PLR0913
    client: MtlsBrokerClient,
    request: ReadyRequest,
    *,
    exchange: str = "a" * 64,
    request_id: UUID = REQUEST_ID,
    frame: bytes | None = None,
    close: bool = True,
    timeout: float = 2,
) -> ssl.SSLSocket | None:
    deadline = time.monotonic() + timeout
    connection, _, _ = client._connect(deadline)
    encoded = frame if frame is not None else mtls_transport.encode_request(request)
    mtls_transport._send_all(
        connection,
        mtls_transport._control_frame(
            mtls_transport._submit_mapping(exchange, request_id, encoded)
        )
        + encoded,
        deadline,
    )
    if close:
        try:
            mtls_transport._full_tls_close(connection, deadline)
        finally:
            connection.close()
        return None
    return connection


def test_real_paired_mtls_lifecycle_exchange(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server, client_local, server_peer = _server(certificates, dispatcher)

    with server:
        client = MtlsBrokerClient(
            server.endpoint,
            local_identity=client_local,
            server_identity=server_peer,
            operation_timeout_seconds=2,
        )
        response = client.request(ReadyRequest(REQUEST_ID, 1))

    assert response == ReadyResponse(REQUEST_ID, True)
    dispatcher.start.assert_called_once_with()
    dispatcher.dispatch.assert_called_once_with(
        CLIENT_PRINCIPAL, ReadyRequest(REQUEST_ID, 1)
    )


def test_real_paired_mtls_workspace_exchange(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    request = WorkspaceStageRequest(
        REQUEST_ID,
        2,
        ATTEMPT_ID,
        UNIT_ID,
        1,
        ".docx",
        CONTENT_LIMITS,
        b"private",
    )
    receipt = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch_workspace.return_value = receipt
    server, client_local, server_peer = _server(
        certificates, dispatcher, workspace=True
    )

    with server:
        client = MtlsBrokerClient(
            server.endpoint,
            local_identity=client_local,
            server_identity=server_peer,
            operation_timeout_seconds=2,
            workspace_limits=CHANNEL,
        )
        response = client.stage_workspace(request)

    assert response == receipt
    dispatcher.dispatch_workspace.assert_called_once_with(CLIENT_PRINCIPAL, request)


@pytest.mark.parametrize("outcome", ["pending", "failure", "success"])
def test_real_paired_mtls_workspace_collect_outcomes(
    certificates: CertificateSet, mocker: MockerFixture, outcome: str
) -> None:
    receipt = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    request = WorkspaceCollectRequest(
        UUID("10000000-0000-4000-8000-000000000007"),
        3,
        REQUEST_ID,
        2,
        ATTEMPT_ID,
        UNIT_ID,
        1,
        INCARNATION_ID,
    )
    if outcome == "pending":
        response = WorkspacePendingResponse(request.request_id, receipt)
    elif outcome == "failure":
        response = WorkspaceFailureResponse(
            request.request_id, receipt, ReverseErrorCategory.MALFORMED
        )
    else:
        response = WorkspaceSuccessResponse(
            request.request_id, receipt, ReverseOutputMode.MARKDOWN, b"bounded-result"
        )
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch_workspace.return_value = response
    server, client_local, server_peer = _server(
        certificates, dispatcher, workspace=True
    )

    with server:
        client = _client(server, client_local, server_peer, workspace=True)
        collected = client.collect_workspace(request)

    assert collected == response
    dispatcher.dispatch_workspace.assert_called_once_with(CLIENT_PRINCIPAL, request)


@pytest.mark.parametrize("failure", ["pin", "uri", "extra-san", "wrong-ca", "expired"])
def test_real_client_rejects_untrusted_or_misidentified_server(
    certificates: CertificateSet, mocker: MockerFixture, failure: str
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server_local, client_peer, client_local, server_peer = _identities(certificates)
    if failure == "extra-san":
        server_local = MtlsLocalIdentity(
            certificates.ca,
            certificates.extra_san_server_certificate,
            certificates.extra_san_server_key,
            SERVER_URI,
            SERVER_PRINCIPAL,
        )
        server_peer = MtlsPeerIdentity(
            SERVER_URI,
            (_pin(certificates.extra_san_server_certificate),),
            SERVER_PRINCIPAL,
        )
    elif failure == "expired":
        server_local = MtlsLocalIdentity(
            certificates.ca,
            certificates.expired_server_certificate,
            certificates.expired_server_key,
            SERVER_URI,
            SERVER_PRINCIPAL,
        )
        server_peer = MtlsPeerIdentity(
            SERVER_URI,
            (_pin(certificates.expired_server_certificate),),
            SERVER_PRINCIPAL,
        )
    elif failure == "pin":
        server_peer = MtlsPeerIdentity(
            SERVER_URI, (f"sha256:{'0' * 64}",), SERVER_PRINCIPAL
        )
    elif failure == "uri":
        server_peer = MtlsPeerIdentity(
            "spiffe://markweave.test/substituted",
            server_peer.leaf_certificate_sha256,
            SERVER_PRINCIPAL,
        )
    elif failure == "wrong-ca":
        client_local = MtlsLocalIdentity(
            certificates.wrong_ca,
            certificates.client_certificate,
            certificates.client_key,
            CLIENT_URI,
            CLIENT_PRINCIPAL,
        )
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(1, 1, 2, 2, 2, 2),
    )

    with server:
        client = _client(server, client_local, server_peer, timeout=1)
        with pytest.raises(BrokerError) as captured:
            client.request(ReadyRequest(REQUEST_ID, 1))

    assert captured.value.category in {
        BrokerErrorCategory.AUTHENTICATION_FAILED,
        BrokerErrorCategory.TRANSPORT_FAILURE,
    }
    dispatcher.dispatch.assert_not_called()


@pytest.mark.parametrize("failure", ["eku", "extra-san", "expired"])
def test_real_server_rejects_wrong_client_role_before_dispatch(
    certificates: CertificateSet, mocker: MockerFixture, failure: str
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server_local, _, _, server_peer = _identities(certificates)
    certificate, key = {
        "eku": (
            certificates.wrong_client_certificate,
            certificates.wrong_client_key,
        ),
        "extra-san": (
            certificates.extra_san_certificate,
            certificates.extra_san_key,
        ),
        "expired": (
            certificates.expired_client_certificate,
            certificates.expired_client_key,
        ),
    }[failure]
    client_local = MtlsLocalIdentity(
        certificates.ca, certificate, key, CLIENT_URI, CLIENT_PRINCIPAL
    )
    client_peer = MtlsPeerIdentity(CLIENT_URI, (_pin(certificate),), CLIENT_PRINCIPAL)
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(1, 1, 2, 2, 2, 2),
    )

    with server:
        client = _client(server, client_local, server_peer, timeout=1)
        with pytest.raises(BrokerError):
            client.request(ReadyRequest(REQUEST_ID, 1))

    dispatcher.dispatch.assert_not_called()


@pytest.mark.parametrize("failure", ["tls12", "alpn"])
def test_real_server_requires_tls13_and_exact_alpn(
    certificates: CertificateSet, mocker: MockerFixture, failure: str
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server, client_local, server_peer = _server(certificates, dispatcher, timeout=1)

    with server:
        client = _client(server, client_local, server_peer, timeout=1)
        if failure == "tls12":
            client._context.minimum_version = ssl.TLSVersion.TLSv1_2
            client._context.maximum_version = ssl.TLSVersion.TLSv1_2
        else:
            client._context.set_alpn_protocols(["unexpected/1"])
        with pytest.raises(BrokerError):
            client.request(ReadyRequest(REQUEST_ID, 1))

    dispatcher.dispatch.assert_not_called()


def test_real_request_does_not_dispatch_before_authenticated_close_notify(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    entered = Event()
    proceed = Event()
    original = mtls_transport._authenticated_eof

    def barrier(connection: ssl.SSLSocket, deadline: float) -> None:
        entered.set()
        assert proceed.wait(1)
        original(connection, deadline)

    mocker.patch.object(mtls_transport, "_authenticated_eof", side_effect=barrier)
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server, client_local, server_peer = _server(certificates, dispatcher)
    request = ReadyRequest(REQUEST_ID, 1)

    with server:
        client = _client(server, client_local, server_peer)
        response_connection, deadline, exchange = _reserve(client)
        request_connection = _submit(client, request, exchange=exchange, close=False)
        assert request_connection is not None
        assert entered.wait(1)
        dispatcher.dispatch.assert_not_called()
        proceed.set()
        mtls_transport._full_tls_close(request_connection, deadline)
        header = mtls_transport._decode_control(
            mtls_transport._receive_control(response_connection, deadline),
            mtls_transport._RESPONSE_KEYS,
        )
        assert header["request_id"] == str(REQUEST_ID)
        length = cast(int, header["frame_length"])
        mtls_transport._receive_exact(response_connection, length, deadline)
        mtls_transport._full_tls_close(response_connection, deadline)

    dispatcher.dispatch.assert_called_once_with(CLIENT_PRINCIPAL, request)


@pytest.mark.parametrize("malformation", ["fin", "rst", "partial", "extra", "slow"])
def test_real_malformed_or_unclosed_request_never_dispatches(
    certificates: CertificateSet,
    mocker: MockerFixture,
    malformation: str,
) -> None:
    timeout = 0.3 if malformation == "slow" else 1
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server, client_local, server_peer = _server(
        certificates, dispatcher, timeout=timeout
    )
    request = ReadyRequest(REQUEST_ID, 1)
    frame = mtls_transport.encode_request(request)

    with server:
        client = _client(server, client_local, server_peer, timeout=1)
        response_connection, _, exchange = _reserve(client, timeout=1)
        deadline = time.monotonic() + 1
        request_connection, _, _ = client._connect(deadline)
        control = mtls_transport._control_frame(
            mtls_transport._submit_mapping(exchange, REQUEST_ID, frame)
        )
        if malformation == "partial":
            mtls_transport._send_all(request_connection, control + frame[:-1], deadline)
            request_connection.close()
        elif malformation == "extra":
            mtls_transport._send_all(
                request_connection, control + frame + b"x", deadline
            )
            try:
                with pytest.raises((OSError, ssl.SSLError)):
                    mtls_transport._full_tls_close(request_connection, deadline)
            finally:
                request_connection.close()
        elif malformation == "slow":
            mtls_transport._send_all(request_connection, control, deadline)
            time.sleep(0.4)
            request_connection.close()
        elif malformation == "rst":
            mtls_transport._send_all(request_connection, control + frame, deadline)
            request_connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            request_connection.close()
        else:
            mtls_transport._send_all(request_connection, control + frame, deadline)
            request_connection.close()
        response_connection.close()
        time.sleep(0.1)

    dispatcher.dispatch.assert_not_called()


def test_real_request_without_reserved_pair_never_dispatches(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server, client_local, server_peer = _server(certificates, dispatcher)

    with server:
        client = _client(server, client_local, server_peer)
        with pytest.raises((OSError, ssl.SSLError)):
            _submit(client, ReadyRequest(REQUEST_ID, 1), exchange="b" * 64)

    dispatcher.dispatch.assert_not_called()


def test_real_two_allowed_leaf_pins_cannot_substitute_pair_incarnation(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server_local, client_peer, client_local, server_peer = _identities(certificates)
    client_peer = MtlsPeerIdentity(
        CLIENT_URI,
        (
            _pin(certificates.client_certificate),
            _pin(certificates.next_client_certificate),
        ),
        CLIENT_PRINCIPAL,
    )
    next_local = MtlsLocalIdentity(
        certificates.ca,
        certificates.next_client_certificate,
        certificates.next_client_key,
        CLIENT_URI,
        CLIENT_PRINCIPAL,
    )
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(1, 1, 2, 2, 2, 2),
    )

    with server:
        first_client = _client(server, client_local, server_peer)
        next_client = _client(server, next_local, server_peer)
        response_connection, _, exchange = _reserve(first_client)
        with pytest.raises((OSError, ssl.SSLError)):
            _submit(next_client, ReadyRequest(REQUEST_ID, 1), exchange=exchange)
        response_connection.close()

    dispatcher.dispatch.assert_not_called()


def test_real_pending_response_slot_does_not_block_its_request_channel(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server_local, client_peer, client_local, server_peer = _identities(certificates)
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(2, 2, 1, 1, 1, 1),
    )
    request = ReadyRequest(REQUEST_ID, 1)

    with server:
        client = _client(server, client_local, server_peer)
        response_connection, deadline, exchange = _reserve(client)
        with pytest.raises((BrokerError, OSError, ssl.SSLError)):
            second_response, _, _ = _reserve(client)
            second_response.close()
        _submit(client, request, exchange=exchange)
        header = mtls_transport._decode_control(
            mtls_transport._receive_control(response_connection, deadline),
            mtls_transport._RESPONSE_KEYS,
        )
        response_frame = mtls_transport._receive_exact(
            response_connection, cast(int, header["frame_length"]), deadline
        )
        mtls_transport._full_tls_close(response_connection, deadline)

    assert mtls_transport.decode_response(response_frame) == ReadyResponse(
        REQUEST_ID, True
    )
    dispatcher.dispatch.assert_called_once_with(CLIENT_PRINCIPAL, request)


def test_real_unauthenticated_handshake_saturation_is_bounded_and_recovers(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server_local, client_peer, client_local, server_peer = _identities(certificates)
    server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=server_local,
        client_identity=client_peer,
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(0.4, 1, 1, 1, 1, 1),
    )

    with server:
        stalled = socket.create_connection(
            (server.endpoint.host, server.endpoint.port), timeout=1
        )
        try:
            deadline = time.monotonic() + 1
            while server._handshakes.acquire(blocking=False):
                server._handshakes.release()
                assert time.monotonic() < deadline
                time.sleep(0.01)
            client = _client(server, client_local, server_peer, timeout=0.2)
            with pytest.raises(BrokerError):
                client.request(ReadyRequest(REQUEST_ID, 1))
        finally:
            stalled.close()

        deadline = time.monotonic() + 1
        while not server._handshakes.acquire(blocking=False):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        server._handshakes.release()
        recovered = _client(server, client_local, server_peer, timeout=1).request(
            ReadyRequest(REQUEST_ID, 1)
        )

    assert recovered == ReadyResponse(REQUEST_ID, True)
    dispatcher.dispatch.assert_called_once_with(
        CLIENT_PRINCIPAL, ReadyRequest(REQUEST_ID, 1)
    )


def test_real_two_request_racers_dispatch_exchange_at_most_once(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.return_value = ReadyResponse(REQUEST_ID, True)
    server, client_local, server_peer = _server(certificates, dispatcher)
    request = ReadyRequest(REQUEST_ID, 1)
    failures: list[type[BaseException]] = []

    with server:
        client = _client(server, client_local, server_peer)
        response_connection, deadline, exchange = _reserve(client)
        first = _submit(client, request, exchange=exchange, close=False)
        second = _submit(client, request, exchange=exchange, close=False)
        assert first is not None and second is not None

        def close_request(connection: ssl.SSLSocket) -> None:
            try:
                mtls_transport._full_tls_close(connection, deadline)
            except BaseException as error:
                failures.append(type(error))
            finally:
                connection.close()

        threads = [
            Thread(target=close_request, args=(first,)),
            Thread(target=close_request, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            assert not thread.is_alive()
        header = mtls_transport._decode_control(
            mtls_transport._receive_control(response_connection, deadline),
            mtls_transport._RESPONSE_KEYS,
        )
        mtls_transport._receive_exact(
            response_connection, cast(int, header["frame_length"]), deadline
        )
        mtls_transport._full_tls_close(response_connection, deadline)

    dispatcher.dispatch.assert_called_once_with(CLIENT_PRINCIPAL, request)
    assert len(failures) <= 1


def test_real_lost_create_response_can_replay_on_a_fresh_exchange(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    request = CreateRequest(REQUEST_ID, 1, ATTEMPT_ID)
    response = CreateResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED)
    dispatched = Event()
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.side_effect = lambda *_args: (dispatched.set(), response)[1]
    server, client_local, server_peer = _server(certificates, dispatcher)

    with server:
        client = _client(server, client_local, server_peer)
        lost_response, _, exchange = _reserve(client)
        lost_response.close()
        deadline = time.monotonic() + 2
        request_connection, _, _ = client._connect(deadline)
        frame = mtls_transport.encode_request(request)
        mtls_transport._send_all(
            request_connection,
            mtls_transport._control_frame(
                mtls_transport._submit_mapping(exchange, REQUEST_ID, frame)
            )
            + frame,
            deadline,
        )
        try:
            mtls_transport._full_tls_close(request_connection, deadline)
        finally:
            request_connection.close()
        assert dispatched.wait(1)

        replayed = client.request(request)

    assert replayed == response
    assert dispatcher.dispatch.call_count == 2


def test_real_lost_stage_response_can_replay_on_a_fresh_exchange(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    request = WorkspaceStageRequest(
        REQUEST_ID,
        2,
        ATTEMPT_ID,
        UNIT_ID,
        1,
        ".docx",
        CONTENT_LIMITS,
        b"private",
    )
    receipt = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    dispatched = Event()
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch_workspace.side_effect = lambda *_args: (
        dispatched.set(),
        receipt,
    )[1]
    server, client_local, server_peer = _server(
        certificates, dispatcher, workspace=True
    )

    with server:
        client = _client(server, client_local, server_peer, workspace=True)
        lost_response, _, exchange = _reserve(client)
        lost_response.close()
        deadline = time.monotonic() + 2
        request_connection, _, _ = client._connect(deadline)
        frame = mtls_transport.encode_workspace_request(request)
        mtls_transport._send_all(
            request_connection,
            mtls_transport._control_frame(
                mtls_transport._submit_mapping(exchange, REQUEST_ID, frame)
            )
            + frame,
            deadline,
        )
        try:
            mtls_transport._full_tls_close(request_connection, deadline)
        finally:
            request_connection.close()
        assert dispatched.wait(1)

        replayed = client.stage_workspace(request)

    assert replayed == receipt
    assert dispatcher.dispatch_workspace.call_count == 2


def test_real_request_after_reservation_deadline_never_dispatches(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server, client_local, server_peer = _server(certificates, dispatcher, timeout=0.2)

    with server:
        client = _client(server, client_local, server_peer, timeout=1)
        response_connection, _, exchange = _reserve(client, timeout=1)
        time.sleep(0.3)
        with pytest.raises((OSError, ssl.SSLError)):
            _submit(
                client,
                ReadyRequest(REQUEST_ID, 1),
                exchange=exchange,
                timeout=1,
            )
        response_connection.close()

    dispatcher.dispatch.assert_not_called()


def test_real_internal_dispatch_failure_sets_content_free_fatal_signal(
    certificates: CertificateSet, mocker: MockerFixture
) -> None:
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    dispatcher.dispatch.side_effect = RuntimeError("private dispatcher detail")
    server, client_local, server_peer = _server(certificates, dispatcher, timeout=1)

    server.start()
    client = _client(server, client_local, server_peer, timeout=1)
    with pytest.raises(BrokerError) as captured:
        client.request(ReadyRequest(REQUEST_ID, 1))
    assert server.wait_stopping(1)
    assert server.failed
    with pytest.raises(RuntimeError, match="Broker mTLS server failed") as stopped:
        server.stop()

    assert "private dispatcher detail" not in str(captured.value)
    assert "private dispatcher detail" not in str(stopped.value)
