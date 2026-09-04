"""Content-free ClamAV INSTREAM responder used only by container smoke tests."""

from __future__ import annotations

import socketserver
import struct

MAX_STREAM_BYTES = 2_000_000


class Handler(socketserver.BaseRequestHandler):
    """Answer health probes or consume one bounded INSTREAM request."""

    def handle(self) -> None:
        command = self._read_command()
        if command == b"zPING\0":
            self.request.sendall(b"PONG\0")
            return
        if command != b"zINSTREAM\0":
            return
        total = 0
        while True:
            size_bytes = self._read_exact(4)
            if size_bytes is None:
                return
            size = struct.unpack("!I", size_bytes)[0]
            if size == 0:
                break
            total += size
            if total > MAX_STREAM_BYTES or self._read_exact(size) is None:
                return
        self.request.sendall(b"stream: OK\0")

    def _read_command(self) -> bytes | None:
        command = bytearray()
        while len(command) < len(b"zINSTREAM\0"):
            byte = self._read_exact(1)
            if byte is None:
                return None
            command.extend(byte)
            if byte == b"\0":
                return bytes(command)
        return bytes(command)

    def _read_exact(self, size: int) -> bytes | None:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.request.recv(size - len(chunks))
            if not chunk:
                return None
            chunks.extend(chunk)
        return bytes(chunks)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 64


if __name__ == "__main__":
    with Server(("0.0.0.0", 3310), Handler) as server:  # noqa: S104 - test sidecar
        server.serve_forever()
