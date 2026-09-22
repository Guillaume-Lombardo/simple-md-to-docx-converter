"""The E2E scanner rejects only the explicit opt-in test marker, within bounds."""

import importlib
import struct
from typing import Any

import pytest

SCANNER = importlib.import_module("scripts.container.fake-clamav")


class MemorySocket:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.response = b""

    def recv(self, size: int) -> bytes:
        chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk

    def sendall(self, payload: bytes) -> None:
        self.response += payload


@pytest.mark.unit
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("split", [1, 17, 67])
def test_scanner_marker_across_stream_chunks_is_opt_in(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, split: int
) -> None:
    monkeypatch.setenv("MARKWEAVE_TEST_CLAMAV_REJECT_EICAR", str(enabled).lower())
    source = SCANNER.EICAR_TEST_MARKER
    chunks = (source[:split], source[split:])
    request: Any = MemorySocket(
        b"zINSTREAM\0"
        + b"".join(struct.pack("!I", len(chunk)) + chunk for chunk in chunks)
        + b"\0\0\0\0"
    )
    SCANNER.Handler(request, ("127.0.0.1", 1), None)
    expected = b"stream: Eicar-Test-Signature FOUND\0" if enabled else b"stream: OK\0"
    assert request.response == expected


@pytest.mark.unit
def test_scanner_still_bounds_payload_before_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKWEAVE_TEST_CLAMAV_REJECT_EICAR", "true")
    request: Any = MemorySocket(
        b"zINSTREAM\0" + struct.pack("!I", SCANNER.MAX_STREAM_BYTES + 1)
    )
    SCANNER.Handler(request, ("127.0.0.1", 1), None)
    assert request.response == b""
