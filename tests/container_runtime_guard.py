"""Runtime guard: a test that launches a container runtime must carry its marker.

The default local command excludes ``requires_podman`` so that it passes on a
host without Podman. A test that launches Podman without that marker would fail
there with ``FileNotFoundError``. This pytest plugin, registered by
``tests/conftest.py``, installs a Python audit hook (``sys.addaudithook``) and
inspects every process launch made by the test process while a test item runs
its setup or call phase:

* ``subprocess.Popen`` (also used by ``subprocess.run``, ``asyncio``
  subprocesses and ``os.popen``): the executable and ``argv[0]``;
* ``os.system``, ``os.exec*``, ``os.posix_spawn*`` and ``pty.spawn``.

The launched program is identified by basename after ``env``, ``nohup`` and
``sudo`` launchers. Shell launches (``sh``/``bash``/``dash``/``zsh -c`` and
``os.system``) are tokenized with ``shlex`` in POSIX mode, so quoted text such as
``echo 'ok; podman ps'`` is never a command. Every command position after
``;``, ``&``, ``&&``, ``||``, ``|``, parentheses, ``$(`` and newlines is
checked, after ``VAR=value`` assignments, shell reserved words and the
launchers above. A shell function with a runtime's name shadows that runtime in
its script. A command string that ``shlex`` cannot tokenize is not inspected.

``podman`` requires ``requires_podman``. ``docker`` requires ``requires_docker``;
``requires_postgres`` and ``requires_s3`` also allow it because their fixture
provisions disposable services with Docker Compose. Markers are the item's
effective markers, so module, class, function and ``pytest.param`` marks count.

A violation raises :class:`ContainerRuntimeMarkerError` from the audit hook
before the process starts, and is recorded on the item so that the test fails
even if its code swallows the exception.

Limits, covered by the Podman-free canonical run: processes started by child
processes (shell scripts, the host-native broker, ``os.spawn*`` and
``pty.spawn`` children that call exec after ``fork``), launches during
collection or outside any test item, and launches during teardown. Teardown is
not enforced because module- and session-scoped fixture finalizers run during
an unrelated item's teardown; launches by fixture setup are attributed to the
item that instantiates the fixture.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import PurePosixPath
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
LAUNCHERS = frozenset({"env", "nohup", "sudo", "exec", "command"})
RESERVED_WORDS = frozenset(
    {"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until", "time"}
)
_PUNCTUATION = ";&|()\n"
_ENFORCED_PHASES = frozenset({"setup", "call"})
_VIOLATIONS = pytest.StashKey[list[str]]()


class ContainerRuntimeMarkerError(RuntimeError):
    """A test launched a container runtime without the matching marker."""


def _text(value: object) -> str | None:
    if isinstance(value, str | bytes | os.PathLike):
        return os.fsdecode(value)
    return None


def _basename(value: object) -> str | None:
    text = _text(value)
    return PurePosixPath(text).name if text else None


def _is_assignment(word: str) -> bool:
    name, separator, _ = word.partition("=")
    return bool(separator) and name.isidentifier()


def _program(words: Sequence[str]) -> tuple[str | None, list[str]]:
    """Return a command's program basename after launchers, and its arguments."""
    remaining = list(words)
    while remaining:
        word = remaining[0]
        name = PurePosixPath(word).name
        if (
            _is_assignment(word)
            or word in RESERVED_WORDS
            or name in LAUNCHERS
            or word.startswith("-")
        ):
            remaining.pop(0)
            continue
        return name, remaining[1:]
    return None, []


def shell_runtimes(command: str) -> frozenset[str]:
    """Return the runtimes started at command positions of a POSIX shell string."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=_PUNCTUATION)
    lexer.whitespace = " \t\r"
    try:
        tokens = list(lexer)
    except ValueError:
        return frozenset()
    segments: list[list[str]] = [[]]
    shadowed: set[str] = set()
    for token in tokens:
        if token and all(character in _PUNCTUATION for character in token):
            if token.startswith("(") and len(segments[-1]) == 1:
                shadowed.add(segments[-1][0])  # name() defines a shell function
            segments.append([])
        else:
            segments[-1].append(token)
    runtimes: set[str] = set()
    for segment in segments:
        if segment[:1] == ["function"] and len(segment) > 1:
            shadowed.add(segment[1])
            continue
        if segment and segment[0] in shadowed:
            continue
        runtimes |= argv_runtimes(segment)
    return frozenset(runtimes - shadowed)


def argv_runtimes(argv: Iterable[object], executable: object = None) -> frozenset[str]:
    """Return the runtimes started by an argument vector and optional executable."""
    words = [text for text in (_text(value) for value in argv) if text is not None]
    runtimes: set[str] = set()
    program, arguments = _program(words)
    executable_name = _basename(executable)
    for name in (executable_name, program):
        if name in RUNTIMES:
            runtimes.add(name)
    shell = executable_name if executable_name in SHELLS else program
    if shell in SHELLS and "-c" in arguments[:-1]:
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
    item.stash.setdefault(_VIOLATIONS, []).append(message)
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
        global _HOOK_INSTALLED  # noqa: PLW0603 - audit hooks cannot be removed
        if not _HOOK_INSTALLED:
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

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        report = yield
        violations = item.stash.get(_VIOLATIONS, [])
        if call.when in _ENFORCED_PHASES and violations and report.passed:
            report.outcome = "failed"
            report.longrepr = "\n".join(
                ["Container runtime launched without its marker:", *violations]
            )
        if call.when in _ENFORCED_PHASES:
            item.stash[_VIOLATIONS] = []
        return report
