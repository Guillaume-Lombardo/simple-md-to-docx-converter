"""Real HTTPS, certificate, mTLS, redirect, and response-boundary checks."""

from __future__ import annotations

import json
import ssl
import time
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from ipaddress import ip_network
from pathlib import Path
from threading import Event, Thread
from typing import cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from markweave.composer.connections import AllowedDestination, ConnectionPolicy
from markweave.composer.egress import (
    ConnectionEgress,
    EgressCancelledError,
    EgressPolicyError,
    EgressResponseError,
    EgressUnavailableError,
)
from markweave.composer.secrets import PlainCredentials

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


class ProviderServer(HTTPServer):
    mode = "ok"
    received_authorization: str | None = None
    delayed_request_started: Event
    release_delayed_response: Event


class ProviderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: PLR0911
        server = cast(ProviderServer, self.server)
        server.received_authorization = self.headers.get("Authorization")
        if server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://unexpected.example/models")
            self.end_headers()
            return
        if server.mode == "oversize":
            self._send(b"x" * 5000)
            return
        if server.mode == "invalid":
            self._send(b"not json")
            return
        if server.mode == "huge_length":
            self.send_response(200)
            self.send_header("Content-Length", "9" * 5000)
            self.end_headers()
            return
        if server.mode == "provider_error":
            self.send_response(503)
            self.end_headers()
            return
        if server.mode == "slow":
            time.sleep(0.2)
            with suppress(OSError):
                self._send(b'{"data":[]}')
            return
        if server.mode == "no_length_oversize":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 5000)
            return
        if server.mode == "invalid_length":
            self.send_response(200)
            self.send_header("Content-Length", "not-a-number")
            self.end_headers()
            return
        if server.mode == "bad_models":
            self._send(b'{"data":[{"id":"bad model"}]}')
            return
        self._send(json.dumps({"data": [{"id": "model-1"}]}).encode())

    def do_POST(self) -> None:
        server = cast(ProviderServer, self.server)
        server.received_authorization = self.headers.get("Authorization")
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        assert request["model"] == "model-1"
        assert request["max_tokens"] == 2
        if server.mode == "delayed_chat":
            server.delayed_request_started.set()
            if not server.release_delayed_response.wait(timeout=2):
                return
            with suppress(OSError):
                self._send(b'{"choices":[{"message":{"content":"Too late"}}]}')
            return
        self._send(b'{"choices":[{"message":{"content":"OK"}}]}')

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _certificate(root: Path) -> tuple[Path, Path, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    cert_path = root / "server.crt"
    key_path = root / "server.key"
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)
    return cert_path, key_path, cert_pem


@pytest.fixture
def provider(tmp_path: Path) -> Iterator[tuple[ProviderServer, bytes, bytes]]:
    cert_path, key_path, cert_pem = _certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=cert_pem.decode("ascii"))
    server = ProviderServer(("127.0.0.1", 0), ProviderHandler)
    server.delayed_request_started = Event()
    server.release_delayed_response = Event()
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, cert_pem, key_path.read_bytes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _egress(
    server: ProviderServer, *, allow_loopback: bool = True, timeout: float = 2
) -> ConnectionEgress:
    port = server.server_port
    policy = ConnectionPolicy(
        destinations=frozenset({AllowedDestination("localhost", port)}),
        allowed_networks=(ip_network("127.0.0.0/8"),) if allow_loopback else (),
        maximum_request_bytes=1024,
        maximum_response_bytes=1024,
        maximum_models=10,
        maximum_model_name_length=100,
        maximum_credential_bytes=4096,
        maximum_output_tokens=8,
        maximum_concurrent_calls=1,
        maximum_allowed_users=10,
        timeout_seconds=timeout,
    )
    return ConnectionEgress(policy, lambda _host, _port: ("127.0.0.1",))


def _credentials(cert: bytes, key: bytes) -> PlainCredentials:
    return PlainCredentials(
        api_key=b"private",
        client_certificate=cert,
        client_private_key=key,
        ca_bundle=cert,
    )


