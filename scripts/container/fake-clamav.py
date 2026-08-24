"""Content-free ClamAV INSTREAM responder used only by container smoke tests."""

from __future__ import annotations

import socketserver
import struct

MAX_STREAM_BYTES = 2_000_000


class Handler(socketserver.BaseRequestHandler):
    """Consume one bounded INSTREAM request and return a clean verdict."""

    def handle(self) -> None:
        command = self._read_exact(10)
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


if __name__ == "__main__":
    with Server(("0.0.0.0", 3310), Handler) as server:  # noqa: S104 - test sidecar
        server.serve_forever()
