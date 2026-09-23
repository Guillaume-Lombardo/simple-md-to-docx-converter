"""Fail-closed checks for the document-engine seccomp launcher."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from markweave.conversion import engine_launcher

pytestmark = pytest.mark.unit


def test_filter_sets_no_new_privileges_before_installing_seccomp(mocker) -> None:
    libc = mocker.Mock()
    libc.prctl.return_value = 0
    mocker.patch.object(engine_launcher.ctypes, "CDLL", return_value=libc)
    mocker.patch.object(engine_launcher.platform, "machine", return_value="x86_64")

    engine_launcher._install_no_network_filter()

    assert libc.prctl.call_count == 2
    assert libc.prctl.call_args_list[0].args[:2] == (38, 1)
    assert libc.prctl.call_args_list[1].args[:2] == (22, 2)


def test_filter_rejects_unsupported_architecture(mocker) -> None:
    mocker.patch.object(engine_launcher.platform, "machine", return_value="unsupported")
    with pytest.raises(OSError) as captured:
        engine_launcher._install_no_network_filter()
    assert captured.value.errno == errno.ENOTSUP


def test_namespace_maps_current_uid_and_gid(mocker) -> None:
    unshare = mocker.patch.object(engine_launcher.os, "unshare")
    mocker.patch.object(engine_launcher.os, "geteuid", return_value=12345)
    mocker.patch.object(engine_launcher.os, "getegid", return_value=0)
    write = mocker.patch.object(Path, "write_text")

    engine_launcher._isolate_namespaces()

    unshare.assert_called_once_with(
        engine_launcher.os.CLONE_NEWUSER | engine_launcher.os.CLONE_NEWNET
    )
    assert [call.args[0] for call in write.call_args_list] == [
        "12345 12345 1\n",
        "deny\n",
        "0 0 1\n",
    ]


@pytest.mark.parametrize("calls", ([-1], [0, -1]))
def test_filter_setup_failure_never_executes_engine(mocker, calls: list[int]) -> None:
    libc = mocker.Mock()
    libc.prctl.side_effect = calls
    mocker.patch.object(engine_launcher.ctypes, "CDLL", return_value=libc)
    mocker.patch.object(engine_launcher.ctypes, "get_errno", return_value=errno.EPERM)
    mocker.patch.object(engine_launcher.platform, "machine", return_value="x86_64")
    execute = mocker.patch.object(engine_launcher.os, "execvpe")
    mocker.patch.object(engine_launcher.sys, "argv", ["launcher.py", "pandoc"])

    assert engine_launcher.main() == 125
    execute.assert_not_called()


def test_missing_engine_returns_distinct_unavailable_status(mocker) -> None:
    mocker.patch.object(engine_launcher, "_install_no_network_filter")
    mocker.patch.object(engine_launcher, "_isolate_namespaces")
    mocker.patch.object(
        engine_launcher.os, "execvpe", side_effect=FileNotFoundError("missing")
    )
    mocker.patch.object(engine_launcher.sys, "argv", ["launcher.py", "missing"])
    assert engine_launcher.main() == engine_launcher.ENGINE_UNAVAILABLE_EXIT_STATUS


def test_empty_command_returns_bounded_failure(mocker) -> None:
    install = mocker.patch.object(engine_launcher, "_install_no_network_filter")
    mocker.patch.object(engine_launcher.sys, "argv", ["launcher.py"])
    assert engine_launcher.main() == 125
    install.assert_not_called()


def test_namespace_setup_failure_never_executes_engine(mocker) -> None:
    mocker.patch.object(engine_launcher, "_install_no_network_filter")
    mocker.patch.object(
        engine_launcher, "_isolate_namespaces", side_effect=OSError(errno.EPERM)
    )
    execute = mocker.patch.object(engine_launcher.os, "execvpe")
    mocker.patch.object(engine_launcher.sys, "argv", ["launcher.py", "pandoc"])
    assert engine_launcher.main() == 125
    execute.assert_not_called()
