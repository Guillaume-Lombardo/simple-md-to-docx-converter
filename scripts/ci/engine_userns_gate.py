"""Run native engine tests with verified user namespaces on hosted Ubuntu CI.

The production launcher stays fail closed. This runner helper temporarily
relaxes Ubuntu's AppArmor user-namespace gate only on a GitHub-hosted runner,
then restores its exact original value after the selected command finishes.
"""

from __future__ import annotations

import errno
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from markweave.conversion.engine_launcher import _install_no_network_filter

if TYPE_CHECKING:
    from collections.abc import Sequence

_APPARMOR_USERNS_KEY = "kernel.apparmor_restrict_unprivileged_userns"
_APPARMOR_USERNS_FILE = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
_APPARMOR_LABEL_FILE = Path("/proc/self/attr/current")
_APPARMOR_USERNS_LABEL = "unprivileged_userns (enforce)"
_MAX_LABEL_BYTES = 96
_ASCII_PRINTABLE_MIN = 32
_ASCII_PRINTABLE_MAX = 126
_MAX_CAPABILITY_HEX_DIGITS = 16
_VERIFIED_APPARMOR_MAPPING_DENIED = 42
_PREFLIGHT_FAILED = 43
_PREFLIGHT_TIMEOUT_SECONDS = 15


def _own_apparmor_label() -> str | None:
    """Read a bounded, printable label from this process only."""
    try:
        with _APPARMOR_LABEL_FILE.open("rb") as label_file:
            raw = label_file.read(_MAX_LABEL_BYTES + 1)
    except OSError:
        return None
    if len(raw) > _MAX_LABEL_BYTES:
        return None
    raw = raw.removesuffix(b"\n")
    if not raw or any(
        character < _ASCII_PRINTABLE_MIN or character > _ASCII_PRINTABLE_MAX
        for character in raw
    ):
        return None
    return raw.decode("ascii")


def _current_userns_restriction() -> str | None:
    try:
        with _APPARMOR_USERNS_FILE.open("rb") as setting_file:
            raw = setting_file.read(4).strip()
    except OSError:
        return None
    return raw.decode("ascii") if raw in {b"0", b"1"} else None


def _own_security_status() -> tuple[str, str]:
    """Return bounded no-new-privileges and effective-capability diagnostics."""
    try:
        with Path("/proc/self/status").open("rb") as status_file:
            lines = status_file.read(16_384).splitlines()
    except OSError:
        return "unavailable", "unavailable"
    values: dict[bytes, str] = {}
    for line in lines:
        key, separator, value = line.partition(b":")
        if separator and key in {b"NoNewPrivs", b"CapEff"}:
            candidate = value.strip()
            valid_no_new_privs = key == b"NoNewPrivs" and candidate in {b"0", b"1"}
            valid_capabilities = (
                key == b"CapEff"
                and bool(candidate)
                and len(candidate) <= _MAX_CAPABILITY_HEX_DIGITS
                and all(
                    character in b"0123456789abcdefABCDEF" for character in candidate
                )
            )
            if valid_no_new_privs or valid_capabilities:
                values[key] = candidate.decode("ascii")
    return values.get(b"NoNewPrivs", "unavailable"), values.get(
        b"CapEff", "unavailable"
    )


def _mapping_error(
    stage: str,
    error: OSError,
    before_label: str | None,
    after_label: str | None,
) -> int:
    restriction = _current_userns_restriction()
    hosted = _is_ephemeral_ubuntu_runner()
    no_new_privs, capabilities = _own_security_status()
    print(f"engine {stage} preflight: errno={error.errno}", file=sys.stderr)
    print(
        "engine namespace context: "
        f"apparmor_before={before_label or 'unavailable'!r} "
        f"apparmor_after={after_label or 'unavailable'!r} "
        f"restriction={restriction or 'unavailable'} "
        f"hosted_ubuntu24={int(hosted)} euid={os.geteuid()} egid={os.getegid()} "
        f"no_new_privs={no_new_privs} cap_eff={capabilities}",
        file=sys.stderr,
    )
    if (
        error.errno == errno.EPERM
        and after_label == _APPARMOR_USERNS_LABEL
        and restriction == "1"
        and hosted
    ):
        return _VERIFIED_APPARMOR_MAPPING_DENIED
    return _PREFLIGHT_FAILED


