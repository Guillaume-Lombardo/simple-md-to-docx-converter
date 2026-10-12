"""Lifecycle and isolation regressions for automatic distributed test services."""

import contextlib
import fcntl
import os
import signal
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_mock import MockerFixture, MockType

from scripts.testing import services

pytestmark = pytest.mark.unit

NOW = 1_800_000_000
DEFERRED = (signal.SIGINT, signal.SIGTERM)
PROJECT = "markweave-tests-" + "a" * 32
OTHER = "markweave-tests-" + "b" * 32


@pytest.fixture(autouse=True)
def clear_service_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(services.STALE_AFTER_VARIABLE, raising=False)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    yield
    for held in services._HELD.values():
        os.close(held.descriptor)
    services._HELD.clear()
    services._OWNED.clear()
    services._UNSETTLED.clear()


@pytest.fixture
def reaper(mocker: MockerFixture) -> MockType:
    """Keep unit lifecycle tests away from the real Docker listings."""
    mocker.patch.object(services, "project_resources", return_value=False)
    return mocker.patch.object(services, "reap_stale_stacks", return_value=())


def _locked_elsewhere(path: Path) -> bool:
    descriptor = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(descriptor)
    return False


def test_partial_configuration_never_launches_replacements(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv(services.VARIABLES[0], "configured-external-service")
    compose = mocker.patch.object(services, "_compose")
    reap = mocker.patch.object(services, "reap_stale_stacks")
    with (
        pytest.raises(RuntimeError, match="Incomplete distributed test configuration"),
        services.distributed_services(),
    ):
        pytest.fail("Partial configuration must fail before tests run")
    compose.assert_not_called()
    reap.assert_not_called()


def test_external_services_are_neither_started_nor_stopped(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    expected = dict.fromkeys(services.VARIABLES, "external")
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    compose = mocker.patch.object(services, "_compose")
    reap = mocker.patch.object(services, "reap_stale_stacks")
    with services.distributed_services():
        assert {name: os.environ[name] for name in expected} == expected
    compose.assert_not_called()
    reap.assert_not_called()


@pytest.mark.parametrize("failure", ["none", "startup", "port", "bucket", "test"])
def test_owned_services_cleanup_on_every_lifecycle_failure(
    failure: str,
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    calls: list[tuple[str, ...]] = []
    projects: list[str] = []

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        projects.append(project)
        calls.append(args)
        assert environment["MARKWEAVE_LOCAL_TEST_PASSWORD"]
        if args[0] == "up" and failure == "startup":
            raise RuntimeError("startup")
        if args[0] == "port":
            return "0.0.0.0:15432" if failure == "port" else "127.0.0.1:15432"
        return ""

    mocker.patch.object(services, "_compose", side_effect=compose)
    prepare = mocker.patch.object(services, "prepare_bucket")
    if failure == "bucket":
        prepare.side_effect = RuntimeError("bucket")

    def exercise() -> None:
        with services.distributed_services():
            assert all(os.environ[name] for name in services.VARIABLES)
            assert (
                os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"] == "http://127.0.0.1:15432"
            )
            assert os.environ["MARKWEAVE_TEST_S3_BUCKET"].startswith("test-")
            if failure == "test":
                raise RuntimeError("test failure")

    if failure == "none":
        exercise()
    else:
        with pytest.raises(RuntimeError):
            exercise()
    assert calls[-1] == ("down", "--volumes", "--remove-orphans", "--timeout", "10")
    assert len(set(projects)) == 1
    assert projects[0].startswith("markweave-tests-")
    assert not any(name in os.environ for name in services.VARIABLES)
    reaper.assert_called_once_with()
    assert not services._OWNED
    assert not list(services.lock_directory().iterdir())


def test_owned_stack_is_labelled_and_locked_for_its_whole_life(
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    """The owner lock is held before the reaper runs and until removal ends."""
    observed: list[str] = []

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        lock = Path(environment["MARKWEAVE_LOCAL_TEST_LOCK"])
        assert lock == services.lock_directory() / f"{project}.lock"
        assert _locked_elsewhere(lock)
        assert environment["MARKWEAVE_LOCAL_TEST_HOST"] == socket.gethostname()
        assert environment["MARKWEAVE_LOCAL_TEST_CREATED"].isdecimal()
        assert services._OWNED[project] is environment
        observed.append(args[0])
        return "127.0.0.1:15432" if args[0] == "port" else ""

    def reap() -> tuple[str, ...]:
        (lock,) = services.lock_directory().iterdir()
        assert _locked_elsewhere(lock)
        observed.append("reap")
        return ()

    reaper.side_effect = reap
    mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(services, "prepare_bucket")
    with services.distributed_services():
        pass
    assert observed == ["reap", "up", "port", "port", "down"]
    assert services.lock_directory().stat().st_mode & 0o777 == 0o700
    assert not list(services.lock_directory().iterdir())


def test_failed_removal_keeps_the_lock_held_until_the_exit_fallback(
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    del reaper

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        del project, environment
        if args[0] == "down":
            raise RuntimeError("down")
        return "127.0.0.1:15432" if args[0] == "port" else ""

    compose_mock = mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(services, "prepare_bucket")
    with pytest.raises(RuntimeError, match="down"), services.distributed_services():
        pass
    (lock,) = services.lock_directory().iterdir()
    assert _locked_elsewhere(lock)
    (project,) = services._OWNED

    compose_mock.side_effect = None
    compose_mock.return_value = ""
    services.remove_owned_stacks()
    assert compose_mock.call_args.args[0] == project
    assert compose_mock.call_args.args[2:] == services.DOWN_ARGUMENTS
    assert not services._OWNED
    assert not services._HELD
    assert not lock.exists()


def test_interpreter_exit_net_keeps_ownership_it_cannot_prove_removed(
    mocker: MockerFixture,
) -> None:
    services._OWNED.update({PROJECT: {}, OTHER: {}})
    compose = mocker.patch.object(
        services, "_compose", side_effect=[RuntimeError("down"), ""]
    )
    mocker.patch.object(services, "project_resources", return_value=False)
    services.remove_owned_stacks()
    assert [call.args[0] for call in compose.call_args_list] == [PROJECT, OTHER]
    assert list(services._OWNED) == [PROJECT]


def test_sigterm_while_owning_a_stack_still_removes_it(
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    del reaper
    compose = mocker.patch.object(
        services,
        "_compose",
        side_effect=lambda _p, _e, *args: (
            "127.0.0.1:15432" if args[0] == "port" else ""
        ),
    )
    mocker.patch.object(services, "prepare_bucket")
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        with pytest.raises(KeyboardInterrupt), services.distributed_services():
            assert signal.getsignal(signal.SIGTERM) is services._interrupt
            os.kill(os.getpid(), signal.SIGTERM)
            pytest.fail("SIGTERM must interrupt the session")
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert compose.call_args.args[2:] == services.DOWN_ARGUMENTS
    assert not services._OWNED


def test_existing_sigterm_handler_is_left_in_place(
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    del reaper
    mocker.patch.object(
        services,
        "_compose",
        side_effect=lambda _p, _e, *args: (
            "127.0.0.1:15432" if args[0] == "port" else ""
        ),
    )
    mocker.patch.object(services, "prepare_bucket")

    def handler(signum: int, frame: object) -> None:
        del signum, frame

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        with services.distributed_services():
            assert signal.getsignal(signal.SIGTERM) is handler
        assert signal.getsignal(signal.SIGTERM) is handler
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_interrupt_during_removal_waits_until_removal_is_recorded(
    mocker: MockerFixture,
    reaper: MockType,
) -> None:
    del reaper
    finished: list[str] = []

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        del project, environment
        if args[0] == "down":
            os.kill(os.getpid(), signal.SIGINT)
            finished.append("down")
        return "127.0.0.1:15432" if args[0] == "port" else ""

    mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(services, "prepare_bucket")
    handlers = {signum: signal.getsignal(signum) for signum in DEFERRED}
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with pytest.raises(KeyboardInterrupt), services.distributed_services():
            pass
    finally:
        signal.signal(signal.SIGINT, previous)
    assert finished == ["down"]
    assert not services._OWNED
    assert not list(services.lock_directory().iterdir())
    assert {signum: signal.getsignal(signum) for signum in DEFERRED} == handlers


def test_deferred_sigterm_reaches_the_restored_handler() -> None:
    """A SIGTERM during removal is raised again once removal has finished."""
    received: list[str] = []

    def handler(signum: int, frame: object) -> None:
        del signum, frame
        received.append("handled")

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        with services._deferred_interrupts():
            os.kill(os.getpid(), signal.SIGTERM)
            received.append("finished")
        assert received == ["finished", "handled"]
        assert signal.getsignal(signal.SIGTERM) is handler
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_handlers_installed_outside_python_are_left_alone(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(services.signal, "getsignal", return_value=None)
    install = mocker.spy(services.signal, "signal")
    with services._deferred_interrupts():
        pass
    install.assert_not_called()


def test_compose_interrupts_are_not_deferred_outside_the_main_thread(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(services.threading, "current_thread", return_value=object())
    install = mocker.spy(services.signal, "signal")
    with services._deferred_interrupts(), services._sigterm_as_interrupt():
        pass
    install.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        "0.0.0.0:5432",
        "::1:5432",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:123\n127.0.0.1:456",
    ],
)
def test_only_single_valid_loopback_bindings_are_accepted(value: str) -> None:
    with pytest.raises(RuntimeError):
        services._port(value)


def _popen(
    mocker: MockerFixture, *, returncode: int = 0, error: BaseException | None = None
) -> MockType:
    process = mocker.Mock(pid=4242, returncode=returncode)
    # An interrupted leader is still running; a completed one has exited.
    process.poll.return_value = returncode if error is None else None
    # A mocked leader must never make the code probe or signal a real group.
    mocker.patch.object(services, "_group_alive", return_value=False)
    if error is None:
        process.communicate.return_value = ("out\n", "private-diagnostic")
    else:
        process.communicate.side_effect = error
    return mocker.patch.object(services.subprocess, "Popen", return_value=process)


def test_compose_failure_does_not_echo_environment_or_diagnostics(
    mocker: MockerFixture,
) -> None:
    popen = _popen(mocker, returncode=1)
    with pytest.raises(RuntimeError, match="Compose up failed") as error:
        services._compose("markweave-tests-example", {}, "up")
    assert "private-diagnostic" not in str(error.value)
    process = popen.return_value
    assert process.communicate.call_args.kwargs["timeout"] == 300
    assert popen.call_args.kwargs["start_new_session"] is True
    assert "shell" not in popen.call_args.kwargs


def test_successful_command_returns_stripped_output(mocker: MockerFixture) -> None:
    _popen(mocker)
    assert services._compose("markweave-tests-example", {}, "port") == "out"


def test_missing_engine_reports_configuration_alternative(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(services.subprocess, "Popen", side_effect=FileNotFoundError)
    with pytest.raises(RuntimeError, match="Docker Compose"):
        services._compose("markweave-tests-example", {}, "up")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (subprocess.TimeoutExpired("docker", 300), RuntimeError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
    ids=["timeout", "interrupt"],
)
def test_interrupted_command_stops_its_whole_process_group(
    mocker: MockerFixture, error: BaseException, expected: type[BaseException]
) -> None:
    _popen(mocker, error=error)
    stop = mocker.patch.object(services, "stop_process_group", return_value=True)
    with pytest.raises(expected) as raised:
        services._compose("markweave-tests-example", {}, "up")
    assert not isinstance(raised.value, services.UnsettledCommandError)
    stop.assert_called_once()
    assert not services._UNSETTLED


def test_surviving_process_group_is_recorded_as_unsettled(
    mocker: MockerFixture,
) -> None:
    popen = _popen(mocker, error=KeyboardInterrupt())
    mocker.patch.object(services, "stop_process_group", return_value=False)
    alive = mocker.patch.object(services, "_group_alive", return_value=True)
    with pytest.raises(services.UnsettledCommandError, match="did not stop"):
        services._compose("markweave-tests-example", {}, "up")
    assert services.unsettled_commands()
    popen.return_value.poll.return_value = -9
    assert services.unsettled_commands()
    alive.return_value = False
    assert not services.unsettled_commands()


@pytest.mark.parametrize("returncode", [0, 1], ids=["success", "failure"])
def test_completed_command_with_a_live_descendant_is_stopped(
    mocker: MockerFixture, returncode: int
) -> None:
    """A finished leader proves nothing about descendants with other streams."""
    _popen(mocker, returncode=returncode)
    alive = mocker.patch.object(services, "_group_alive", return_value=True)
    stop = mocker.patch.object(services, "stop_process_group", return_value=False)
    with pytest.raises(services.UnsettledCommandError, match="did not stop"):
        services._compose("markweave-tests-example", {}, "up")
    stop.assert_called_once()
    assert 4242 in services._UNSETTLED
    stop.return_value = True
    alive.return_value = False
    services._UNSETTLED.clear()
    expectation = (
        pytest.raises(RuntimeError, match="failed")
        if returncode
        else contextlib.nullcontext()
    )
    alive.return_value = True
    with expectation:
        services._compose("markweave-tests-example", {}, "up")
    assert not services._UNSETTLED


def test_stop_process_group_escalates_and_reports_survivors(
    mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(services, "STOP_KILL_SECONDS", 0.05)
    killpg = mocker.patch.object(services.os, "killpg")
    process = mocker.Mock(pid=4242)
    process.poll.return_value = None
    assert services.stop_process_group(process) is False
    signals = [call.args[1] for call in killpg.call_args_list if call.args[1]]
    assert signals[0] == signal.SIGTERM
    assert signals[-1] == signal.SIGKILL


@pytest.mark.parametrize(
    ("failure", "alive"),
    [(None, True), (ProcessLookupError, False), (PermissionError, True)],
    ids=["alive", "gone", "foreign"],
)
def test_group_liveness_probe(
    mocker: MockerFixture, failure: type[OSError] | None, alive: bool
) -> None:
    mocker.patch.object(services.os, "killpg", side_effect=failure)
    assert services._group_alive(4242) is alive


def test_interrupted_startup_is_removed_until_the_engine_is_empty(
    mocker: MockerFixture, reaper: MockType
) -> None:
    """A late resource from an interrupted startup triggers another removal."""
    del reaper
    removals: list[str] = []

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        del project, environment
        if args[0] == "up":
            raise KeyboardInterrupt
        removals.append(args[0])
        return ""

    mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(services, "project_resources", side_effect=[True, False])
    with pytest.raises(KeyboardInterrupt), services.distributed_services():
        pass
    assert removals == ["down", "down"]
    assert not services._OWNED
    assert not list(services.lock_directory().iterdir())


@pytest.mark.parametrize("cause", ["resources", "survivor"])
def test_unsettled_startup_keeps_ownership_and_lock(
    mocker: MockerFixture, reaper: MockType, cause: str
) -> None:
    del reaper

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        del project, environment
        if args[0] == "up":
            raise KeyboardInterrupt
        return ""

    mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(
        services, "project_resources", return_value=cause == "resources"
    )
    mocker.patch.object(
        services, "unsettled_commands", return_value=cause == "survivor"
    )
    with (
        pytest.raises(RuntimeError, match="could not be settled"),
        services.distributed_services(),
    ):
        pass
    (lock,) = services.lock_directory().iterdir()
    assert _locked_elsewhere(lock)
    assert len(services._OWNED) == 1
    assert len(services._HELD) == 1


def test_project_resource_listing_filters_by_compose_project(
    mocker: MockerFixture,
) -> None:
    docker = mocker.patch.object(services, "_docker", side_effect=["", "volume", ""])
    assert services.project_resources(PROJECT) is True
    filters = {call.args[0][-1] for call in docker.call_args_list}
    assert filters == {f"label=com.docker.compose.project={PROJECT}"}


def test_lock_directory_falls_back_to_the_temporary_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(services.tempfile, "gettempdir", lambda: str(tmp_path))
    directory = services.lock_directory()
    assert directory == tmp_path / f"markweave-test-services-{os.getuid()}"
    assert directory.is_dir()


# Reaper ------------------------------------------------------------------


def _labels(
    project: str = PROJECT,
    *,
    lock: Path | str | None = None,
    created: int = NOW - 10,
    host: str | None = None,
) -> str:
    path = lock if lock is not None else services.lock_directory() / f"{project}.lock"
    return "\t".join((project, str(path), str(created), host or socket.gethostname()))


@pytest.fixture
def listing(mocker: MockerFixture) -> dict[str, list[str]]:
    """Serve container, volume and network listings from a mutable table."""
    table: dict[str, list[str]] = {"container": [], "volume": [], "network": []}

    def docker(arguments: tuple[str, ...], environment: object, action: str) -> str:
        del environment, action
        assert "--filter" in arguments
        assert arguments[arguments.index("--filter") + 1] == (
            "label=io.markweave.tests.stack=local-services"
        )
        return "\n".join(table[arguments[0]])

    mocker.patch.object(services, "_docker", side_effect=docker)
    return table


@pytest.fixture
def removals(mocker: MockerFixture) -> MockType:
    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        assert args == services.DOWN_ARGUMENTS
        assert environment["MARKWEAVE_LOCAL_TEST_CREATED"] == "0"
        return ""

    return mocker.patch.object(services, "_compose", side_effect=compose)


def _removed(removals: MockType) -> list[str]:
    return [call.args[0] for call in removals.call_args_list]


def test_reaper_removes_a_stack_whose_owner_released_its_lock(
    listing: dict[str, list[str]], removals: MockType
) -> None:
    lock = services.lock_directory() / f"{PROJECT}.lock"
    lock.touch()
    listing["volume"].append(_labels())
    assert services.reap_stale_stacks(now=NOW) == (PROJECT,)
    assert _removed(removals) == [PROJECT]
    assert not lock.exists()


def test_reaper_never_touches_a_stack_whose_owner_holds_its_lock(
    listing: dict[str, list[str]], removals: MockType
) -> None:
    lock = services.lock_directory() / f"{PROJECT}.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        listing["container"].append(_labels(created=0))
        assert services.reap_stale_stacks(now=NOW) == ()
    finally:
        os.close(descriptor)
    removals.assert_not_called()
    assert lock.exists()


@pytest.mark.parametrize(("age", "reaped"), [(3600, True), (3599, False)])
def test_reaper_needs_the_age_bound_when_the_lock_file_is_gone(
    listing: dict[str, list[str]], removals: MockType, age: int, reaped: bool
) -> None:
    listing["network"].append(_labels(created=NOW - age))
    assert services.reap_stale_stacks(now=NOW) == ((PROJECT,) if reaped else ())
    assert _removed(removals) == ([PROJECT] if reaped else [])


def test_reaper_age_bound_is_configurable_and_validated(
    listing: dict[str, list[str]],
    removals: MockType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing["network"].append(_labels(created=NOW - 60))
    monkeypatch.setenv(services.STALE_AFTER_VARIABLE, "60")
    assert services.reap_stale_stacks(now=NOW) == (PROJECT,)
    monkeypatch.setenv(services.STALE_AFTER_VARIABLE, "-1")
    with pytest.raises(RuntimeError, match="whole number"):
        services.reap_stale_stacks(now=NOW)
    assert _removed(removals) == [PROJECT]


@pytest.mark.parametrize(
    "line",
    [
        "markweave-tests-unexpected\t/l\t0\th",
        "\t".join(("markweave-tests-" + "a" * 32, "relative.lock", "0", "h")),
    ],
    ids=["foreign-name", "relative-lock"],
)
def test_reaper_ignores_malformed_ownership(
    listing: dict[str, list[str]], removals: MockType, line: str
) -> None:
    listing["container"].append(line)
    assert services.reap_stale_stacks(now=NOW) == ()
    removals.assert_not_called()


@pytest.mark.parametrize(
    "variant", ["other-host", "other-lock-name", "undecimal-created", "missing-label"]
)
def test_reaper_keeps_stacks_it_cannot_prove_abandoned(
    listing: dict[str, list[str]], removals: MockType, variant: str
) -> None:
    line = {
        "other-host": _labels(created=0, host="another-host"),
        "other-lock-name": _labels(
            created=0, lock=services.lock_directory() / f"{OTHER}.lock"
        ),
        "undecimal-created": _labels().replace(str(NOW - 10), "yesterday"),
        "missing-label": "\t".join(_labels(created=0).split("\t")[:3]),
    }[variant]
    listing["container"].append(line)
    assert services.reap_stale_stacks(now=NOW) == ()
    removals.assert_not_called()


def test_reaper_keeps_a_project_whose_resources_disagree(
    listing: dict[str, list[str]], removals: MockType
) -> None:
    (services.lock_directory() / f"{PROJECT}.lock").touch()
    listing["container"].append(_labels(created=0))
    listing["volume"].append(_labels(created=1))
    listing["network"].append(_labels(created=0))
    assert services.reap_stale_stacks(now=NOW) == ()
    removals.assert_not_called()


def test_reaper_skips_stacks_this_process_owns(
    listing: dict[str, list[str]], removals: MockType
) -> None:
    services._OWNED[PROJECT] = {}
    listing["container"].append(_labels(created=0))
    assert services.reap_stale_stacks(now=NOW) == ()
    removals.assert_not_called()


def test_reaper_skips_an_unreadable_lock(
    listing: dict[str, list[str]], removals: MockType, mocker: MockerFixture
) -> None:
    (services.lock_directory() / f"{PROJECT}.lock").touch()
    mocker.patch.object(services, "_open_lock", side_effect=PermissionError)
    listing["container"].append(_labels(created=0))
    assert services.reap_stale_stacks(now=NOW) == ()
    removals.assert_not_called()


def test_reaper_continues_after_a_failed_removal(
    listing: dict[str, list[str]], removals: MockType
) -> None:
    for project in (PROJECT, OTHER):
        (services.lock_directory() / f"{project}.lock").touch()
        listing["container"].append(_labels(project))
    removals.side_effect = [RuntimeError("down"), ""]
    assert services.reap_stale_stacks(now=NOW) == (OTHER,)
    assert _removed(removals) == [PROJECT, OTHER]
    assert (services.lock_directory() / f"{PROJECT}.lock").exists()
    assert not (services.lock_directory() / f"{OTHER}.lock").exists()


def test_interrupt_during_group_stop_waits_for_bookkeeping(
    mocker: MockerFixture,
) -> None:
    """A second interrupt cannot cut the stop or its record short."""
    popen = _popen(mocker, error=KeyboardInterrupt())
    order: list[str] = []

    def stop(process: object) -> bool:
        del process
        os.kill(os.getpid(), signal.SIGINT)
        order.append("stopped")
        return True

    mocker.patch.object(services, "stop_process_group", side_effect=stop)
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with pytest.raises(KeyboardInterrupt):
            services._compose("markweave-tests-example", {}, "up")
    finally:
        signal.signal(signal.SIGINT, previous)
    assert order == ["stopped"]
    popen.return_value.stdout.close.assert_called_once_with()
    assert not services._UNSETTLED


def test_command_group_is_recorded_until_its_death_is_proven(
    mocker: MockerFixture,
) -> None:
    observed: list[bool] = []
    popen = _popen(mocker)
    popen.return_value.communicate.side_effect = lambda **_: (
        observed.append(4242 in services._UNSETTLED) or ("", "")
    )
    services._compose("markweave-tests-example", {}, "port")
    assert observed == [True]
    assert not services._UNSETTLED


def test_survivors_are_stopped_before_removal_and_at_exit(
    mocker: MockerFixture, reaper: MockType
) -> None:
    del reaper
    order: list[str] = []
    survivor = mocker.Mock(pid=4343)
    survivor.poll.return_value = None
    services._UNSETTLED[4343] = survivor

    def stop(process: object) -> bool:
        order.append("stop")
        return True

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        del project, environment
        order.append(args[0])
        return "127.0.0.1:15432" if args[0] == "port" else ""

    mocker.patch.object(services, "stop_process_group", side_effect=stop)
    mocker.patch.object(services, "_compose", side_effect=compose)
    mocker.patch.object(services, "prepare_bucket")
    with services.distributed_services():
        pass
    assert order == ["up", "port", "port", "stop", "down"]
    assert not services._UNSETTLED

    services._UNSETTLED[4343] = survivor
    services._OWNED[PROJECT] = {}
    order.clear()
    services.remove_owned_stacks()
    assert order == ["stop", "down"]


RECORDED_GROUP_CASES = {
    "live-group-young": (True, 10, False),
    "live-group-stale": (True, 3600, True),
    "dead-group": (False, 10, True),
}


@pytest.mark.parametrize("case", RECORDED_GROUP_CASES)
def test_reaper_waits_for_live_recorded_groups_up_to_the_stale_bound(
    listing: dict[str, list[str]],
    removals: MockType,
    mocker: MockerFixture,
    case: str,
) -> None:
    """A dead owner's detached command may still be creating its stack."""
    alive, age, reaped = RECORDED_GROUP_CASES[case]
    lock = services.lock_directory() / f"{PROJECT}.lock"
    lock.write_text("4242\n")
    probe = mocker.patch.object(services, "_group_alive", return_value=alive)
    listing["container"].append(_labels(created=NOW - age))
    assert services.reap_stale_stacks(now=NOW) == ((PROJECT,) if reaped else ())
    assert _removed(removals) == ([PROJECT] if reaped else [])
    if age < 3600:
        probe.assert_called_once_with(4242)
    assert lock.exists() is not reaped


def test_started_groups_are_written_to_every_held_lock(mocker: MockerFixture) -> None:
    lock = services.lock_directory() / f"{PROJECT}.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    services._HELD[PROJECT] = services._HeldLock(lock, descriptor)
    _popen(mocker)
    services._compose(PROJECT, {}, "port")
    services._compose(PROJECT, {}, "port")
    assert services._recorded_groups(descriptor) == [4242, 4242]


def test_release_refuses_while_the_stack_or_a_group_remains(
    mocker: MockerFixture,
) -> None:
    lock = services.lock_directory() / f"{PROJECT}.lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    services._HELD[PROJECT] = services._HeldLock(lock, descriptor)
    services._OWNED[PROJECT] = {}
    assert services.release_if_settled(PROJECT) is False
    del services._OWNED[PROJECT]
    survivor = mocker.patch.object(services, "unsettled_commands", return_value=True)
    assert services.release_if_settled(PROJECT) is False
    assert _locked_elsewhere(lock)
    survivor.return_value = False
    assert services.release_if_settled(PROJECT) is True
    assert not lock.exists()
    assert services.release_if_settled(PROJECT) is True
