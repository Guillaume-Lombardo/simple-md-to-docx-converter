"""Tests for the separately versioned paired mTLS broker transport."""

from __future__ import annotations

import json
import os
import socket
import ssl
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker import mtls_transport
from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal, RuntimeChannelLimits
from markweave.broker.mtls_transport import (
    MTLS_ALPN,
    MTLS_PROTOCOL_NAME,
    MTLS_PROTOCOL_VERSION,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    MtlsTransportLimits,
    leaf_certificate_sha256,
)
from markweave.broker.protocol import ReadyRequest, ReadyResponse, encode_request
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    encode_workspace_request,
    encode_workspace_response,
)
from markweave.reversions.models import ReverseContentLimits

pytestmark = [pytest.mark.unit, pytest.mark.light_coverage]

REQUEST_ID = UUID("20000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("20000000-0000-4000-8000-000000000002")
UNIT_ID = UUID("20000000-0000-4000-8000-000000000003")
INCARNATION_ID = UUID("20000000-0000-4000-8000-000000000004")
PRINCIPAL = AuthenticatedPrincipal(UUID("20000000-0000-4000-8000-000000000005"))
URI = "spiffe://markweave.test/worker"
PIN = f"sha256:{'1' * 64}"
CHANNEL = RuntimeChannelLimits(1000, 2000)
LOCAL = MtlsLocalIdentity(
    Path("/ca.crt"),
    Path("/identity.crt"),
    Path("/identity.key"),
    URI,
    PRINCIPAL,
)
PEER = MtlsPeerIdentity(URI, (PIN,), PRINCIPAL)
CONTENT_LIMITS = ReverseContentLimits(
    1000, 2000, 100, 10, 10, 100, 10, 5, 2, 100, 200, 500, 1000
)


def _stage() -> WorkspaceStageRequest:
    return WorkspaceStageRequest(
        REQUEST_ID,
        2,
        ATTEMPT_ID,
        UNIT_ID,
        1,
        ".docx",
        CONTENT_LIMITS,
        b"private",
    )


def _mock_server(mocker: MockerFixture) -> mtls_transport.MtlsBrokerServer:
    mocker.patch.object(mtls_transport, "_tls_context", return_value=mocker.Mock())
    return mtls_transport.MtlsBrokerServer(
        mtls_transport.MtlsEndpoint("127.0.0.1", 0),
        local_identity=LOCAL,
        client_identity=PEER,
        dispatcher=mocker.Mock(spec=BrokerDispatcher),
        limits=mtls_transport.MtlsTransportLimits(1, 1, 1, 1, 1, 1),
    )


def test_mtls_control_protocol_has_an_independent_exact_golden() -> None:
    assert mtls_transport._control_frame(
        mtls_transport._reservation_mapping(REQUEST_ID)
    ) == (
        b"\x00\x00\x00\x97"
        b'{"channel":"response","operation":"RESERVE","protocol":"markweave-'
        b'reverse-broker-mtls","request_id":"20000000-0000-4000-8000-000000000001",'
        b'"version":1}'
    )
    assert MTLS_PROTOCOL_NAME == "markweave-reverse-broker-mtls"
    assert MTLS_PROTOCOL_VERSION == 1
    assert MTLS_ALPN == "markweave-reverse-broker-mtls/1"


def test_existing_protocol_frames_are_embedded_byte_for_byte() -> None:
    lifecycle = encode_request(ReadyRequest(REQUEST_ID, 1))
    workspace = encode_workspace_request(_stage())

    lifecycle_mapping = mtls_transport._submit_mapping("a" * 64, REQUEST_ID, lifecycle)
    workspace_mapping = mtls_transport._submit_mapping("a" * 64, REQUEST_ID, workspace)

    assert mtls_transport._decode_existing_request(lifecycle, CHANNEL) == ReadyRequest(
        REQUEST_ID, 1
    )
    assert mtls_transport._decode_existing_request(workspace, CHANNEL) == _stage()
    assert lifecycle_mapping["frame_length"] == len(lifecycle)
    assert workspace_mapping["frame_length"] == len(workspace)


@pytest.mark.parametrize(
    "endpoint",
    [
        ("localhost", 443),
        ("127.0.0.01", 443),
        ("::1", 443),
        ("127.0.0.1", -1),
        ("127.0.0.1", 65536),
        (cast(Any, 1), 443),
        ("127.0.0.1", cast(Any, True)),
    ],
)
def test_endpoint_rejects_noncanonical_or_non_ipv4_values(
    endpoint: tuple[Any, Any],
) -> None:
    with pytest.raises(ValueError, match="endpoint is invalid"):
        MtlsEndpoint(*endpoint)


@pytest.mark.parametrize(
    "values",
    [
        (0, 1, 1, 1, 1, 1),
        (1, float("inf"), 1, 1, 1, 1),
        (1, 1, 0, 1, 1, 1),
        (1, 1, 1, True, 1, 1),
    ],
)
def test_transport_limits_reject_missing_or_nonfinite_bounds(
    values: tuple[Any, ...],
) -> None:
    with pytest.raises(ValueError, match="limits are invalid"):
        MtlsTransportLimits(*values)


@pytest.mark.parametrize(
    "uri",
    [
        "https://markweave.test/worker",
        "spiffe://markweave.test/",
        "spiffe://user@markweave.test/worker",
        "spiffe://markweave.test:443/worker",
        "spiffe://markweave.test/worker?query",
        "spiffe://MARKWEAVE.test/worker",
        "spiffe://markweave.test/%FF",
        "é",
    ],
)
def test_peer_identity_rejects_noncanonical_role_uri(uri: str) -> None:
    with pytest.raises(ValueError, match="peer identity is invalid"):
        MtlsPeerIdentity(uri, (PIN,), PRINCIPAL)


@pytest.mark.parametrize(
    "pins",
    [
        (),
        (PIN, PIN),
        (PIN, PIN.replace("1", "2"), PIN.replace("1", "3")),
        ("1" * 64,),
        (f"sha256:{'A' * 64}",),
        (cast(Any, [PIN])),
    ],
)
def test_peer_identity_rejects_noncanonical_leaf_pinsets(pins: Any) -> None:
    with pytest.raises(ValueError, match="peer identity is invalid"):
        MtlsPeerIdentity(URI, pins, PRINCIPAL)


def test_leaf_certificate_digest_is_canonical_and_type_checked() -> None:
    assert leaf_certificate_sha256(b"certificate") == (
        "sha256:03d66dd08835c1ca3f128cceacd1f31ac94163096b20f445ae84285bc0832d72"
    )
    with pytest.raises(ValueError, match="leaf certificate is invalid"):
        leaf_certificate_sha256(cast(Any, b""))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"operation":"RESERVE","operation":"RESERVE"}',
        b'{"version":NaN}',
        b'{"version": 1}',
        b"\xff",
        b"{}",
    ],
)
def test_control_decoder_rejects_duplicate_noncanonical_or_unknown_data(
    payload: bytes,
) -> None:
    frame = len(payload).to_bytes(4, "big") + payload
    with pytest.raises(BrokerError) as captured:
        mtls_transport._decode_control(frame, mtls_transport._ACK_KEYS)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_control_decoder_rejects_oversized_or_mismatched_length() -> None:
    with pytest.raises(BrokerError):
        mtls_transport._decode_control(
            cast(Any, bytearray(b"xxxx")), mtls_transport._ACK_KEYS
        )
    with pytest.raises(BrokerError):
        mtls_transport._decode_control(
            (4097).to_bytes(4, "big") + b"x" * 4097,
            mtls_transport._ACK_KEYS,
        )
    with pytest.raises(BrokerError):
        mtls_transport._decode_control(
            b"\x00\x00\x00\x02x",
            mtls_transport._ACK_KEYS,
        )


