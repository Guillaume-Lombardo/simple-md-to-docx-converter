"""Private HTTPS OpenAI-compatible provider for final-image Composer E2E."""

from __future__ import annotations

import json
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread

_KEY = "composer-e2e-write-only-secret"
_MODEL = "composer-e2e-model"
_SLOW_MODEL = "composer-e2e-slow-model"
_MAXIMUM_REQUEST_BYTES = 16_384
_lock = Lock()
_slow_started = Event()


class Handler(BaseHTTPRequestHandler):
    """Serve only the two operations exercised by the backend connection gate."""

    server_version = "ComposerE2EProvider"
    sys_version = ""

    def log_message(self, format: str, *arguments: object) -> None:
        # Request headers and bodies include credentials and never enter logs.
        del format, arguments

    def _send(self, status: int, body: object) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except BrokenPipeError, ConnectionResetError:
            # A cancelled model step closes its transport before this slow reply.
            pass

    def _record(self, operation: str, status: int) -> None:
        with _lock:
            print(json.dumps({"operation": operation, "status": status}), flush=True)

    def _authorized(self) -> bool:
        if isinstance(self.server, Server) and self.server.client_certificate_required:
            # TLS rejects absent or untrusted certificates before this handler runs.
            return True
        if self.headers.get("Authorization") != f"Bearer {_KEY}":
            self._record("authorization", 401)
            self._send(401, {"error": "Unauthorized"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path == "/e2e/status":
            self._send(
                200,
                {
                    "slow_started": _slow_started.is_set(),
                    "slow_calls": Server.slow_count,
                },
            )
            return
        if self.path != "/v1/models":
            self._send(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        self._record("models", 200)
        self._send(200, {"data": [{"id": _MODEL}, {"id": _SLOW_MODEL}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": "Not found"})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= _MAXIMUM_REQUEST_BYTES:
                raise ValueError
            payload = json.loads(self.rfile.read(length))
            if (
                payload["model"] not in {_MODEL, _SLOW_MODEL}
                or payload["stream"] is not False
                or not isinstance(payload["messages"], list)
            ):
                raise ValueError
        except KeyError, TypeError, ValueError, json.JSONDecodeError:
            self._record("chat", 400)
            self._send(400, {"error": "Invalid request"})
            return
        with _lock:
            Server.completion_count += 1
            attempt = Server.completion_count
        if payload["model"] == _SLOW_MODEL:
            with _lock:
                Server.slow_count += 1
            _slow_started.set()
            time.sleep(1.5)
        if attempt in {1, 3}:
            self._record("chat", 503)
            self._send(503, {"error": "Provider temporarily unavailable"})
            return
        self._record("chat", 200)
        self._send(200, {"choices": [{"message": {"content": "Connection healthy"}}]})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    completion_count = 0
    slow_count = 0

    def __init__(self, port: int, *, client_certificate_required: bool) -> None:
        self.client_certificate_required = client_certificate_required
        super().__init__(("0.0.0.0", port), Handler)  # noqa: S104 - private harness network


def main() -> None:
    servers: list[Server] = []
    for port, client_certificate_required in ((8443, False), (8444, True)):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain("/provider/server.crt", "/provider/server.key")
        if client_certificate_required:
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cafile="/provider/server.crt")
        server = Server(port, client_certificate_required=client_certificate_required)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        servers.append(server)
    Thread(
        target=servers[1].serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
    ).start()
    servers[0].serve_forever(poll_interval=0.2)


if __name__ == "__main__":
    main()
