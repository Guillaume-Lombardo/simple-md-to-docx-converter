"""Exercise the kernel policy in a real unprivileged document-engine child."""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys

import pytest

from markweave.conversion.engine_launcher import isolated_engine_command
from markweave.templates.engines import TemplateEngineConfig, _run
from markweave.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)

pytestmark = pytest.mark.integration


def test_engine_descendants_keep_unix_ipc_but_cannot_open_ip_or_io_uring() -> None:
    program = """
import ctypes
import errno
import socket
import subprocess
import sys

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
    pass
left, right = socket.socketpair()
with left, right:
    pass
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_NETLINK):
    try:
        socket.socket(family, socket.SOCK_STREAM)
    except OSError as error:
        assert error.errno == errno.EPERM, (family, error)
    else:
        raise AssertionError(f"socket family {family} was allowed")

libc = ctypes.CDLL(None, use_errno=True)
for number in (425, 426, 427, 438, 101, 310, 311, 0x40000000 | 41):
    assert libc.syscall(number, 0, 0, 0) == -1
    assert ctypes.get_errno() == errno.EPERM, number

child_program = '''
import errno
import socket
try:
    socket.socket(socket.AF_INET)
except OSError as error:
    assert error.errno == errno.EPERM
else:
    raise AssertionError('descendant opened IP socket')
'''
child = subprocess.run(
    [sys.executable, "-c", child_program],
    check=False,
)
assert child.returncode == 0, child.returncode
"""
    completed = subprocess.run(
        isolated_engine_command([sys.executable, "-c", program]),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        close_fds=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_engine_has_private_network_namespace_and_preserves_uid() -> None:
    parent_network = os.readlink("/proc/self/ns/net")
    program = """
import os
import sys
assert os.geteuid() == int(sys.argv[1])
assert os.getegid() == int(sys.argv[2])
assert os.readlink('/proc/self/ns/net') != sys.argv[3]
"""
    completed = subprocess.run(
        isolated_engine_command(
            [
                sys.executable,
                "-c",
                program,
                str(os.geteuid()),
                str(os.getegid()),
                parent_network,
            ]
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        close_fds=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_engine_cannot_reach_a_live_parent_tcp_listener() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as parent_probe:
            parent_probe.settimeout(1)
            parent_probe.connect(address)
        program = """
import errno
import socket
import sys
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.connect(('127.0.0.1', int(sys.argv[1])))
except OSError as error:
    assert error.errno == errno.EPERM, error
else:
    raise AssertionError('engine reached the parent TCP listener')
"""
        completed = subprocess.run(
            isolated_engine_command([sys.executable, "-c", program, str(address[1])]),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            close_fds=True,
        )
    assert completed.returncode == 0, completed.stderr


def test_engine_cannot_write_permissive_parent_proc_memory() -> None:
    # Temporarily opt this test process out of Yama's parent-only restriction;
    # the namespace must still prevent access to its harmless local marker.
    pr_set_ptracer = 0x59616D61
    ptracer_any = ctypes.c_ulong(-1).value
    libc = ctypes.CDLL(None, use_errno=True)
    marker = ctypes.create_string_buffer(b"safe")
    assert libc.prctl(pr_set_ptracer, ptracer_any, 0, 0, 0) == 0
    try:
        program = """
import errno
import os
import sys
try:
    with open(f'/proc/{os.getppid()}/mem', 'r+b', buffering=0) as memory:
        memory.seek(int(sys.argv[1]))
        memory.write(b'fail')
except OSError as error:
    assert error.errno in (errno.EACCES, errno.EPERM), error
else:
    raise AssertionError('engine wrote parent memory')
"""
        completed = subprocess.run(
            isolated_engine_command(
                [sys.executable, "-c", program, str(ctypes.addressof(marker))]
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            close_fds=True,
        )
    finally:
        assert libc.prctl(pr_set_ptracer, 0, 0, 0, 0) == 0
    assert completed.returncode == 0, completed.stderr
    assert marker.value == b"safe"


def test_launcher_does_not_pass_an_inherited_network_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as inherited:
        os.set_inheritable(inherited.fileno(), True)
        target = os.readlink(f"/proc/self/fd/{inherited.fileno()}")
        program = (
            "import os,sys; "
            "fd=int(sys.argv[1]); "
            "expected=sys.argv[2]; "
            "assert not os.path.exists(f'/proc/self/fd/{fd}') "
            "or os.readlink(f'/proc/self/fd/{fd}') != expected"
        )
        completed = subprocess.run(
            isolated_engine_command(
                [sys.executable, "-c", program, str(inherited.fileno()), target]
            ),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            close_fds=True,
        )
    assert completed.returncode == 0, completed.stderr


def test_template_engine_reports_missing_executable(tmp_path) -> None:
    missing = str(tmp_path / "absent-template-engine")
    config = TemplateEngineConfig(missing, missing, 5.0, 0.5, tmp_path)
    with pytest.raises(TemplateValidationError) as captured:
        _run((missing, "--version"), tmp_path, {"PATH": os.environ["PATH"]}, config)
    assert captured.value.code is TemplateValidationErrorCode.ENGINE_UNAVAILABLE
    assert str(tmp_path) not in str(captured.value)