def test_peer_authentication_requires_tls_alpn_exact_uri_and_leaf_pin(
    mocker: MockerFixture,
) -> None:
    certificate = b"exact leaf"
    pin = leaf_certificate_sha256(certificate)
    expected = MtlsPeerIdentity(URI, (pin,), PRINCIPAL)
    connection = mocker.Mock(spec=ssl.SSLSocket)
    connection.version.return_value = "TLSv1.3"
    connection.selected_alpn_protocol.return_value = MTLS_ALPN
    connection.getpeercert.side_effect = lambda binary_form=False: (
        certificate if binary_form else {"subjectAltName": (("URI", URI),)}
    )

    assert mtls_transport._authenticate_peer(connection, expected) == (
        PRINCIPAL,
        pin,
    )

    for version, alpn, san, pinned in [
        ("TLSv1.2", MTLS_ALPN, URI, pin),
        ("TLSv1.3", "other", URI, pin),
        ("TLSv1.3", MTLS_ALPN, f"{URI}/other", pin),
        ("TLSv1.3", MTLS_ALPN, URI, f"sha256:{'0' * 64}"),
    ]:
        connection.version.return_value = version
        connection.selected_alpn_protocol.return_value = alpn
        connection.getpeercert.side_effect = lambda binary_form=False, san=san: (
            certificate if binary_form else {"subjectAltName": (("URI", san),)}
        )
        with pytest.raises(BrokerError) as captured:
            mtls_transport._authenticate_peer(
                connection, MtlsPeerIdentity(URI, (pinned,), PRINCIPAL)
            )
        assert captured.value.category is BrokerErrorCategory.AUTHENTICATION_FAILED


