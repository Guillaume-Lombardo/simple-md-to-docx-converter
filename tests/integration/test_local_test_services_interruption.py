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
    sh -c '
      if [ "$FAKE_CHILD_IGNORES_TERM" = 1 ]; then trap "" TERM; fi
      echo $$ > "$FAKE_DOCKER_CHILD"
      sleep "$FAKE_CHILD_DELAY"
      echo resource >> "$FAKE_DOCKER_STATE"
    ' &
    sleep 30
    ;;
  *" compose "*" down "*)
    if kill -0 "$(cat "$FAKE_DOCKER_CHILD" 2>/dev/null)" 2>/dev/null; then
      echo down-while-startup-alive >> "$FAKE_DOCKER_LOG"
    else
      echo down >> "$FAKE_DOCKER_LOG"
    fi
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
) -> Iterator[tuple[Path, Path, Path]]:
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text(FAKE_DOCKER)
    executable.chmod(0o700)
    log, state, child = tmp_path / "docker.log", tmp_path / "state", tmp_path / "pid"
    log.touch()
    state.touch()
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state))
    monkeypatch.setenv("FAKE_DOCKER_CHILD", str(child))
    monkeypatch.setenv("FAKE_CHILD_IGNORES_TERM", "0")
    monkeypatch.setenv("FAKE_CHILD_DELAY", "1")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield log, state, child
    signal.signal(signal.SIGINT, previous)
    services._OWNED.clear()
    services._UNSETTLED.clear()


def _interrupt_after_startup(child: Path, *delays: float) -> threading.Thread:
    """Send SIGINT after each delay, counted from the startup child's launch."""

    def interrupt() -> None:
        deadline = time.monotonic() + 30
        while not child.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        for delay in delays:
            time.sleep(delay)
            os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt, daemon=True)
    thread.start()
    return thread


def _assert_settled(log: Path, state: Path) -> None:
    # Removal ran only after the startup child was gone.
    assert log.read_text().split() == ["up", "down"]
    # The startup child would have written its resource by now.
    time.sleep(2.5)
    assert state.read_text() == ""
    assert not services._OWNED
    assert not services._UNSETTLED
    assert not list(services.lock_directory().iterdir())


@pytest.mark.parametrize("interruption", ["sigint", "timeout"])
def test_interrupted_startup_leaves_no_late_resource(
    fake_docker: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    log, state, child = fake_docker
    if interruption == "sigint":
        thread = _interrupt_after_startup(child, 0.2)
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
    _assert_settled(log, state)


@pytest.mark.parametrize("trigger", ["second-interrupt", "interrupt-after-timeout"])
def test_interrupt_during_group_termination_waits_for_proven_death(
    fake_docker: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
) -> None:
    """A startup child that ignores SIGTERM needs SIGKILL after the grace period.

    An interrupt that arrives while the group is being stopped must neither
    abort the stop nor let teardown release ownership while the child lives.
    """
    log, state, child = fake_docker
    monkeypatch.setenv("FAKE_CHILD_IGNORES_TERM", "1")
    monkeypatch.setenv("FAKE_CHILD_DELAY", "1.5")
    monkeypatch.setattr(services, "STOP_GRACE_SECONDS", 1.0)
    if trigger == "second-interrupt":
        thread = _interrupt_after_startup(child, 0.2, 0.3)
    else:
        monkeypatch.setattr(services, "COMMAND_TIMEOUT_SECONDS", 0.3)
        thread = _interrupt_after_startup(child, 0.6)
    with pytest.raises(KeyboardInterrupt), services.distributed_services():
        pytest.fail("startup must not complete")
    thread.join(timeout=5)
    _assert_settled(log, state)
