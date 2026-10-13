"""Serve the final-image E2E broker at admission limit one with a release window.

The final-image E2E broker is the trusted external component: it is host-native
and runs from the reviewed source tree, never from a final image. This launcher
leaves that broker unchanged. It derives a configuration whose mTLS admission
limits are one, delegates the server's connection, pending-exchange and handler
slots to wrappers that sleep for a fixed window before every release, records
every admission decision, and then runs the unmodified broker process entry
point.

A broker that frees a slot only after the step that completes the client's
exchange refuses the client's immediate next exchange inside that window. A
broker that frees its slots first admits it. The report proves both that no
admission was refused and that reconnections arrived inside the window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic, sleep
from typing import TYPE_CHECKING, Any, Final

from markweave.broker import mtls_transport
from markweave.broker import process as broker_process

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

REPORT_SCHEMA: Final = "t141-admission-window-v1"
CONFIGURATION_NAME: Final = "broker-admission-window.json"
# Each delivered exchange holds two windowed releases on each of its channels,
# so the window adds about four windows to the exchange, well inside the
# fixture's five-second transport deadline and its response reserve.
RELEASE_WINDOW_SECONDS: Final = 0.15
WINDOWED_SLOTS: Final = ("_connections", "_exchanges", "_handlers")
_ADMISSION_LIMITS: Final = ("max_handlers", "max_handshakes", "max_pending_exchanges")
_MAX_REPORT_BYTES: Final = 65_536


class AdmissionRecorder:
    """Persist content-free admission counters after every decision."""

    def __init__(
        self, path: Path, window_seconds: float, clock: Callable[[], float] = monotonic
    ) -> None:
        self._path = path
        self._window = window_seconds
        self._clock = clock
        self._lock = Lock()
        self._released_at: dict[str, float | None] = dict.fromkeys(WINDOWED_SLOTS)
        self._counts = {
            slot: {"acquired": 0, "refused": 0, "released": 0, "immediate": 0}
            for slot in WINDOWED_SLOTS
        }
        self._write()

    def acquired(self, slot: str, admitted: bool) -> None:
        """Record one admission decision and whether it followed a release closely."""

        with self._lock:
            counts = self._counts[slot]
            if not admitted:
                counts["refused"] += 1
            else:
                counts["acquired"] += 1
                released_at = self._released_at[slot]
                if (
                    released_at is not None
                    and self._clock() - released_at < self._window
                ):
                    counts["immediate"] += 1
            self._write()

    def released(self, slot: str) -> None:
        """Record one completed release."""

        with self._lock:
            self._counts[slot]["released"] += 1
            self._released_at[slot] = self._clock()
            self._write()

    def _write(self) -> None:
        payload = (
            json.dumps(
                {
                    "schema": REPORT_SCHEMA,
                    "window_seconds": self._window,
                    "slots": self._counts,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        if len(payload) > _MAX_REPORT_BYTES:
            raise RuntimeError("T141 admission report exceeds its byte limit")
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.fchmod(descriptor, 0o644)
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        os.replace(temporary, self._path)


class WindowedSlot:
    """Delegate one server slot, widening each release by a fixed window."""

    def __init__(
        self,
        slot: str,
        semaphore: BoundedSemaphore,
        recorder: AdmissionRecorder,
        window_seconds: float,
        pause: Callable[[float], None] = sleep,
    ) -> None:
        self._slot = slot
        self._semaphore = semaphore
        self._recorder = recorder
        self._window = window_seconds
        self._pause = pause

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        admitted = self._semaphore.acquire(blocking, timeout)
        self._recorder.acquired(self._slot, admitted)
        return admitted

    def release(self, n: int = 1) -> None:
        self._pause(self._window)
        self._semaphore.release(n)
        self._recorder.released(self._slot)


def write_configuration(root: Path) -> Path:
    """Derive the fixture broker configuration at admission limit one."""

    configuration = json.loads((root / "broker.json").read_text("utf-8"))
    transport = configuration.get("transport")
    if configuration.get("transport_kind") != "mtls" or not isinstance(transport, dict):
        raise RuntimeError("T141 admission window requires the mTLS fixture broker")
    for limit in _ADMISSION_LIMITS:
        transport[limit] = 1
    path = root / CONFIGURATION_NAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(
            descriptor,
            (
                json.dumps(configuration, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode(),
        )
    finally:
        os.close(descriptor)
    return path


def install_release_window(
    recorder: AdmissionRecorder, window_seconds: float = RELEASE_WINDOW_SECONDS
) -> Callable[[], None]:
    """Wrap the slots of every mTLS broker server built afterwards."""

    server_type = mtls_transport.MtlsBrokerServer
    original = server_type.__init__

    def initialize(
        server: mtls_transport.MtlsBrokerServer, *args: Any, **kwargs: Any
    ) -> None:
        original(server, *args, **kwargs)
        for slot in WINDOWED_SLOTS:
            setattr(
                server,
                slot,
                WindowedSlot(slot, getattr(server, slot), recorder, window_seconds),
            )

    server_type.__init__ = initialize

    def uninstall() -> None:
        server_type.__init__ = original

    return uninstall


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixture broker at admission limit one with a release window."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.root.is_absolute() or not args.report.is_absolute():
        parser.error("root and report must be absolute paths")
    configuration = write_configuration(args.root)
    recorder = AdmissionRecorder(args.report, RELEASE_WINDOW_SECONDS)
    install_release_window(recorder)
    return broker_process.main([str(configuration)])


if __name__ == "__main__":  # pragma: no cover - harness process boundary
    sys.exit(main())