def test_workspace_response_round_trip_keeps_existing_frame_exact() -> None:
    request = _stage()
    response = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    frame = encode_workspace_response(response, CHANNEL)

    assert (
        mtls_transport._decode_existing_response(frame, request, CHANNEL, PRINCIPAL)
        == response
    )


def test_collect_request_with_trailing_bytes_is_rejected() -> None:
    request = WorkspaceCollectRequest(
        REQUEST_ID,
        3,
        REQUEST_ID,
        2,
        ATTEMPT_ID,
        UNIT_ID,
        1,
        INCARNATION_ID,
    )
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_request(
            encode_workspace_request(request) + b"x", CHANNEL
        )


def test_lifecycle_response_binding_is_preserved() -> None:
    request = ReadyRequest(REQUEST_ID, 1)
    response = ReadyResponse(REQUEST_ID, True)
    frame = mtls_transport.encode_response(response)
    assert (
        mtls_transport._decode_existing_response(frame, request, None, PRINCIPAL)
        == response
    )


def test_local_identity_requires_absolute_paths_and_exact_principal() -> None:
    with pytest.raises(ValueError, match="local identity is invalid"):
        MtlsLocalIdentity(
            Path("ca.crt"),
            Path("/certificate.crt"),
            Path("/certificate.key"),
            URI,
            PRINCIPAL,
        )


def test_control_json_rejects_nonserializable_values() -> None:
    with pytest.raises(BrokerError):
        mtls_transport._control_frame({"invalid": object()})
    with pytest.raises(BrokerError):
        mtls_transport._control_frame({"invalid": "x" * 4097})


def test_response_header_digest_binds_exact_existing_frame() -> None:
    frame = encode_request(ReadyRequest(REQUEST_ID, 1))
    mapping = mtls_transport._response_mapping("a" * 64, REQUEST_ID, frame)
    assert mapping["frame_sha256"] == mtls_transport._sha256(frame)
    assert (
        json.loads(mtls_transport._control_frame(mapping)[4:].decode("ascii"))
        == mapping
    )


@pytest.mark.parametrize(
    ("parser", "value"),
    [
        (mtls_transport._exchange_id, "x" * 64),
        (mtls_transport._exchange_id, "a" * 63),
        (mtls_transport._uuid, 1),
        (mtls_transport._uuid, "not-a-uuid"),
        (mtls_transport._uuid, "A0000000-0000-4000-8000-000000000001"),
    ],
)
def test_control_identifiers_reject_noncanonical_values(
    parser: Any, value: Any
) -> None:
    with pytest.raises(BrokerError) as captured:
        parser(value)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize("value", [0, -1, 11, True, "1"])
def test_control_lengths_are_positive_bounded_exact_integers(value: Any) -> None:
    with pytest.raises(BrokerError):
        mtls_transport._positive_length(value, 10)


