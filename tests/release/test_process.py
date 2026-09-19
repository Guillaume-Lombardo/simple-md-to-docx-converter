"""Failure contracts for Linux release-command supervision."""

from __future__ import annotations

import signal
import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release import process as runner
from scripts.release.artifacts import ArtifactError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("results", "message"),
    [
        ([-1], "cannot inspect"),
        ([0, -1], "cannot enable"),
        ([0, 0, -1], "cannot restore"),
    ],
)
def test_subreaper_failure_is_safe_and_explicit(
    mocker: MockerFixture, results: list[int], message: str
) -> None:
    libc = mocker.Mock()
    libc.prctl.side_effect = results
    mocker.patch.object(runner.ctypes, "CDLL", return_value=libc)
    with pytest.raises(ArtifactError, match=message), runner._subreaper():
        pass


def test_reaping_errors_are_not_hidden(mocker: MockerFixture) -> None:
    wait = mocker.patch.object(runner.os, "waitpid", side_effect=PermissionError)
    with pytest.raises(ArtifactError, match="cannot reap"):
        runner._reap_group(123)
    wait.assert_called_once_with(-123, runner.os.WNOHANG)


@pytest.mark.parametrize("probe", [True, False])
def test_group_permission_errors_are_safe(mocker: MockerFixture, probe: bool) -> None:
    mocker.patch.object(runner.os, "killpg", side_effect=PermissionError)
    with pytest.raises(ArtifactError, match="release command process group"):
        if probe:
            runner._group_exists(123)
        else:
            runner._signal_group(123, signal.SIGTERM)


def test_leader_cleanup_failure_is_not_hidden(mocker: MockerFixture) -> None:
    process = mocker.Mock(pid=123)
    process.wait.side_effect = subprocess.TimeoutExpired("fixed argv", 5)
    signal_group = mocker.patch.object(runner, "_signal_group")
    mocker.patch.object(runner, "_wait_group_exit", return_value=False)
    with pytest.raises(ArtifactError, match="leader could not be reaped"):
        runner._terminate_group(process, label="build")
    assert signal_group.call_args_list == [
        mocker.call(123, signal.SIGTERM),
        mocker.call(123, signal.SIGKILL),
    ]


def test_start_failure_is_safe(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.object(runner.subprocess, "Popen", side_effect=OSError("private"))
    with pytest.raises(ArtifactError, match=r"^build failed to start$"):
        runner._run_command(("fixed",), cwd=tmp_path, label="build", timeout=1)


def test_failed_exit_keeps_status(mocker: MockerFixture, tmp_path: Path) -> None:
    process = mocker.Mock(pid=123)
    process.wait.return_value = 17
    mocker.patch.object(runner.subprocess, "Popen", return_value=process)
    mocker.patch.object(runner, "_reap_group")
    mocker.patch.object(runner, "_group_exists", return_value=False)
    with pytest.raises(ArtifactError, match="failed with exit code 17"):
        runner._run_command(("fixed",), cwd=tmp_path, label="build", timeout=1)
