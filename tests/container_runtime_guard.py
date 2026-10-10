"""Runtime guard: a test that launches a container runtime must carry its marker.

The default local command excludes ``requires_podman`` so that it passes on a
host without Podman. A test that launches Podman without that marker would fail
there with ``FileNotFoundError``. This pytest plugin, registered by
``tests/conftest.py``, installs a Python audit hook (``sys.addaudithook``) and
inspects the process launches made while a test item runs its setup or call
phase:

* ``subprocess.Popen``, which also serves ``subprocess.run``, asyncio
  subprocesses, ``os.popen`` and ``subprocess.getoutput``;
* ``os.system``, ``os.posix_spawn*`` and ``pty.spawn``;
* ``os.exec*``, including the exec that an ``os.spawn*`` child performs after
  ``fork``. The forked child inherits the active item, refuses the exec and
  reports the violation to the parent through a per-process directory, so the
  parent fails the test when it builds the report.

Program rule: the launched executable is authoritative (an explicit
``executable=`` wins over ``argv[0]``). When it is a launcher (``env``,
``nohup``, ``sudo``), the program is the first argument vector word after
``VAR=value`` assignments and options. When it is a shell (``sh``, ``bash``,
``dash``, ``zsh``) the ``-c`` script is inspected.

Shell rule: a shell command string (``shell=True``, a shell ``-c`` script,
``os.system``, ``os.popen``, ``asyncio.create_subprocess_shell``,
``subprocess.getoutput``) launches a runtime if the runtime's name appears in it
as a shell word anywhere, including inside quotes, ``$(...)`` and backticks.
This deliberately over-approximates (``echo podman`` is flagged): a missed
launch is silent, whereas a false positive fails loudly and is fixed by marking
the test.

``podman`` requires ``requires_podman``. ``docker`` requires ``requires_docker``;
``requires_postgres`` and ``requires_s3`` also allow it because their fixture
provisions disposable services with Docker Compose. Markers are the item's
effective markers, so module, class, function and ``pytest.param`` marks count.

A violation raises :class:`ContainerRuntimeMarkerError` from the audit hook
before the process starts, and is recorded on the item. A recorded violation
fails the setup or call report whatever the test did afterwards, including
swallowing the error, skipping or being marked ``xfail``.

Limits, covered by the Podman-free canonical run: processes launched by other
processes (shell scripts, the host-native broker), non-Python launchers,
launches during collection, outside any item or during teardown (module- and
session-scoped finalizers run during an unrelated item's teardown), and launches
from threads while a nested pytester session runs (attributed to the innermost
active item). Launches by fixture setup are attributed to the item that
instantiates the fixture. An ``os.spawn*`` child that is not awaited
(``P_NOWAIT``) may report after its item's report has been built.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Sequence

RUNTIMES = frozenset({"podman", "docker"})
ALLOWING_MARKERS = {
    "podman": ("requires_podman",),
    "docker": ("requires_docker", "requires_postgres", "requires_s3"),
}
SHELLS = frozenset({"sh", "bash", "dash", "zsh"})
LAUNCHERS = frozenset({"env", "nohup", "sudo"})
_SHELL_WORDS = {
    runtime: re.compile(
        rf"(?:^|[^\w.\-/])(?:[^\s'\"`;|&()<>]*/)?{runtime}(?=$|[^\w.\-])"
    )
    for runtime in RUNTIMES
}
_ENFORCED_PHASES = frozenset({"setup", "call"})
_VIOLATIONS = pytest.StashKey[list[str]]()


class ContainerRuntimeMarkerError(RuntimeError):
    """A test launched a container runtime without the matching marker."""


def _text(value: object) -> str | None:
    if isinstance(value, str | bytes | os.PathLike):
        return os.fsdecode(value)
    return None


def shell_runtimes(command: str) -> frozenset[str]:
    """Return runtimes whose name appears as a shell word anywhere in a command."""
    return frozenset(
        runtime for runtime, pattern in _SHELL_WORDS.items() if pattern.search(command)
    )


def _after_launcher(words: Sequence[str]) -> int | None:
    """Return the index of the program a launcher runs."""
    for index, word in enumerate(words):
        name, separator, _ = word.partition("=")
        if (separator and name.isidentifier()) or word.startswith("-"):
            continue
        return index
    return None


def argv_runtimes(argv: Iterable[object], executable: object = None) -> frozenset[str]:
    """Return the runtimes started by an executable and its argument vector."""
    words = [text for text in (_text(value) for value in argv) if text is not None]
    program = _text(executable) or (words[0] if words else None)
    arguments = words[1:]
    name = PurePosixPath(program).name if program else None
    while name in LAUNCHERS:
        index = _after_launcher(arguments)
        if index is None:
            return frozenset()
        name = PurePosixPath(arguments[index]).name
        arguments = arguments[index + 1 :]
    runtimes = {name} & RUNTIMES if name else set()
    if name in SHELLS and "-c" in arguments[:-1]:
        runtimes |= shell_runtimes(arguments[arguments.index("-c") + 1])
    return frozenset(runtimes)


def launched_runtimes(event: str, arguments: tuple[Any, ...]) -> frozenset[str]:
    """Return the container runtimes started by one audit event."""
    if event == "subprocess.Popen" and len(arguments) >= 2:
        executable, argv = arguments[0], arguments[1]
        if isinstance(argv, str | bytes | os.PathLike):
            argv = [argv]
        return argv_runtimes(argv if isinstance(argv, list | tuple) else [], executable)
    if event == "os.system" and arguments:
        command = _text(arguments[0])
        return shell_runtimes(command) if command else frozenset()
    if event in {"os.exec", "os.posix_spawn"} and len(arguments) >= 2:
        argv = arguments[1] if isinstance(arguments[1], list | tuple) else []
        return argv_runtimes(argv, arguments[0])
    if event == "pty.spawn" and arguments:
        argv = arguments[0]
        return argv_runtimes([argv] if isinstance(argv, str | bytes) else argv)
    return frozenset()


_EVENTS = frozenset(
    {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "pty.spawn"}
)
# The innermost running context: an item phase, or ``None`` for session work
# outside items. Nested pytester sessions push on top of the outer item.
_ACTIVE: list[tuple[pytest.Item | None, str]] = []
_OWNER_PID = os.getpid()
_CHILD_REPORTS: Path | None = None
_HOOK_INSTALLED = False


def missing_markers(item: pytest.Item, runtimes: Iterable[str]) -> list[str]:
    """Return runtimes whose allowing markers are all absent from the item."""
    return sorted(
        runtime
        for runtime in runtimes
        if not any(
            item.get_closest_marker(marker) is not None
            for marker in ALLOWING_MARKERS[runtime]
        )
    )


def _report_from_child(nodeid: str, message: str) -> None:
    """Let a forked child hand its violation to the parent process."""
    if _CHILD_REPORTS is not None:
        path = _CHILD_REPORTS / f"{uuid.uuid4().hex}.violation"
        path.write_text(f"{nodeid}\n{message}", encoding="utf-8")


def _child_reports(nodeid: str) -> list[str]:
    """Consume violations that forked children reported for one item."""
    if _CHILD_REPORTS is None:
        return []
    messages: list[str] = []
    for path in sorted(_CHILD_REPORTS.glob("*.violation")):
        owner, _, message = path.read_text(encoding="utf-8").partition("\n")
        if owner == nodeid:
            messages.append(message)
            path.unlink()
    return messages


def _audit(event: str, arguments: tuple[Any, ...]) -> None:
    if event not in _EVENTS or not _ACTIVE:
        return
    item, phase = _ACTIVE[-1]
    if item is None or phase not in _ENFORCED_PHASES:
        return
    missing = missing_markers(item, launched_runtimes(event, arguments))
    if not missing:
        return
    message = "; ".join(
        f"{item.nodeid} launched {runtime} without pytest.mark."
        f"{ALLOWING_MARKERS[runtime][0]}"
        for runtime in missing
    )
    if os.getpid() == _OWNER_PID:
        item.stash.setdefault(_VIOLATIONS, []).append(message)
    else:
        _report_from_child(item.nodeid, message)
    raise ContainerRuntimeMarkerError(message)


def _within(item: pytest.Item, phase: str) -> Generator[None, Any, Any]:
    _ACTIVE.append((item, phase))
    try:
        return (yield)
    finally:
        _ACTIVE.pop()


class ContainerRuntimeGuard:
    """Pytest plugin that tracks the running item for the audit hook."""

    def __init__(self) -> None:
        global _HOOK_INSTALLED, _CHILD_REPORTS  # noqa: PLW0603 - one per process
        if not _HOOK_INSTALLED:
            _CHILD_REPORTS = Path(tempfile.mkdtemp(prefix="runtime-guard-"))
            atexit.register(shutil.rmtree, _CHILD_REPORTS, ignore_errors=True)
            sys.addaudithook(_audit)
            _HOOK_INSTALLED = True

    @pytest.hookimpl(wrapper=True)
    def pytest_sessionstart(self) -> Generator[None]:
        _ACTIVE.append((None, "session"))
        return (yield)

    @pytest.hookimpl(wrapper=True)
    def pytest_sessionfinish(self) -> Generator[None]:
        try:
            return (yield)
        finally:
            _ACTIVE.pop()

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item: pytest.Item) -> Generator[None]:
        return (yield from _within(item, "setup"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None]:
        return (yield from _within(item, "call"))

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item: pytest.Item) -> Generator[None]:
        return (yield from _within(item, "teardown"))

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        # As the outermost wrapper, this runs after skip and xfail handling.
        report = yield
        if call.when not in _ENFORCED_PHASES:
            return report
        violations = [*item.stash.get(_VIOLATIONS, []), *_child_reports(item.nodeid)]
        item.stash[_VIOLATIONS] = []
        if not violations:
            return report
        summary = "\n".join(
            ["Container runtime launched without its marker:", *violations]
        )
        if hasattr(report, "wasxfail"):
            del report.wasxfail
        if report.failed:
            # Keep the guard error traceback and add the summary.
            report.sections.append(("container runtime guard", summary))
        else:
            report.outcome = "failed"
            report.longrepr = summary
        return report
