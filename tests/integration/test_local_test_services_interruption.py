"""Every exit path of a local service stack keeps the ownership invariant.

INVARIANT: the owner ``flock`` is released and the lock file unlinked only when
no Docker command group the session started is alive and the stack's resources
were removed.

A fake ``docker`` executable stands in for the CLI and Compose plugin. Its
``compose up`` can start a "startup child" that writes a resource after a delay,
either sharing the command's output pipes or with its streams redirected, and
that optionally ignores SIGTERM so only SIGKILL stops it. Every fake process
records its pid, ``down`` logs whether the startup child was still alive, and
``ls`` reports the simulated resources. The single release point is wrapped so
that every release asserts the invariant at the moment it happens.
"""

import fcntl
import os
import signal
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

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
echo $$ >> "$FAKE_DOCKER_PIDS"
child() {
  sh -c '
    if [ "$FAKE_CHILD_IGNORES_TERM" = 1 ]; then trap "" TERM; fi
    echo $$ >> "$FAKE_DOCKER_PIDS"
    echo $$ > "$FAKE_DOCKER_CHILD"
    sleep "$FAKE_CHILD_DELAY"
    echo container >> "$FAKE_DOCKER_STATE"
  ' &
}
countdown() {
  n=$(cat "$1" 2>/dev/null || echo 0)
  [ "$n" -gt 0 ] || return 1
  echo $((n - 1)) > "$1"
}
case " $* " in
  *" compose "*" up "*)
    echo up >> "$FAKE_DOCKER_LOG"
    printf 'network\nvolume\n' >> "$FAKE_DOCKER_STATE"
    case "$FAKE_UP_MODE" in
      ok) ;;
      ok-detached) child </dev/null >/dev/null 2>&1 ;;
      fail-detached) child </dev/null >/dev/null 2>&1; exit 1 ;;
      block) child; sleep 30 ;;
    esac
    ;;
  *" compose "*" port "*)
    echo 127.0.0.1:15432
    ;;
  *" compose "*" down "*)
    if [ -s "$FAKE_DOCKER_CHILD" ] && kill -0 "$(cat "$FAKE_DOCKER_CHILD")" 2>/dev/null
    then
      echo down-while-startup-alive >> "$FAKE_DOCKER_LOG"
    else
      echo down >> "$FAKE_DOCKER_LOG"
    fi
    if countdown "$FAKE_DOCKER_DOWN_FAILS"; then exit 1; fi
    : > "$FAKE_DOCKER_STATE"
    # Compose reports success while an in-use network or volume remains.
    if countdown "$FAKE_DOCKER_DOWN_LEAVES"; then
      echo "$FAKE_DOWN_LEFTOVER" >> "$FAKE_DOCKER_STATE"
    fi
    ;;
  *" ls "*)
    case "$FAKE_LS_MODE" in
      always) echo "$3" ;;
      block) child; sleep 30 ;;
      *) grep -x "$3" "$FAKE_DOCKER_STATE" || true ;;
    esac
    ;;
esac
"""


def _running(pid: int) -> bool:
    """Return whether a process exists and is not a zombie awaiting reaping."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _lock_held(path: Path) -> bool:
    descriptor = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


@dataclass
class Fake:
    """Files of the fake engine and the releases observed during a scenario."""

    root: Path
    releases: list[str] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)

    def path(self, name: str) -> Path:
        return self.root / name

    def log(self) -> list[str]:
        return self.path("docker.log").read_text().split()

    def resources(self) -> str:
        return self.path("state").read_text()

    def live_pids(self) -> list[int]:
        pids = map(int, self.path("pids").read_text().split())
        return [pid for pid in pids if _running(pid)]


