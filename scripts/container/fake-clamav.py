"""Content-free ClamAV INSTREAM responder used only by container smoke tests."""

from __future__ import annotations

import os
import signal
import socketserver
import struct
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import FrameType

MAX_STREAM_BYTES = 2_000_000
EICAR_TEST_MARKER = (
    rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


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
        tail = b""
        infected = False
        reject_eicar = os.environ.get("MARKWEAVE_TEST_CLAMAV_REJECT_EICAR") == "true"
        while True:
            size_bytes = self._read_exact(4)
            if size_bytes is None:
                return
            size = struct.unpack("!I", size_bytes)[0]
            if size == 0:
                break
            total += size
            if total > MAX_STREAM_BYTES:
                return
            chunk = self._read_exact(size)
            if chunk is None:
                return
            if reject_eicar:
                window = tail + chunk
                infected = infected or EICAR_TEST_MARKER in window
                tail = window[-(len(EICAR_TEST_MARKER) - 1) :]
        self.request.sendall(
            b"stream: Eicar-Test-Signature FOUND\0" if infected else b"stream: OK\0"
        )

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


def stop_on_sigterm(server: Server) -> None:
    """Stop serving promptly when the container runtime sends SIGTERM.

    The responder runs as PID 1, which has no default SIGTERM action, so without
    this handler every ``podman stop`` waits for its grace period and then
    resorts to SIGKILL. ``shutdown`` blocks until ``serve_forever`` returns, so it
    must run outside the main thread that executes the signal handler.
    """

    def handle(_signum: int, _frame: FrameType | None) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, handle)


if __name__ == "__main__":
    with Server(("0.0.0.0", 3310), Handler) as server:  # noqa: S104 - test sidecar
        stop_on_sigterm(server)
        server.serve_forever()
