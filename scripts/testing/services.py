"""Own disposable local PostgreSQL/RustFS services for one test session.

Every stack carries labels naming its owner lock, creation time and host. The
owning session holds an exclusive ``flock`` on that lock for the stack's whole
life, so the kernel releases it however the session ends. Teardown runs on
success, failure, ``KeyboardInterrupt``, ``SIGTERM`` (turned into
``KeyboardInterrupt`` while a stack is owned) and interpreter exit, and
``SIGINT``/``SIGTERM`` wait until ``compose down`` has finished. An interrupted
or timed-out Docker command is stopped with its whole process group, including
the Compose plugin, before teardown starts, and a stack whose startup did not
finish is released only once the engine reports no resource left. A session that
cannot clean up at all, such as after ``SIGKILL`` or a pytest-xdist worker
crash, leaves a stack whose lock nobody holds; the next acquisition reaps it.
A stack whose owner still holds its lock is never touched.
"""

from __future__ import annotations

import atexit
import contextlib
import fcntl
import os
import re
import secrets
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from uuid import uuid4

from scripts.ci.prepare_s3_test_bucket import main as prepare_bucket

VARIABLES = (
    "MARKWEAVE_TEST_POSTGRES_URL",
    "MARKWEAVE_TEST_S3_ENDPOINT_URL",
    "MARKWEAVE_TEST_S3_REGION",
    "MARKWEAVE_TEST_S3_ACCESS_KEY_ID",
    "MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY",
    "MARKWEAVE_TEST_S3_BUCKET",
)
MAX_PORT = 65535
ROOT = Path(__file__).resolve().parents[2]
DOCKER_HOST = "unix:///var/run/docker.sock"
PROJECT_PREFIX = "markweave-tests-"
PROJECT_PATTERN = re.compile(rf"{PROJECT_PREFIX}[0-9a-f]{{32}}")
STACK_LABEL = "io.markweave.tests.stack"
STACK_KIND = "local-services"
LOCK_LABEL = "io.markweave.tests.lock"
CREATED_LABEL = "io.markweave.tests.created"
HOST_LABEL = "io.markweave.tests.host"
STALE_AFTER_VARIABLE = "MARKWEAVE_TEST_SERVICES_STALE_AFTER_SECONDS"
# Applies only when a stack's lock file has disappeared, so its owner cannot be
# proven gone; a provisional bound that stays configurable.
DEFAULT_STALE_AFTER_SECONDS = 3600
DOWN_ARGUMENTS = ("down", "--volumes", "--remove-orphans", "--timeout", "10")
OWNERSHIP_LABELS = (LOCK_LABEL, CREATED_LABEL, HOST_LABEL)
_PROJECT_FORMAT = "\t".join(
    f'{{{{.Label "{label}"}}}}'
    for label in ("com.docker.compose.project", *OWNERSHIP_LABELS)
)
_LISTINGS = (("container", "ls", "--all"), ("volume", "ls"), ("network", "ls"))
_DEFERRED_SIGNALS = (signal.SIGINT, signal.SIGTERM)
# Projects this process owns, with their Compose environment, for the exit net.
_OWNED: dict[str, dict[str, str]] = {}
# Process groups of interrupted commands that survived termination.
_UNSETTLED: set[int] = set()
COMMAND_TIMEOUT_SECONDS = 300
STOP_GRACE_SECONDS = 5.0
STOP_KILL_SECONDS = 10.0
SETTLE_ATTEMPTS = 3
UNAVAILABLE = (
    "Local test services require a working Docker Compose installation; "
    "alternatively configure all MARKWEAVE_TEST_* service variables."
)


def configured_environment(environment: Mapping[str, str]) -> bool:
    """Never silently replace a partially configured external service set."""
    configured = {name for name in VARIABLES if environment.get(name)}
    if configured and configured != set(VARIABLES):
        missing = ", ".join(sorted(set(VARIABLES) - configured))
        raise RuntimeError(
            f"Incomplete distributed test configuration; missing: {missing}"
        )
    return bool(configured)


