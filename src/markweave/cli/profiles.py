"""Secure, bounded XDG persistence for remote CLI connection profiles."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from markweave.cli.errors import CliError
from markweave.cli.types import ConnectionProfile

_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MAX_URL_LENGTH = 2_048
_MAX_SECRET_LENGTH = 4_096
_MAX_FILE_LENGTH = 16_384
_SCHEMA_VERSION = 1
_PROFILE_FILE_MODE = 0o600
_ASCII_CONTROL_MAX = 32
_ASCII_DELETE = 127


def validate_profile_name(name: str) -> str:
    """Return a safe bounded profile name, rejecting path-like values."""
    if not _PROFILE_NAME.fullmatch(name):
        raise CliError(
            "invalid_profile",
            "Profile names contain only letters, digits, '.', '_' and '-'.",
        )
    return name


def validate_service_url(value: str, *, verify_tls: bool) -> str:
    """Validate a remote base URL before it reaches an HTTP client."""
    if (
        not value
        or len(value) > _MAX_URL_LENGTH
        or any(
            ord(character) <= _ASCII_CONTROL_MAX or ord(character) == _ASCII_DELETE
            for character in value
        )
    ):
        raise CliError("invalid_service_url", "The service URL is invalid.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as error:
        raise CliError("invalid_service_url", "The service URL is invalid.") from error
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise CliError(
            "invalid_service_url", "The service URL must be an absolute HTTP URL."
        )
    if parsed.username is not None or parsed.password is not None:
        raise CliError(
            "invalid_service_url", "The service URL must not contain credentials."
        )
    if parsed.query or parsed.fragment:
        raise CliError(
            "invalid_service_url",
            "The service URL must not contain a query or fragment.",
        )
    if (
        parsed.scheme == "http"
        and verify_tls
        and not _is_loopback_host(parsed.hostname)
    ):
        raise CliError(
            "tls_required",
            "HTTPS is required except for a loopback-only evaluation service.",
        )
    return value.rstrip("/")


def _is_loopback_host(hostname: str | None) -> bool:
    try:
        return hostname is not None and ip_address(hostname).is_loopback
    except ValueError:
        return False


class ProfileStore:
    """Read and atomically replace owner-only profile documents."""

    def __init__(self, state_home: Path | None = None) -> None:
        self._state_home = (
            state_home if state_home is not None else _default_state_home()
        )

    def load(self, name: str) -> ConnectionProfile:
        """Load one validated profile without exposing its opaque values."""
        path = self._path(name)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as error:
            raise CliError(
                "profile_not_found", "The selected connection profile does not exist."
            ) from error
        except OSError as error:
            raise CliError(
                "profile_unsafe", "The selected connection profile is unsafe."
            ) from error
        try:
            metadata = os.fstat(descriptor)
            _check_profile_metadata(metadata)
            with os.fdopen(descriptor, encoding="utf-8") as profile_file:
                descriptor = -1
                try:
                    content = profile_file.read(_MAX_FILE_LENGTH + 1)
                except UnicodeError as error:
                    raise CliError(
                        "profile_invalid", "The selected connection profile is invalid."
                    ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > _MAX_FILE_LENGTH:
            raise CliError(
                "profile_invalid", "The selected connection profile is invalid."
            )
        return _decode_profile(content, name)

    def save(self, profile: ConnectionProfile) -> None:
        """Replace one profile through a private temporary file and atomic rename."""
        name = validate_profile_name(profile.name)
        payload = _encode_profile(profile)
        directory = self._directory()
        self._reject_unsafe_existing_path(self._path(name))
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=directory)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, _PROFILE_FILE_MODE)
            with os.fdopen(descriptor, "w", encoding="utf-8") as profile_file:
                descriptor = -1
                profile_file.write(payload)
                profile_file.flush()
                os.fsync(profile_file.fileno())
            os.replace(temporary_path, self._path(name))
        except OSError as error:
            raise CliError(
                "profile_write_failed", "The connection profile could not be saved."
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                temporary_path.unlink()

    def delete(self, name: str) -> None:
        """Remove a validated regular profile file after a successful remote logout."""
        path = self._path(name)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        _check_profile_metadata(metadata)
        try:
            path.unlink()
        except OSError as error:
            raise CliError(
                "profile_delete_failed", "The connection profile could not be removed."
            ) from error

    def _directory(self) -> Path:
        self._ensure_state_home()
        markweave_directory = self._state_home / "markweave"
        self._ensure_private_directory(markweave_directory)
        directory = markweave_directory / "profiles"
        self._ensure_private_directory(directory)
        return directory

    def _path(self, name: str) -> Path:
        return (
            self._state_home
            / "markweave"
            / "profiles"
            / f"{validate_profile_name(name)}.json"
        )

    @staticmethod
    def _reject_unsafe_existing_path(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        _check_profile_metadata(metadata)

    def _ensure_state_home(self) -> None:
        try:
            self._state_home.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = self._state_home.lstat()
        except OSError as error:
            raise CliError(
                "profile_directory_unsafe", "The profile directory is unsafe."
            ) from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise CliError(
                "profile_directory_unsafe", "The profile directory is unsafe."
            )
        if metadata.st_uid != os.getuid():
            raise CliError(
                "profile_directory_unsafe", "The profile directory is unsafe."
            )

    @staticmethod
    def _ensure_private_directory(directory: Path) -> None:
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
            metadata = directory.lstat()
        except OSError as error:
            raise CliError(
                "profile_directory_unsafe", "The profile directory is unsafe."
            ) from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
        ):
            raise CliError(
                "profile_directory_unsafe", "The profile directory is unsafe."
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            try:
                directory.chmod(0o700)
            except OSError as error:
                raise CliError(
                    "profile_directory_unsafe", "The profile directory is unsafe."
                ) from error


def _default_state_home() -> Path:
    value = os.environ.get("XDG_STATE_HOME")
    if value:
        path = Path(value)
        if not path.is_absolute():
            raise CliError(
                "profile_directory_unsafe", "XDG_STATE_HOME must be an absolute path."
            )
        return path
    return Path.home() / ".local" / "state"


def _check_profile_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != _PROFILE_FILE_MODE
    ):
        raise CliError("profile_unsafe", "The selected connection profile is unsafe.")


def _encode_profile(profile: ConnectionProfile) -> str:
    service_url = validate_service_url(profile.service_url, verify_tls=True)
    if (
        profile.session_state is None
        or profile.csrf_state is None
        or not profile.session_state
        or not profile.csrf_state
        or len(profile.session_state) > _MAX_SECRET_LENGTH
        or len(profile.csrf_state) > _MAX_SECRET_LENGTH
    ):
        raise CliError("profile_invalid", "The connection profile is invalid.")
    data = {
        "csrf_state": profile.csrf_state,
        "name": validate_profile_name(profile.name),
        "service_url": service_url,
        "session_state": profile.session_state,
        "version": _SCHEMA_VERSION,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _decode_profile(content: str, expected_name: str) -> ConnectionProfile:
    try:
        data: Any = json.loads(content)
    except (TypeError, ValueError) as error:
        raise CliError(
            "profile_invalid", "The selected connection profile is invalid."
        ) from error
    if not isinstance(data, dict) or set(data) != {
        "csrf_state",
        "name",
        "service_url",
        "session_state",
        "version",
    }:
        raise CliError("profile_invalid", "The selected connection profile is invalid.")
    if data.get("version") != _SCHEMA_VERSION or data.get("name") != expected_name:
        raise CliError("profile_invalid", "The selected connection profile is invalid.")
    session = data.get("session_state")
    csrf = data.get("csrf_state")
    service_url = data.get("service_url")
    if not all(isinstance(value, str) for value in (session, csrf, service_url)):
        raise CliError("profile_invalid", "The selected connection profile is invalid.")
    if (
        not session
        or not csrf
        or len(session) > _MAX_SECRET_LENGTH
        or len(csrf) > _MAX_SECRET_LENGTH
    ):
        raise CliError("profile_invalid", "The selected connection profile is invalid.")
    return ConnectionProfile(
        name=expected_name,
        service_url=validate_service_url(service_url, verify_tls=True),
        session_state=session,
        csrf_state=csrf,
    )
