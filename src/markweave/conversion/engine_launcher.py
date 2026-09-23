"""Isolate document engines in private Linux user/network namespaces and seccomp.

This file is invoked as an isolated Python script, not imported by the engine.
Both boundaries persist across exec and fork, including engine descendants.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Final

_PR_SET_NO_NEW_PRIVS: Final = 38
_PR_SET_SECCOMP: Final = 22
_SECCOMP_MODE_FILTER: Final = 2
_SECCOMP_RET_KILL_PROCESS: Final = 0x80000000
_SECCOMP_RET_ERRNO: Final = 0x00050000
_SECCOMP_RET_ALLOW: Final = 0x7FFF0000
_BPF_LD_W_ABS: Final = 0x20
_BPF_JMP_JEQ_K: Final = 0x15
_BPF_JMP_JSET_K: Final = 0x45
_BPF_RET_K: Final = 0x06

# AUDIT_ARCH_* and __NR_* values from Linux UAPI. Unknown architectures fail
# closed instead of silently running a document engine with network access.
_ARCHITECTURES: Final = {
    "x86_64": (0xC000003E, 41, 53, 101, 310, 311),
    "aarch64": (0xC00000B7, 198, 199, 117, 270, 271),
}
_DENIED_SYSCALLS: Final = (425, 426, 427, 438)  # io_uring*, pidfd_getfd
_MINIMUM_ARGUMENTS: Final = 2
ENGINE_UNAVAILABLE_EXIT_STATUS: Final = 127


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def _statement(code: int, value: int) -> _SockFilter:
    return _SockFilter(code, 0, 0, value)


def _jump(value: int, true: int, false: int) -> _SockFilter:
    return _SockFilter(_BPF_JMP_JEQ_K, true, false, value)


def _install_no_network_filter() -> None:
    if sys.platform != "linux" or platform.machine() not in _ARCHITECTURES:
        raise OSError(errno.ENOTSUP, "Document-engine network isolation is unavailable")
    audit_arch, socket_nr, socketpair_nr, *cross_process_syscalls = _ARCHITECTURES[
        platform.machine()
    ]
    denied = _SECCOMP_RET_ERRNO | errno.EPERM
    instructions = [
        _statement(_BPF_LD_W_ABS, 4),  # seccomp_data.arch
        _jump(audit_arch, 1, 0),
        _statement(_BPF_RET_K, _SECCOMP_RET_KILL_PROCESS),
        _statement(_BPF_LD_W_ABS, 0),  # seccomp_data.nr
        _SockFilter(_BPF_JMP_JSET_K, 0, 1, 0x40000000),  # x32 ABI
        _statement(_BPF_RET_K, denied),
    ]
    for syscall_nr in (socket_nr, socketpair_nr):
        instructions.extend(
            (
                _statement(_BPF_LD_W_ABS, 0),  # seccomp_data.nr
                _jump(syscall_nr, 0, 4),
                _statement(_BPF_LD_W_ABS, 16),  # low word of args[0]
                _jump(socket.AF_UNIX, 1, 0),
                _statement(_BPF_RET_K, denied),
                _statement(_BPF_RET_K, _SECCOMP_RET_ALLOW),
            )
        )
    for syscall_nr in (*_DENIED_SYSCALLS, *cross_process_syscalls):
        instructions.extend(
            (
                _statement(_BPF_LD_W_ABS, 0),
                _jump(syscall_nr, 0, 1),
                _statement(_BPF_RET_K, denied),
            )
        )
    instructions.append(_statement(_BPF_RET_K, _SECCOMP_RET_ALLOW))

    filter_array = (_SockFilter * len(instructions))(*instructions)
    program = _SockFprog(len(instructions), filter_array)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set no-new-privileges")
    if (
        libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0)
        != 0
    ):
        raise OSError(
            ctypes.get_errno(), "Cannot install document-engine seccomp filter"
        )


def _isolate_namespaces() -> None:
    """Remove external network routes and access to the parent's process memory."""
    user_id = os.geteuid()
    group_id = os.getegid()
    os.unshare(os.CLONE_NEWUSER | os.CLONE_NEWNET)
    Path("/proc/self/uid_map").write_text(f"{user_id} {user_id} 1\n")
    Path("/proc/self/setgroups").write_text("deny\n")
    Path("/proc/self/gid_map").write_text(f"{group_id} {group_id} 1\n")


def isolated_engine_command(arguments: list[str]) -> list[str]:
    """Return a fixed argv that isolates the supplied engine before its exec."""
    return [sys.executable, "-I", "-S", str(Path(__file__).resolve()), *arguments]


def main() -> int:
    if len(sys.argv) < _MINIMUM_ARGUMENTS:
        return 125
    try:
        _install_no_network_filter()
        _isolate_namespaces()
    except OSError:
        # Engine adapters intentionally suppress document-engine stderr. Exit
        # without leaking a path or input to logs; fail closed on setup errors.
        return 125
    try:
        os.execvpe(sys.argv[1], sys.argv[1:], os.environ)  # noqa: S606 - fixed engine argv
    except FileNotFoundError, PermissionError:
        return ENGINE_UNAVAILABLE_EXIT_STATUS
    except OSError:
        return 125
    return 125


if __name__ == "__main__":
    raise SystemExit(main())