class UnsettledCommandError(RuntimeError):
    """A Docker command or one of its descendants survived termination."""


def _group_alive(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_process_group(process: subprocess.Popen[str]) -> bool:
    """Terminate a command started in its own session and every descendant.

    Killing only the Docker CLI leaves its Compose plugin running, and the
    plugin can then create resources after teardown removed the stack. Return
    ``True`` once the leader is reaped and the whole process group is gone.
    """
    group = process.pid
    for signum, grace in (
        (signal.SIGTERM, STOP_GRACE_SECONDS),
        (signal.SIGKILL, STOP_KILL_SECONDS),
    ):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signum)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if process.poll() is not None and not _group_alive(group):
                return True
            time.sleep(0.02)
    return False


def _docker(
    arguments: tuple[str, ...], environment: Mapping[str, str], action: str
) -> str:
    command = ("docker", "--host", DOCKER_HOST, *arguments)
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local Docker argv
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A terminal interrupt must not reach the command directly; an
            # interruption stops its whole process group below instead.
            start_new_session=True,
        )
    except OSError as error:
        raise RuntimeError(UNAVAILABLE) from error
    try:
        stdout, _ = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
    except BaseException as error:
        # Interrupts and timeouts both stop the whole group before teardown
        # may run; a survivor keeps ownership so the stack is never forgotten.
        settled = stop_process_group(process)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        if not settled:
            _UNSETTLED.add(process.pid)
            raise UnsettledCommandError(
                f"Local test services: {action} did not stop."
            ) from error
        if isinstance(error, subprocess.TimeoutExpired):
            raise RuntimeError(UNAVAILABLE) from error
        raise
    if process.returncode:
        # Engine diagnostics can contain environment values. Do not echo them.
        raise RuntimeError(f"Local test services: {action} failed.")
    return stdout.strip()


def unsettled_commands() -> bool:
    """Return whether any interrupted Docker command still has a live process."""
    _UNSETTLED.difference_update(
        {group for group in _UNSETTLED if not _group_alive(group)}
    )
    return bool(_UNSETTLED)


def _compose(project: str, environment: Mapping[str, str], *arguments: str) -> str:
    return _docker(
        (
            "compose",
            "--project-name",
            project,
            "--file",
            str(ROOT / "compose.test.yaml"),
            *arguments,
        ),
        environment,
        f"Compose {arguments[0]}",
    )


def _port(value: str) -> int:
    host, separator, port = value.partition(":")
    if host != "127.0.0.1" or not separator or not port.isdecimal():
        raise RuntimeError("Test service must publish one loopback-only port.")
    number = int(port)
    if not 1 <= number <= MAX_PORT:
        raise RuntimeError("Test service port is invalid.")
    return number


def lock_directory() -> Path:
    """Return the private per-user directory that holds stack owner locks."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    base = Path(runtime) if runtime and Path(runtime).is_dir() else None
    directory = (base or Path(tempfile.gettempdir())) / (
        f"markweave-test-services-{os.getuid()}"
    )
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def _open_lock(path: Path, *, create: bool) -> int:
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    return os.open(path, flags, 0o600)


def _close_lock(descriptor: int, path: Path, *, remove: bool) -> None:
    try:
        if remove:
            path.unlink(missing_ok=True)
    finally:
        os.close(descriptor)


@contextmanager
def _deferred_interrupts() -> Generator[None]:
    """Deliver SIGINT and SIGTERM only after the protected block has finished.

    Python runs signal handlers in the main thread whichever thread the kernel
    picks, so replacing the handlers defers the signals even in processes with
    other threads, such as pytest-xdist workers. The first deferred signal is
    raised again once the previous handlers are back.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pending: list[int] = []

    def defer(signum: int, frame: FrameType | None) -> None:
        del frame
        pending.append(signum)

    previous = {
        signum: handler
        for signum in _DEFERRED_SIGNALS
        if (handler := signal.getsignal(signum)) is not None
    }
    for signum in previous:
        signal.signal(signum, defer)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if pending:
            signal.raise_signal(pending[0])


