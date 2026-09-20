"""Rootless systemd slice cleanup and bounded kernel cgroup evidence."""

from __future__ import annotations

import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final

from markweave.broker.podman_contract import Command, PodmanRuntimeError

_CGROUP_EVENTS_MAX_BYTES: Final = 4096

_SYSTEMD_PROPERTIES_MAX_BYTES: Final = 512

_OWNER_ONLY_MODE: Final = 0o700


class SystemdCgroupRemover:
    """Stop one exact rootless systemd slice through a bounded local command."""

    def __init__(self, command: Command) -> None:
        if not callable(command):
            raise ValueError("Systemd cgroup remover configuration is invalid")
        self._command = command

    def __call__(self, path: Path) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or re.fullmatch(r"markweavet70[0-9a-f]{32}\.slice", path.name) is None
        ):
            raise PodmanRuntimeError("Podman cgroup cleanup identity is invalid")
        self._command(("--user", "stop", path.name), max_output_bytes=0)
        _, output = self._command(
            (
                "--user",
                "show",
                path.name,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=ControlGroup",
            ),
            max_output_bytes=_SYSTEMD_PROPERTIES_MAX_BYTES,
        )
        properties = _parse_systemd_properties(output)
        if properties != {
            "ActiveState": "inactive",
            "ControlGroup": "",
            "LoadState": "loaded",
            "SubState": "dead",
        }:
            raise PodmanRuntimeError("Podman cgroup cleanup is unconfirmed")
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise PodmanRuntimeError("Podman cgroup cleanup failed") from error
        if _parse_cgroup_events(_read_cgroup_events(path)).get("populated") != 0:
            raise PodmanRuntimeError("Podman cgroup cleanup is unconfirmed")
        try:
            path.rmdir()
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise PodmanRuntimeError("Podman cgroup cleanup failed") from error
        raise PodmanRuntimeError("Podman cgroup cleanup is unconfirmed")


def _read_cgroup_events(path: Path) -> bytes:
    try:
        descriptor = os.open(
            path / "cgroup.events", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        try:
            value = os.read(descriptor, _CGROUP_EVENTS_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup evidence is unavailable") from error
    if len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup evidence exceeded its bound")
    return value


def _create_cgroup(path: Path) -> None:
    with suppress(FileExistsError):
        path.mkdir(mode=_OWNER_ONLY_MODE)
    try:
        metadata = path.stat(follow_symlinks=False)
        (path / "cgroup.events").stat(follow_symlinks=False)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup preparation failed") from error
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise PodmanRuntimeError("Podman cgroup preparation failed")


def _validate_cgroup_root(path: Path) -> None:
    expected = Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service"
    )
    try:
        resolved = path.resolve(strict=True)
        metadata = path.stat(follow_symlinks=False)
        (path / "cgroup.controllers").stat(follow_symlinks=False)
        (path / "cgroup.events").stat(follow_symlinks=False)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup root is invalid") from error
    if (
        path != expected
        or resolved != expected
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
    ):
        raise PodmanRuntimeError("Podman cgroup root is invalid")


def _read_process_cgroup(pid: int) -> bytes:
    if type(pid) is not int or pid <= 0:
        raise PodmanRuntimeError("Podman cgroup binding is invalid")
    path = Path("/proc") / str(pid) / "cgroup"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            value = os.read(descriptor, _CGROUP_EVENTS_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup binding is unavailable") from error
    if len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup binding exceeded its bound")
    return value


def _validate_hooks_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            entries = os.listdir(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman hooks directory is invalid") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != _OWNER_ONLY_MODE
        or entries
    ):
        raise PodmanRuntimeError("Podman hooks directory is invalid")


def _parse_cgroup_events(value: bytes) -> dict[str, int]:
    if type(value) is not bytes or not value or len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup evidence is invalid")
    parsed: dict[str, int] = {}
    try:
        text = value.decode("ascii")
        for line in text.splitlines():
            key, raw = line.split(" ", 1)
            if key not in {"frozen", "populated"} or key in parsed:
                raise ValueError
            parsed[key] = int(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise PodmanRuntimeError("Podman cgroup evidence is invalid") from error
    if set(parsed) != {"frozen", "populated"} or any(
        item not in {0, 1} for item in parsed.values()
    ):
        raise PodmanRuntimeError("Podman cgroup evidence is invalid")
    return parsed


def _parse_systemd_properties(value: bytes) -> dict[str, str]:
    if (
        type(value) is not bytes
        or not value
        or len(value) > _SYSTEMD_PROPERTIES_MAX_BYTES
    ):
        raise PodmanRuntimeError("Podman systemd evidence is invalid")
    parsed: dict[str, str] = {}
    allowed = {"ActiveState", "ControlGroup", "LoadState", "SubState"}
    try:
        text = value.decode("ascii")
        if not text.endswith("\n"):
            raise ValueError
        for line in text.splitlines():
            key, raw = line.split("=", 1)
            if key not in allowed or key in parsed:
                raise ValueError
            parsed[key] = raw
    except (UnicodeDecodeError, ValueError) as error:
        raise PodmanRuntimeError("Podman systemd evidence is invalid") from error
    if set(parsed) != allowed:
        raise PodmanRuntimeError("Podman systemd evidence is invalid")
    return parsed


def _matches_process_cgroup(evidence: bytes, expected: str) -> bool:
    """Match the two exact cgroup-v2 placements supported by crun systemd."""

    return evidence in {
        f"0::{expected}\n".encode(),
        f"0::{expected}/container\n".encode(),
    }
