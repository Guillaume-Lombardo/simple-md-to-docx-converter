"""Real loopback HTTP coverage for the CLI authentication transport."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import ClassVar

import pytest

from markweave.cli.http import HttpTransport
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.integration


class _AuthenticationHandler(BaseHTTPRequestHandler):
    """Minimal remote contract server without sharing an ASGI transport."""

    received: ClassVar[list[tuple[str, str | None, str | None, dict[str, str]]]] = []

    def do_GET(self) -> None:
        if (
            self.path == "/api/v1/session"
            and self.headers.get("Cookie") == "session=opaque"
        ):
            self._send(
                200,
                {"username": "alice", "role": "user", "password_change_required": True},
            )
            return
        self._send(
            401, {"error": {"code": "SESSION_INVALID", "message": "Sign in again."}}
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length)) if length else {}
        self.received.append(
            (
                self.path,
                self.headers.get("Cookie"),
                self.headers.get("X-CSRF-Token"),
                payload,
            )
        )
        if self.path == "/api/v1/login":
            self._send(
                200,
                {
                    "csrf_token": "csrf-opaque",
                    "user": {"username": "alice", "password_change_required": True},
                },
                cookie="session=opaque; HttpOnly; Secure; SameSite=Lax; Path=/",
            )
            return
        if (
            self.path == "/api/v1/password"
            and self.headers.get("X-CSRF-Token") == "csrf-opaque"
        ):
            self.send_response(204)
            self.end_headers()
            return
        self._send(
            403,
            {"error": {"code": "CSRF_INVALID", "message": "Invalid CSRF token."}},
        )

    def _send(
        self, status: int, body: dict[str, object], *, cookie: str | None = None
    ) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def auth_server():
    """Run an actual loopback HTTP server for transport boundary verification."""
    _AuthenticationHandler.received.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AuthenticationHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_transport_preserves_session_cookie_and_csrf_over_real_http(
    auth_server: str,
) -> None:
    """The standard-library boundary serializes the documented HTTP contract exactly."""
    transport = HttpTransport(
        auth_server, verify_tls=False, timeout=2, session_cookie_name="session"
    )
    login = transport.login("alice", "not-persisted")
    assert login.status == 200
    assert login.session == "session=opaque"
    assert login.payload is not None
    csrf = login.payload["csrf_token"]
    assert isinstance(csrf, str)
    profile = ConnectionProfile(
        name="default",
        service_url=auth_server,
        session_state=login.session,
        csrf_state=csrf,
    )
    session = transport.session(profile)
    changed = transport.change_password(profile, "new-password", "new-password")

    assert session.status == 200
    assert changed.status == 204
    assert _AuthenticationHandler.received == [
        (
            "/api/v1/login",
            None,
            None,
            {"username": "alice", "password": "not-persisted"},
        ),
        (
            "/api/v1/password",
            "session=opaque",
            "csrf-opaque",
            {"password": "new-password", "confirmation": "new-password"},
        ),
    ]


def test_loopback_transport_bypasses_configured_proxies(
    auth_server: str, monkeypatch
) -> None:
    """Loopback evaluation login never sends credentials through ambient proxies."""
    proxy_requests: list[bytes] = []

    class ProxySink(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            proxy_requests.append(self.rfile.read(length))
            self.send_response(502)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxySink)
    proxy_thread = Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        proxy_url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("http_proxy", proxy_url)
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("no_proxy", "")
        monkeypatch.setenv("NO_PROXY", "")

        response = HttpTransport(
            auth_server, verify_tls=False, timeout=2, session_cookie_name="session"
        ).login("alice", "not-persisted")

        assert response.status == 200
        assert response.session == "session=opaque"
        assert _AuthenticationHandler.received == [
            (
                "/api/v1/login",
                None,
                None,
                {"username": "alice", "password": "not-persisted"},
            )
        ]
        assert proxy_requests == []
    finally:
        proxy.shutdown()
        proxy_thread.join()
        proxy.server_close()


def test_redirect_never_forwards_session_or_csrf_to_another_server() -> None:
    """A hostile redirect cannot exfiltrate authenticated headers across origins."""
    sink_requests: list[tuple[str | None, str | None]] = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            sink_requests.append(
                (self.headers.get("Cookie"), self.headers.get("X-CSRF-Token"))
            )
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    sink_thread = Thread(target=sink.serve_forever, daemon=True)
    sink_thread.start()

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/steal")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    source_thread = Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        profile = ConnectionProfile(
            name="default",
            service_url=f"http://127.0.0.1:{source.server_port}",
            session_state="session=opaque",
            csrf_state="csrf-opaque",
        )
        response = HttpTransport(
            profile.service_url,
            verify_tls=False,
            timeout=2,
            session_cookie_name="session",
        ).logout(profile)
        assert response.status == 302
        assert sink_requests == []
    finally:
        source.shutdown()
        source_thread.join()
        source.server_close()
        sink.shutdown()
        sink_thread.join()
        sink.server_close()