def _interrupt(signum: int, frame: FrameType | None) -> None:
    del signum, frame
    raise KeyboardInterrupt


@contextmanager
def _sigterm_as_interrupt() -> Generator[None]:
    """Turn a default, fatal SIGTERM into KeyboardInterrupt so teardown runs."""
    if (
        threading.current_thread() is not threading.main_thread()
        or signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    ):
        yield
        return
    signal.signal(signal.SIGTERM, _interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def remove_owned_stacks() -> None:
    """Remove stacks whose session ended without running its finalizer."""
    for project, environment in list(_OWNED.items()):
        with contextlib.suppress(RuntimeError), _deferred_interrupts():
            _compose(project, environment, *DOWN_ARGUMENTS)
        _OWNED.pop(project, None)


atexit.register(remove_owned_stacks)


@dataclass(frozen=True)
class StackLabels:
    """Ownership labels read back from one labelled Compose project."""

    lock: str
    created: str
    host: str


def labelled_stacks() -> dict[str, StackLabels]:
    """Return every Compose project that carries this repository's stack label.

    Containers, volumes and networks are all listed, so a project whose
    containers are gone is still found. A project whose resources disagree
    about ownership is left out rather than guessed.
    """
    found: dict[str, StackLabels | None] = {}
    for resource, *listing in _LISTINGS:
        output = _docker(
            (
                resource,
                *listing,
                "--filter",
                f"label={STACK_LABEL}={STACK_KIND}",
                "--format",
                _PROJECT_FORMAT,
            ),
            os.environ,
            f"Docker {resource} listing",
        )
        for line in output.splitlines():
            project, *values = line.split("\t")
            if not PROJECT_PATTERN.fullmatch(project) or len(values) != len(
                OWNERSHIP_LABELS
            ):
                continue
            labels = StackLabels(*values)
            if found.setdefault(project, labels) not in {labels, None}:
                found[project] = None
    return {project: labels for project, labels in found.items() if labels}


def _stale_after() -> int:
    value = os.environ.get(STALE_AFTER_VARIABLE, "")
    if not value:
        return DEFAULT_STALE_AFTER_SECONDS
    if not value.isdecimal():
        raise RuntimeError(f"{STALE_AFTER_VARIABLE} must be a whole number.")
    return int(value)


@dataclass(frozen=True)
class _Claim:
    """Proof that a stack is abandoned, holding its lock when one exists."""

    lock: Path
    descriptor: int | None


def _claim(
    project: str, labels: StackLabels, *, now: float, stale_after: int
) -> _Claim | None:
    """Return a claim on an abandoned stack, or ``None`` if it may be in use."""
    lock = Path(labels.lock)
    if (
        labels.host != socket.gethostname()
        or not lock.is_absolute()
        or lock.name != f"{project}.lock"
    ):
        return None
    try:
        descriptor = _open_lock(lock, create=False)
    except FileNotFoundError:
        old = labels.created.isdecimal() and now - int(labels.created) >= stale_after
        return _Claim(lock, None) if old else None
    except OSError:
        return None
    try:
        # A held claim also stops a concurrent reaper from removing it twice.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        return None
    return _Claim(lock, descriptor)


def _cleanup_environment() -> dict[str, str]:
    # ``compose down`` interpolates the file, so every required value needs a
    # placeholder; nothing is started with them.
    return {
        **os.environ,
        "MARKWEAVE_LOCAL_TEST_PASSWORD": "removal",
        "MARKWEAVE_LOCAL_TEST_ACCESS_KEY": "removal",
        "MARKWEAVE_LOCAL_TEST_LOCK": "removal",
        "MARKWEAVE_LOCAL_TEST_CREATED": "0",
        "MARKWEAVE_LOCAL_TEST_HOST": "removal",
    }


def reap_stale_stacks(*, now: float | None = None) -> tuple[str, ...]:
    """Remove labelled stacks whose owner is provably gone; keep every live one."""
    moment = time.time() if now is None else now
    stale_after = _stale_after()
    reaped: list[str] = []
    for project, labels in sorted(labelled_stacks().items()):
        if project in _OWNED:
            continue
        claim = _claim(project, labels, now=moment, stale_after=stale_after)
        if claim is None:
            continue
        removed = False
        try:
            with _deferred_interrupts():
                _compose(project, _cleanup_environment(), *DOWN_ARGUMENTS)
                removed = True
                reaped.append(project)
        except RuntimeError:
            continue
        finally:
            if claim.descriptor is not None:
                _close_lock(claim.descriptor, claim.lock, remove=removed)
    return tuple(reaped)


def project_resources(project: str) -> bool:
    """Return whether any container, volume or network of a project exists."""
    return any(
        _docker(
            (
                resource,
                *listing,
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ),
            os.environ,
            f"Docker {resource} listing",
        )
        for resource, *listing in _LISTINGS
    )


def _settle_interrupted_startup(project: str, environment: Mapping[str, str]) -> None:
    """Prove that an interrupted or failed startup left nothing behind.

    A request the stopped startup had already sent can still complete inside
    the engine after the first removal, so removal repeats until the project
    is empty. Ownership is kept when that cannot be proven.
    """
    for _ in range(SETTLE_ATTEMPTS):
        if not unsettled_commands() and not project_resources(project):
            return
        _compose(project, environment, *DOWN_ARGUMENTS)
    raise RuntimeError(
        "Local test services: an interrupted startup could not be settled."
    )


@contextmanager
def distributed_services() -> Generator[None]:
    """Reuse explicit services or acquire, initialize and remove a private test stack."""
    if configured_environment(os.environ):
        yield
        return
    project = f"{PROJECT_PREFIX}{uuid4().hex}"
    lock = lock_directory() / f"{project}.lock"
    descriptor = _open_lock(lock, create=True)
    leaked = False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        reap_stale_stacks()
        password = secrets.token_hex(24)
        access_key = secrets.token_hex(12)
        environment = {
            **os.environ,
            "MARKWEAVE_LOCAL_TEST_PASSWORD": password,
            "MARKWEAVE_LOCAL_TEST_ACCESS_KEY": access_key,
            "MARKWEAVE_LOCAL_TEST_LOCK": str(lock),
            "MARKWEAVE_LOCAL_TEST_CREATED": str(int(time.time())),
            "MARKWEAVE_LOCAL_TEST_HOST": socket.gethostname(),
        }
        previous = {name: os.environ.get(name) for name in VARIABLES}
        with _sigterm_as_interrupt():
            _OWNED[project] = environment
            leaked = True
            started = False
            try:
                _compose(
                    project,
                    environment,
                    "up",
                    "--detach",
                    "--wait",
                    "--wait-timeout",
                    "120",
                )
                started = True
                postgres = _port(
                    _compose(project, environment, "port", "postgres", "5432")
                )
                rustfs = _port(_compose(project, environment, "port", "rustfs", "9000"))
                os.environ.update(
                    dict(
                        zip(
                            VARIABLES,
                            (
                                f"postgresql+psycopg://postgres:{password}@127.0.0.1:{postgres}/md_converter_test",
                                f"http://127.0.0.1:{rustfs}",
                                "us-east-1",
                                access_key,
                                password,
                                f"test-{uuid4().hex}",
                            ),
                            strict=True,
                        )
                    )
                )
                prepare_bucket()
                yield
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
                # Bookkeeping shares the masked block, so a deferred interrupt
                # cannot separate a finished removal from its record.
                with _deferred_interrupts():
                    _compose(project, environment, *DOWN_ARGUMENTS)
                    if not started:
                        _settle_interrupted_startup(project, environment)
                    _OWNED.pop(project, None)
                    leaked = False
    finally:
        # After a failed removal the lock file stays, so the next session can
        # prove this stack abandoned and reap it at once.
        _close_lock(descriptor, lock, remove=not leaked)