@pytest.fixture
def fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> Iterator[Fake]:
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "docker"
    executable.write_text(FAKE_DOCKER)
    executable.chmod(0o700)
    result = Fake(tmp_path)
    for name in ("docker.log", "state", "pids"):
        result.path(name).touch()
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    for variable, name in (
        ("FAKE_DOCKER_LOG", "docker.log"),
        ("FAKE_DOCKER_STATE", "state"),
        ("FAKE_DOCKER_PIDS", "pids"),
        ("FAKE_DOCKER_CHILD", "child"),
        ("FAKE_DOCKER_DOWN_FAILS", "down-fails"),
        ("FAKE_DOCKER_DOWN_LEAVES", "down-leaves"),
    ):
        monkeypatch.setenv(variable, str(result.path(name)))
    monkeypatch.setenv("FAKE_UP_MODE", "ok")
    monkeypatch.setenv("FAKE_LS_MODE", "state")
    monkeypatch.setenv("FAKE_CHILD_IGNORES_TERM", "0")
    monkeypatch.setenv("FAKE_CHILD_DELAY", "1")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(services, "STOP_GRACE_SECONDS", 1.0)
    mocker.patch.object(services, "prepare_bucket")
    close_lock = services._close_lock

    def checked_close(descriptor: int, path: Path, *, remove: bool) -> None:
        assert not result.live_pids(), "lock released while a command group lives"
        assert result.resources() == "", "lock released before resources removed"
        result.releases.append(path.name)
        close_lock(descriptor, path, remove=remove)

    mocker.patch.object(services, "_close_lock", side_effect=checked_close)
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    yield result
    result.stop.set()
    for thread in result.threads:
        thread.join(timeout=5)
    signal.signal(signal.SIGINT, previous)
    for process in services._UNSETTLED.values():
        services.stop_process_group(process)
    for held in services._HELD.values():
        os.close(held.descriptor)
    services._HELD.clear()
    services._CREATION_ATTEMPTED.clear()
    services._OWNED.clear()
    services._UNSETTLED.clear()


def _interrupt_after_child_start(fake: Fake, delays: tuple[float, ...]) -> None:
    """Send SIGINT after each delay, counted from the startup child's launch.

    The thread only waits on its own event, never on the ``time`` module, so it
    cannot consume values from a later test that patches ``time.monotonic``,
    and the fixture stops and joins it before the test ends.
    """
    if not delays:
        return

    def interrupt() -> None:
        for _ in range(3000):
            if fake.path("child").exists() or fake.stop.wait(0.01):
                break
        for delay in delays:
            if fake.stop.wait(delay):
                return
            os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=interrupt, daemon=True)
    fake.threads.append(thread)
    thread.start()


@dataclass(frozen=True)
class Scenario:
    environment: dict[str, str]
    expected: type[BaseException] | None
    released: bool
    released_after_exit_fallback: bool
    timeout: float | None = None
    interrupts: tuple[float, ...] = ()
    interrupt_before_teardown: bool = False
    down_failures: int = 0
    down_leaves: str | None = None
    unkillable_until_exit: bool = False


