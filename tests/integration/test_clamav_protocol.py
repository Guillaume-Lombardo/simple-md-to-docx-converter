"""Deterministic real TCP coverage for the clamd INSTREAM boundary."""

from __future__ import annotations

import runpy
import socket
import socketserver
from threading import Thread
from typing import cast

import pytest

from markweave.malware import ClamAVUploadScanner


class _ClamdHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        command = self.request.recv(len(b"zINSTREAM\0"))
        content = bytearray()
        while True:
            size = int.from_bytes(self._read_exact(4), "big")
            if size == 0:
                break
            content.extend(self._read_exact(size))
        assert isinstance(self.server, _TestServer)
        self.server.command = command
        self.server.content = bytes(content)
        self.request.sendall(b"stream: OK\0")

    def _read_exact(self, size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            result.extend(self.request.recv(size - len(result)))
        return bytes(result)


class _TestServer(socketserver.TCPServer):
    command = b""
    content = b""


@pytest.mark.integration
def test_clamav_adapter_speaks_real_framed_instream_protocol() -> None:
    with _TestServer(("127.0.0.1", 0), _ClamdHandler) as server:
        thread = Thread(target=server.handle_request)
        thread.start()
        address = server.server_address
        ClamAVUploadScanner(str(address[0]), int(address[1]), 2, chunk_bytes=3).scan(
            b"abcdefg"
        )
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert server.command == b"zINSTREAM\0"
        assert server.content == b"abcdefg"


@pytest.mark.integration
def test_container_fake_clamav_answers_health_and_scan_protocols() -> None:
    namespace = runpy.run_path("scripts/container/fake-clamav.py")
    server_type = cast(type[socketserver.ThreadingTCPServer], namespace["Server"])
    handler_type = cast(type[socketserver.BaseRequestHandler], namespace["Handler"])

    with server_type(("127.0.0.1", 0), handler_type) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            address = cast(tuple[str, int], server.server_address)
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"zPING\0")
                assert connection.recv(64) == b"PONG\0"
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"zUNKNOWN\0")
                assert connection.recv(64) == b""
            ClamAVUploadScanner(*address, 2, chunk_bytes=3).scan(b"abcdefg")
        finally:
            server.shutdown()
            thread.join(timeout=2)
        assert not thread.is_alive()
