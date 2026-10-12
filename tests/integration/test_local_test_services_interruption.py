"""Interrupted service startup stops the whole Compose process group.

A fake ``docker`` executable stands in for the CLI. Its ``compose up`` starts a
child in the same process group, like the real Compose plugin, which creates a
"resource" one second later. Killing only the direct CLI process would let that
child create the resource after teardown had already removed the stack.
"""

import os
import signal
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.testing import services

pytestmark = [
    pytest.mark.integration,
    pytest.mark.light_coverage,
    pytest.mark.container_runtime_guard_exempt(
        reason="runs a fake docker shell script, never a container runtime"
    ),
]

FAKE_DOCKER = """\
#!/bin/sh
case " $* " in
  *" compose "*" up "*)
    echo up >> "$FAKE_DOCKER_LOG"
    ( sleep 1; echo resource >> "$FAKE_DOCKER_STATE" ) &
    sleep 30
    ;;
  *" compose "*" down "*)
    echo down >> "$FAKE_DOCKER_LOG"
    : > "$FAKE_DOCKER_STATE"
    ;;
  *" ls "*)
    cat "$FAKE_DOCKER_STATE"
    ;;
esac
"""


@pytest.fixture
def fake_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Path, Path]]:
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text(FAKE_DOCKER)
    executable.chmod(0o700)
    log, state = tmp_path / "docker.log", tmp_path / "state"
    log.touch()
    state.touch()
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)
    yield log, state
    services._OWNED.clear()
    services._UNSETTLED.clear()


def _interrupt_once_up_started(log: Path) -> threading.Thread:
    def interrupt() -> None:
        deadline = time.monotonic() + 30
        while "up" not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("interruption", ["sigint", "timeout"])
def test_interrupted_startup_leaves_no_late_resource(
    fake_docker: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    log, state = fake_docker
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        if interruption == "sigint":
            thread = _interrupt_once_up_started(log)
            with pytest.raises(KeyboardInterrupt), services.distributed_services():
                pytest.fail("startup must not complete")
            thread.join(timeout=5)
        else:
            monkeypatch.setattr(services, "COMMAND_TIMEOUT_SECONDS", 0.3)
            with (
                pytest.raises(RuntimeError, match="Docker Compose"),
                services.distributed_services(),
            ):
                pytest.fail("startup must not complete")
    finally:
        signal.signal(signal.SIGINT, previous)
    assert log.read_text().split() == ["up", "down"]
    # The simulated plugin would have written its resource after one second.
    time.sleep(1.5)
    assert state.read_text() == ""
    assert not services._OWNED
    assert not list(services.lock_directory().iterdir())