def _preflight() -> int:
    """Report only the setup stage and errno, never engine paths or content."""
    user_id = os.geteuid()
    group_id = os.getegid()
    user_namespace = os.readlink("/proc/self/ns/user")
    network_namespace = os.readlink("/proc/self/ns/net")
    try:
        _install_no_network_filter()
    except OSError as error:
        print(f"engine seccomp preflight: errno={error.errno}", file=sys.stderr)
        return _PREFLIGHT_FAILED
    before_label = _own_apparmor_label()
    try:
        os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
    except OSError as error:
        print(f"engine unshare preflight: errno={error.errno}", file=sys.stderr)
        return _PREFLIGHT_FAILED
    after_label = _own_apparmor_label()
    # Mirror the production launcher's exact mapping order, while identifying
    # the syscall stage so only a verified AppArmor denial can reach the gate.
    for stage, path, mapping in (
        ("uid_map", Path("/proc/self/uid_map"), f"{user_id} {user_id} 1\n"),
        ("setgroups", Path("/proc/self/setgroups"), "deny\n"),
        ("gid_map", Path("/proc/self/gid_map"), f"{group_id} {group_id} 1\n"),
    ):
        try:
            path.write_text(mapping)
        except OSError as error:
            return _mapping_error(stage, error, before_label, after_label)
    if (
        os.geteuid() != user_id
        or os.getegid() != group_id
        or os.readlink("/proc/self/ns/user") == user_namespace
        or os.readlink("/proc/self/ns/net") == network_namespace
    ):
        print(
            "engine namespace preflight: identity or isolation mismatch",
            file=sys.stderr,
        )
        return _PREFLIGHT_FAILED
    print("engine namespace preflight: same UID/GID and private user/net namespaces")
    return 0


def _run_preflight() -> int:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.ci.engine_userns_gate", "--preflight"],
            check=False,
            timeout=_PREFLIGHT_TIMEOUT_SECONDS,
        )
    except OSError, subprocess.TimeoutExpired:
        print("engine namespace preflight could not complete", file=sys.stderr)
        return _PREFLIGHT_FAILED
    return completed.returncode


def _is_ephemeral_ubuntu_runner() -> bool:
    try:
        os_release = platform.freedesktop_os_release()
    except OSError:
        return False
    return (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux"
        and os_release.get("ID") == "ubuntu"
        and os_release.get("VERSION_ID") == "24.04"
    )


def _set_apparmor_userns_restriction(value: str) -> bool:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed sysctl key/value
            [
                "/usr/bin/sudo",
                "/usr/sbin/sysctl",
                "-q",
                "-w",
                f"{_APPARMOR_USERNS_KEY}={value}",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0


def _run_command(command: list[str]) -> int:
    try:
        return subprocess.run(command, check=False).returncode  # noqa: S603 - reviewed argv
    except OSError:
        print("engine test command could not start", file=sys.stderr)
        return 1


def _run_with_temporary_allowance(arguments: list[str]) -> int:
    """Keep the system-wide policy change within this ephemeral CI step."""
    try:
        previous = _APPARMOR_USERNS_FILE.read_text(encoding="ascii").strip()
    except OSError:
        return 1
    if previous != "1":
        return 1

    def stop_on_signal(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    handled_signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers = {
        signum: signal.signal(signum, stop_on_signal) for signum in handled_signals
    }
    status = 1
    try:
        if _set_apparmor_userns_restriction("0") and _run_preflight() == 0:
            status = _run_command(arguments)
    finally:
        # A second catchable cancellation must not interrupt restoration.
        for signum in handled_signals:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if not _set_apparmor_userns_restriction(previous):
                print(
                    "engine userns gate could not restore AppArmor policy",
                    file=sys.stderr,
                )
                status = 1
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--preflight"]:
        return _preflight()
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    if not arguments:
        print("engine userns gate requires a command", file=sys.stderr)
        return 64

    result = _run_preflight()
    if result == 0:
        return _run_command(arguments)
    if result != _VERIFIED_APPARMOR_MAPPING_DENIED or not _is_ephemeral_ubuntu_runner():
        return 1
    return _run_with_temporary_allowance(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