def test_deadline_and_bounded_io_fail_closed(mocker: MockerFixture) -> None:
    mocker.patch.object(mtls_transport, "monotonic", return_value=2.0)
    with pytest.raises(TimeoutError):
        mtls_transport._remaining(1.0)

    connection = mocker.Mock(spec=ssl.SSLSocket)
    connection.recv.side_effect = [b"a", b""]
    with pytest.raises(BrokerError):
        mtls_transport._receive_exact(connection, 2, 3.0)

    connection.recv.side_effect = None
    connection.send.return_value = 0
    with pytest.raises(OSError, match="no progress"):
        mtls_transport._send_all(connection, b"x", 3.0)


def test_authenticated_eof_rejects_trailing_application_data(
    mocker: MockerFixture,
) -> None:
    connection = mocker.Mock(spec=ssl.SSLSocket)
    connection.recv.return_value = b"x"
    with pytest.raises(BrokerError):
        mtls_transport._authenticated_eof(connection, float("inf"))
    connection.unwrap.assert_not_called()


def test_receive_control_rejects_oversized_prefix(mocker: MockerFixture) -> None:
    connection = mocker.Mock(spec=ssl.SSLSocket)
    connection.recv.return_value = (4097).to_bytes(4, "big")
    with pytest.raises(BrokerError):
        mtls_transport._receive_control(connection, float("inf"))


@pytest.mark.parametrize(
    "frame",
    [
        b"x",
        (10).to_bytes(4, "big") + b"{}",
    ],
)
def test_embedded_request_rejects_truncated_frames(frame: bytes) -> None:
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_request(frame, CHANNEL)


def test_workspace_frames_require_explicit_limits() -> None:
    request = _stage()
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_request(encode_workspace_request(request), None)
    response = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_response(
            encode_workspace_response(response, CHANNEL),
            request,
            None,
            PRINCIPAL,
        )


def test_workspace_response_rejects_truncated_or_extra_payload() -> None:
    request = _stage()
    response = WorkspaceStageReceipt(
        REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID, 1, INCARNATION_ID
    )
    frame = encode_workspace_response(response, CHANNEL)
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_response(
            (len(frame) + 1).to_bytes(4, "big") + frame[4:],
            request,
            CHANNEL,
            PRINCIPAL,
        )
    with pytest.raises(BrokerError):
        mtls_transport._decode_existing_response(
            frame + b"x", request, CHANNEL, PRINCIPAL
        )


def test_tls_context_rejects_missing_explicit_material() -> None:
    with pytest.raises(ValueError, match="certificate material is invalid"):
        mtls_transport._tls_context(LOCAL, server=False)


def test_preloaded_server_context_is_bound_to_declared_original_identity(
    mocker: MockerFixture,
) -> None:
    context = mocker.Mock(spec=ssl.SSLContext)
    mocker.patch.object(mtls_transport, "_tls_context", return_value=context)
    mocker.patch.object(
        mtls_transport.os,
        "stat",
        return_value=mocker.Mock(st_dev=1, st_ino=2),
    )
    loaded = replace(
        LOCAL,
        ca_certificate=Path("/proc/self/fd/10"),
        certificate_chain=Path("/proc/self/fd/11"),
        private_key=Path("/proc/self/fd/12"),
    )
    prepared = mtls_transport.build_mtls_server_context(loaded, declared_identity=LOCAL)

    assert prepared._local_identity == LOCAL
    assert "/proc/self/fd" not in repr(prepared._local_identity)
    with pytest.raises(ValueError, match="server configuration is invalid"):
        mtls_transport.MtlsBrokerServer(
            MtlsEndpoint("127.0.0.1", 0),
            local_identity=replace(LOCAL, private_key=Path("/different.key")),
            client_identity=PEER,
            dispatcher=mocker.Mock(spec=BrokerDispatcher),
            limits=MtlsTransportLimits(1, 1, 1, 1, 1, 1),
            server_context=prepared,
        )


def test_server_context_uses_the_direct_identity_when_no_alias_is_needed(
    mocker: MockerFixture,
) -> None:
    context = mocker.Mock(spec=ssl.SSLContext)
    mocker.patch.object(mtls_transport, "_tls_context", return_value=context)

    prepared = mtls_transport.build_mtls_server_context(LOCAL)

    assert prepared._local_identity == LOCAL
    assert prepared._context is context


