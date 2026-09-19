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


def test_continuous_exited_children_cannot_monopolize_reaping(
    mocker: MockerFixture,
) -> None:
    wait = mocker.patch.object(runner.os, "waitpid", return_value=(456, 0))
    assert runner._reap_group(123) is False
    assert wait.call_count == runner.REAP_BATCH_SIZE


def test_continuous_reaping_preserves_group_cleanup_deadline(
    mocker: MockerFixture,
) -> None:
    process = mocker.Mock(pid=123)
    process.poll.return_value = 0
    mocker.patch.object(runner.os, "waitpid", return_value=(456, 0))
    mocker.patch.object(runner, "_group_exists", return_value=True)
    mocker.patch.object(runner.time, "monotonic", side_effect=(0, 6))
    assert runner._wait_group_exit(process, timeout=5) is False


def test_continuous_reaping_after_leader_exit_still_attempts_termination(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    process = mocker.Mock(pid=123)
    process.wait.return_value = 0
    mocker.patch.object(runner.subprocess, "Popen", return_value=process)
    mocker.patch.object(runner.os, "waitpid", return_value=(456, 0))
    mocker.patch.object(runner.time, "monotonic", side_effect=(0, 6))
    mocker.patch.object(runner, "_group_exists", return_value=True)
    terminate = mocker.patch.object(runner, "_terminate_group")
    with pytest.raises(ArtifactError, match="left descendant processes running"):
        runner._run_command(("fixed",), cwd=tmp_path, label="build", timeout=1)
    terminate.assert_called_once_with(process, label="build")


def test_finite_zombie_batches_after_leader_exit_can_succeed(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    process = mocker.Mock(pid=123)
    process.wait.return_value = 0
    mocker.patch.object(runner.subprocess, "Popen", return_value=process)
    wait = mocker.patch.object(
        runner.os,
        "waitpid",
        side_effect=[(456, 0)] * (runner.REAP_BATCH_SIZE + 1) + [ChildProcessError],
    )
    mocker.patch.object(runner, "_group_exists", return_value=False)
    runner._run_command(("fixed",), cwd=tmp_path, label="build", timeout=1)
    assert wait.call_count == runner.REAP_BATCH_SIZE + 2