def test_real_https_mtls_models_and_chat(
    provider: tuple[ProviderServer, bytes, bytes],
) -> None:
    server, cert, key = provider
    egress = _egress(server)
    endpoint = f"https://localhost:{server.server_port}/v1"
    credentials = _credentials(cert, key)
    assert egress.discover_models(endpoint, credentials) == ("model-1",)
    assert server.received_authorization == "Bearer private"
    assert egress.chat(
        endpoint,
        credentials,
        model="model-1",
        messages=[{"role": "user", "content": "Hi"}],
        max_output_tokens=2,
    )["choices"]


def test_real_https_mtls_without_api_key_omits_authorization_header(
    provider: tuple[ProviderServer, bytes, bytes],
) -> None:
    server, cert, key = provider
    endpoint = f"https://localhost:{server.server_port}/v1"
    credentials = PlainCredentials(
        client_certificate=cert,
        client_private_key=key,
        ca_bundle=cert,
    )
    assert _egress(server).discover_models(endpoint, credentials) == ("model-1",)
    assert server.received_authorization is None


def test_cancel_delayed_https_chat_releases_slot_without_publishing_result(
    provider: tuple[ProviderServer, bytes, bytes],
) -> None:
    server, cert, key = provider
    server.mode = "delayed_chat"
    egress = _egress(server)
    endpoint = f"https://localhost:{server.server_port}/v1"
    credentials = _credentials(cert, key)
    cancelled = Event()
    outcomes: list[object] = []

    def call() -> None:
        try:
            outcomes.append(
                egress.chat(
                    endpoint,
                    credentials,
                    model="model-1",
                    messages=[{"role": "user", "content": "Hi"}],
                    max_output_tokens=2,
                    cancel_event=cancelled,
                )
            )
        except Exception as error:
            outcomes.append(error)

    worker = Thread(target=call)
    worker.start()
    try:
        assert server.delayed_request_started.wait(timeout=2)
        cancelled.set()
        worker.join(timeout=1)
        assert not worker.is_alive(), "Cancellation did not release the prompt slot"
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], EgressCancelledError)
    finally:
        server.release_delayed_response.set()
        worker.join(timeout=2)
    server.mode = "ok"
    assert egress.chat(
        endpoint,
        credentials,
        model="model-1",
        messages=[{"role": "user", "content": "Next"}],
        max_output_tokens=2,
    )["choices"]


@pytest.mark.parametrize(
    "mode", ["redirect", "oversize", "invalid", "huge_length", "bad_models"]
)
def test_invalid_provider_responses_are_rejected(
    provider: tuple[ProviderServer, bytes, bytes], mode: str
) -> None:
    server, cert, key = provider
    server.mode = mode
    with pytest.raises(EgressResponseError):
        _egress(server).discover_models(
            f"https://localhost:{server.server_port}/v1", _credentials(cert, key)
        )


def test_untrusted_tls_or_disallowed_address_fails_closed(
    provider: tuple[ProviderServer, bytes, bytes],
) -> None:
    server, cert, key = provider
    endpoint = f"https://localhost:{server.server_port}/v1"
    with pytest.raises(EgressUnavailableError):
        _egress(server).discover_models(
            endpoint,
            PlainCredentials(
                api_key=b"private", client_certificate=cert, client_private_key=key
            ),
        )
    with pytest.raises(EgressPolicyError):
        _egress(server, allow_loopback=False).discover_models(
            endpoint, _credentials(cert, key)
        )


def test_provider_error_is_safe_unavailable(
    provider: tuple[ProviderServer, bytes, bytes],
) -> None:
    server, cert, key = provider
    server.mode = "provider_error"
    with pytest.raises(EgressUnavailableError, match="Model provider is unavailable"):
        _egress(server).discover_models(
            f"https://localhost:{server.server_port}/v1", _credentials(cert, key)
        )


@pytest.mark.parametrize("mode", ["slow", "no_length_oversize", "invalid_length"])
def test_deadline_and_streaming_size_enforced(
    provider: tuple[ProviderServer, bytes, bytes], mode: str
) -> None:
    server, cert, key = provider
    server.mode = mode
    egress = _egress(server, timeout=0.05 if mode == "slow" else 2)
    error_type = EgressUnavailableError if mode == "slow" else EgressResponseError
    with pytest.raises(error_type):
        egress.discover_models(
            f"https://localhost:{server.server_port}/v1", _credentials(cert, key)
        )