BLOCK = {"FAKE_UP_MODE": "block"}
BLOCK_IGNORING_TERM = {"FAKE_UP_MODE": "block", "FAKE_CHILD_IGNORES_TERM": "1"}
SCENARIOS = {
    "normal": Scenario({}, None, True, True),
    "normal-with-detached-descendant": Scenario(
        {"FAKE_UP_MODE": "ok-detached"}, None, True, True
    ),
    "nonzero-with-detached-descendant": Scenario(
        {"FAKE_UP_MODE": "fail-detached"}, RuntimeError, True, True
    ),
    "interrupt-during-up": Scenario(
        BLOCK, KeyboardInterrupt, True, True, interrupts=(0.2,)
    ),
    "second-interrupt": Scenario(
        BLOCK_IGNORING_TERM, KeyboardInterrupt, True, True, interrupts=(0.2, 0.3)
    ),
    "timeout": Scenario(BLOCK, RuntimeError, True, True, timeout=0.3),
    "interrupt-during-timeout-stop": Scenario(
        BLOCK_IGNORING_TERM,
        KeyboardInterrupt,
        True,
        True,
        timeout=0.3,
        interrupts=(0.6,),
    ),
    "interrupt-before-teardown": Scenario(
        BLOCK_IGNORING_TERM,
        KeyboardInterrupt,
        False,
        True,
        interrupts=(0.2,),
        interrupt_before_teardown=True,
    ),
    "settle-failure": Scenario(
        {**BLOCK, "FAKE_LS_MODE": "always"},
        RuntimeError,
        False,
        False,
        interrupts=(0.2,),
    ),
    "transient-removal-failure": Scenario({}, None, True, True, down_failures=1),
    "removal-failure-then-exit-fallback": Scenario(
        {}, RuntimeError, False, True, down_failures=services.REMOVAL_ATTEMPTS
    ),
    **{
        f"down-succeeds-but-leaves-{resource}": Scenario(
            {}, RuntimeError, False, True, down_leaves=resource
        )
        for resource in ("container", "network", "volume")
    },
    # A command group that survives every stop attempt before any stack
    # resource exists: only the live-group half of the invariant holds it.
    "surviving-group-before-startup": Scenario(
        {
            "FAKE_LS_MODE": "block",
            "FAKE_CHILD_IGNORES_TERM": "1",
            "FAKE_CHILD_DELAY": "30",
        },
        services.UnsettledCommandError,
        False,
        True,
        interrupts=(0.2,),
        unkillable_until_exit=True,
    ),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_exit_path_keeps_the_ownership_invariant(
    fake: Fake,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    name: str,
) -> None:
    scenario = SCENARIOS[name]
    for variable, value in scenario.environment.items():
        monkeypatch.setenv(variable, value)
    if scenario.timeout is not None:
        monkeypatch.setattr(services, "COMMAND_TIMEOUT_SECONDS", scenario.timeout)
    fake.path("down-fails").write_text(str(scenario.down_failures))
    if scenario.down_leaves is not None:
        monkeypatch.setenv("FAKE_DOWN_LEFTOVER", scenario.down_leaves)
        fake.path("down-leaves").write_text(str(services.REMOVAL_ATTEMPTS))
    restop = services.stop_unsettled_commands
    if scenario.interrupt_before_teardown:
        # A signal landing between ``finally:`` and the teardown's deferred
        # block skips the whole teardown; raising at its first step is the
        # same observable outcome.
        mocker.patch.object(
            services, "stop_unsettled_commands", side_effect=KeyboardInterrupt
        )
    stop = services.stop_process_group
    if scenario.unkillable_until_exit:
        mocker.patch.object(services, "stop_process_group", return_value=False)
    _interrupt_after_child_start(fake, scenario.interrupts)

    # A KeyboardInterrupt that escapes a test aborts the whole session, so the
    # outcome is captured and compared instead.
    outcome: BaseException | None = None
    try:
        with services.distributed_services():
            pass
    except BaseException as error:
        outcome = error
    if scenario.expected is None:
        assert outcome is None, repr(outcome)
    else:
        assert isinstance(outcome, scenario.expected), repr(outcome)
    mocker.patch.object(services, "stop_unsettled_commands", side_effect=restop)
    if scenario.unkillable_until_exit:
        mocker.patch.object(services, "stop_process_group", side_effect=stop)
        monkeypatch.setenv("FAKE_LS_MODE", "state")

    locks = list(services.lock_directory().iterdir())
    assert bool(fake.releases) is scenario.released
    if not scenario.released:
        (lock,) = locks
        assert _lock_held(lock), "ownership released early"
        services.remove_owned_stacks()
    assert bool(fake.releases) is scenario.released_after_exit_fallback
    if scenario.released_after_exit_fallback:
        assert not list(services.lock_directory().iterdir())
        assert "down-while-startup-alive" not in fake.log()
        # A surviving startup child would have written its resource by now.
        time.sleep(1.5)
        assert fake.resources() == ""
        assert not fake.live_pids()
    else:
        (lock,) = locks
        assert _lock_held(lock)