def test_server_context_loads_an_immutable_material_snapshot(
    mocker: MockerFixture,
) -> None:
    context = mocker.Mock(spec=ssl.SSLContext)
    observed: tuple[bytes, bytes, bytes] | None = None

    def load_context(identity: MtlsLocalIdentity, *, server: bool) -> ssl.SSLContext:
        nonlocal observed
        assert server
        observed = (
            identity.ca_certificate.read_bytes(),
            identity.certificate_chain.read_bytes(),
            identity.private_key.read_bytes(),
        )
        return context

    mocker.patch.object(mtls_transport, "_tls_context", side_effect=load_context)
    material = (b"private CA", b"server certificate", b"private key")

    prepared = mtls_transport.build_mtls_server_context_from_material(LOCAL, material)

    assert observed == material
    assert prepared._local_identity == LOCAL
    assert prepared._context is context
    assert "private key" not in repr(prepared)


def test_server_preserves_stop_requested_before_start(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(mtls_transport, "_tls_context", return_value=mocker.Mock())
    dispatcher = mocker.Mock(spec=BrokerDispatcher)
    server = mtls_transport.MtlsBrokerServer(
        mtls_transport.MtlsEndpoint("127.0.0.1", 0),
        local_identity=LOCAL,
        client_identity=PEER,
        dispatcher=dispatcher,
        limits=mtls_transport.MtlsTransportLimits(1, 1, 1, 1, 1, 1),
    )

    assert server.endpoint == mtls_transport.MtlsEndpoint("127.0.0.1", 0)
    assert not server.stopping
    assert not server.failed
    with pytest.raises(ValueError, match="wait timeout is invalid"):
        server.wait_stopping(float("nan"))
    server.request_stop()
    server.start()
    server.stop()

    assert server.stopping
    dispatcher.start.assert_called_once_with()


def test_server_rejects_second_start(mocker: MockerFixture) -> None:
    server = _mock_server(mocker)
    server.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            server.start()
    finally:
        server.stop()


def test_mtls_server_releases_adopted_authority_lock_after_proven_stop(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _mock_server(mocker)
    descriptor = os.open(tmp_path / "authority.lock", os.O_RDWR | os.O_CREAT, 0o600)
    server._adopt_authority_lock(descriptor)
    with pytest.raises(ValueError, match="authority lock is invalid"):
        server._adopt_authority_lock(descriptor)

    server.stop()

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_mtls_server_retains_authority_lock_when_handlers_do_not_drain(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    server = _mock_server(mocker)
    descriptor = os.open(tmp_path / "authority.lock", os.O_RDWR | os.O_CREAT, 0o600)
    server._adopt_authority_lock(descriptor)
    server._listener = mocker.Mock(spec=socket.socket)
    server._threads.add(mocker.Mock())
    mocker.patch.object(mtls_transport, "monotonic", side_effect=[0.0, 2.0])

    with pytest.raises(RuntimeError, match="handlers did not drain"):
        server.stop()

    os.fstat(descriptor)
    os.close(descriptor)


def test_server_channel_handlers_reject_wrong_roles_and_digest(
    mocker: MockerFixture,
) -> None:
    server = _mock_server(mocker)
    connection = mocker.Mock(spec=ssl.SSLSocket)
    with pytest.raises(BrokerError):
        server._handle_response_channel(
            connection,
            {"channel": "request"},
            PRINCIPAL,
            PIN,
            float("inf"),
        )
    submit = mtls_transport._submit_mapping("a" * 64, REQUEST_ID, b"x")
    submit["channel"] = "response"
    with pytest.raises(BrokerError):
        server._handle_request_channel(connection, submit, PRINCIPAL, PIN, float("inf"))
    submit["channel"] = "request"
    submit["frame_sha256"] = "invalid"
    with pytest.raises(BrokerError):
        server._handle_request_channel(connection, submit, PRINCIPAL, PIN, float("inf"))


def test_duplicate_server_exchange_is_rejected_without_replacing_reservation(
    mocker: MockerFixture,
) -> None:
    server = _mock_server(mocker)
    exchange = "a" * 64
    existing = mtls_transport._Reservation(
        REQUEST_ID, PRINCIPAL, PIN, float("inf"), Event()
    )
    server._reservations[exchange] = existing
    mocker.patch.object(mtls_transport.secrets, "token_hex", return_value=exchange)

    server._handle_response_channel(
        mocker.Mock(spec=ssl.SSLSocket),
        mtls_transport._reservation_mapping(REQUEST_ID),
        PRINCIPAL,
        PIN,
        float("inf"),
    )

    assert server._reservations == {exchange: existing}


@pytest.mark.parametrize("failure", ["digest", "request-id"])
def test_request_payload_binding_is_checked_before_dispatch(
    mocker: MockerFixture, failure: str
) -> None:
    server = _mock_server(mocker)
    exchange = "a" * 64
    server._reservations[exchange] = mtls_transport._Reservation(
        REQUEST_ID, PRINCIPAL, PIN, float("inf"), Event()
    )
    embedded_id = (
        UUID("20000000-0000-4000-8000-000000000099")
        if failure == "request-id"
        else REQUEST_ID
    )
    frame = encode_request(ReadyRequest(embedded_id, 1))
    submit = mtls_transport._submit_mapping(exchange, REQUEST_ID, frame)
    if failure == "digest":
        submit["frame_sha256"] = f"sha256:{'0' * 64}"
    connection = mocker.Mock(spec=ssl.SSLSocket)
    connection.recv.side_effect = [frame, b""]
    connection.unwrap.return_value = mocker.Mock()

    with pytest.raises(BrokerError):
        server._handle_request_channel(connection, submit, PRINCIPAL, PIN, float("inf"))
    cast(Any, server._dispatcher.dispatch).assert_not_called()


def test_dispatch_gate_and_watchdog_expiry_are_fatal(
    mocker: MockerFixture,
) -> None:
    server = _mock_server(mocker)
    server._dispatch_gate.acquire()
    assert (
        server._dispatch(ReadyRequest(REQUEST_ID, 1), PRINCIPAL, monotonic() + 0.001)
        is None
    )
    server._dispatch_gate.release()
    assert server.failed

    other = _mock_server(mocker)
    other._watch_dispatch(Event(), monotonic() - 1)
    other._record_fatal(RuntimeError("ignored second detail"))
    assert other.failed

    waiting = _mock_server(mocker)
    waiting._watch_dispatch(Event(), monotonic() + 0.001)
    assert waiting.failed


def test_stage_client_rejects_non_stage_response(mocker: MockerFixture) -> None:
    client = object.__new__(mtls_transport.MtlsBrokerClient)
    mocker.patch.object(
        client,
        "_exchange",
        return_value=ReadyResponse(REQUEST_ID, True),
    )
    with pytest.raises(BrokerError):
        client.stage_workspace(_stage())


def test_client_requires_workspace_limits_before_transport(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(mtls_transport, "_tls_context", return_value=mocker.Mock())
    client = mtls_transport.MtlsBrokerClient(
        mtls_transport.MtlsEndpoint("127.0.0.1", 1),
        local_identity=LOCAL,
        server_identity=PEER,
        operation_timeout_seconds=1,
    )
    with pytest.raises(BrokerError) as captured:
        client.stage_workspace(_stage())
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize("kind", ["server", "client"])
def test_transport_constructors_reject_wrong_typed_configuration(
    mocker: MockerFixture, kind: str
) -> None:
    mocker.patch.object(mtls_transport, "_tls_context", return_value=mocker.Mock())
    if kind == "server":
        with pytest.raises(ValueError, match="server configuration is invalid"):
            mtls_transport.MtlsBrokerServer(
                cast(Any, "127.0.0.1:1"),
                local_identity=LOCAL,
                client_identity=PEER,
                dispatcher=mocker.Mock(spec=BrokerDispatcher),
                limits=mtls_transport.MtlsTransportLimits(1, 1, 1, 1, 1, 1),
            )
    else:
        with pytest.raises(ValueError, match="client configuration is invalid"):
            mtls_transport.MtlsBrokerClient(
                mtls_transport.MtlsEndpoint("127.0.0.1", 1),
                local_identity=LOCAL,
                server_identity=PEER,
                operation_timeout_seconds=cast(Any, True),
            )
