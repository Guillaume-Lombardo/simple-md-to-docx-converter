"""A broker subprocess handles termination delivered to its accept thread."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_CHILD = """
import os
import signal
import sys
from pathlib import Path
from threading import Thread
from uuid import uuid4

from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.process import BrokerProcess
from markweave.broker.unix_transport import UnixBrokerServer, UnixTransportLimits

root = Path(sys.argv[1])


class IdleDispatcher(BrokerDispatcher):
    def __init__(self):
        pass

    def start(self):
        pass


class SignalProbeServer(UnixBrokerServer):
    def start(self):
        super().start()
        accept_thread = self._accept_thread
        assert accept_thread is not None and accept_thread.ident is not None

        def target_accept_thread():
            assert sys.stdin is not None
            if sys.stdin.readline().strip() == 'stop':
                (root / 'signal-targeted').write_text(str(accept_thread.ident))
                signal.pthread_kill(accept_thread.ident, signal.SIGTERM)

        Thread(target=target_accept_thread, name='signal-probe', daemon=True).start()

    def wait_stopping(self, timeout=None):
        (root / 'main-waiting').touch()
        return super().wait_stopping(timeout)


server = SignalProbeServer(
    root / 'broker.sock',
    expected_client_uid=os.geteuid(),
    principal=AuthenticatedPrincipal(uuid4()),
    dispatcher=IdleDispatcher(),
    limits=UnixTransportLimits(1, 1, 2, 2),
)
raise SystemExit(BrokerProcess(server, hard_shutdown_timeout_seconds=1).run())
"""


def _await_path(path: Path, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            break
        time.sleep(0.02)
    raise AssertionError(f"Broker signal probe did not reach {path.name}")


def _await_main_futex_wait(process: subprocess.Popen[bytes]) -> None:
    """Prove the main thread entered Event.wait before targeting another thread."""

    wait_channel = Path(f"/proc/{process.pid}/task/{process.pid}/wchan")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process.poll() is None:
        try:
            if wait_channel.read_text().strip().startswith("futex"):
                return
        except OSError:
            pass
        time.sleep(0.02)
    raise AssertionError("Broker main thread did not enter its futex wait")


def test_one_sigterm_to_accept_thread_stops_idle_broker() -> None:
    with tempfile.TemporaryDirectory(prefix="mw-broker-signal-") as directory:
        root = Path(directory)
        process = subprocess.Popen(
            (sys.executable, "-u", "-c", _CHILD, str(root)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _await_path(root / "main-waiting", process)
            _await_main_futex_wait(process)
            assert (root / "broker.sock").exists()
            assert process.stdin is not None
            process.stdin.write(b"stop\n")
            process.stdin.flush()
            _await_path(root / "signal-targeted", process)
            assert process.wait(timeout=3) == 0
            assert not (root / "broker.sock").exists()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            stdout, stderr = process.communicate(timeout=3)
            assert stdout == b"" and stderr == b""
