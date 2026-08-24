"""Deterministic real TCP coverage for the clamd INSTREAM boundary."""

from __future__ import annotations

import socketserver
from threading import Thread

import pytest

from md_converter.malware import ClamAVUploadScanner


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
