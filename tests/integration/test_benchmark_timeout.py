from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

RUNNER = "scripts/javascript/run_bounded_benchmark_command.py"


def _assert_process_gone(process_id: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(process_id, signal.SIGKILL)
    pytest.fail(f"the supervised command left process {process_id} running")


@pytest.mark.integration
def test_benchmark_timeout_kills_descendant_process_group(tmp_path: Path) -> None:
    log = tmp_path / "timeout.log"
    descendant_pid_file = tmp_path / "descendant.pid"
    program = (
        "trap 'exit 0' TERM; "
        f'sh -c \'trap "" TERM; echo $$ > {descendant_pid_file!s}; '
        "while :; do sleep 30; done' & "
        "wait"
    )

    completed = subprocess.run(
        [
            RUNNER,
            "1",
            "1",
            str(log),
            "test/descendant",
            "--",
            "sh",
            "-c",
            program,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 124
    assert completed.stdout == ""
    assert "boundary_timeout label=test/descendant" in completed.stderr
    evidence = log.read_text(encoding="utf-8")
    assert "boundary_start label=test/descendant timeout_seconds=1" in evidence
    assert "boundary_timeout label=test/descendant" in evidence
    assert "deadline_reached=true" in evidence
    assert "normalized_status=124" in evidence

    descendant_pid = int(descendant_pid_file.read_text(encoding="utf-8"))
    _assert_process_gone(descendant_pid)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("program", "expected_status"),
    (("exit 124", 124), ("kill -TERM $$", 143), ("kill -KILL $$", 137)),
)
def test_benchmark_boundary_preserves_native_command_status(
    tmp_path: Path, program: str, expected_status: int
) -> None:
    log = tmp_path / "native-status.log"

    completed = subprocess.run(
        [
            RUNNER,
            "5",
            "1",
            str(log),
            "test/native-status",
            "--",
            "sh",
            "-c",
            program,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == expected_status
    evidence = log.read_text(encoding="utf-8")
    assert (
        "boundary_exit label=test/native-status deadline_reached=false "
        f"status={expected_status}"
    ) in evidence
    assert "boundary_timeout" not in evidence


@pytest.mark.integration
def test_deadline_records_direct_process_sigterm_status(tmp_path: Path) -> None:
    log = tmp_path / "deadline-status.log"

    completed = subprocess.run(
        [RUNNER, "1", "1", str(log), "test/deadline-status", "--", "sleep", "30"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 124
    evidence = log.read_text(encoding="utf-8")
    assert "deadline_reached=true observed_status=143 normalized_status=124" in evidence


@pytest.mark.integration
def test_nested_benchmark_boundaries_finish_inner_group_cleanup(tmp_path: Path) -> None:
    for attempt in range(3):
        outer_log = tmp_path / f"outer-{attempt}.log"
        inner_log = tmp_path / f"inner-{attempt}.log"
        descendant_pid_file = tmp_path / f"descendant-{attempt}.pid"
        program = (
            "trap 'exit 0' TERM; "
            f'sh -c \'trap "" TERM; echo $$ > {descendant_pid_file!s}; '
            "while :; do sleep 30; done' & "
            "wait"
        )

        completed = subprocess.run(
            [
                RUNNER,
                "1",
                "5",
                str(outer_log),
                f"test/outer/{attempt}",
                "--",
                RUNNER,
                "30",
                "1",
                str(inner_log),
                f"test/inner/{attempt}",
                "--",
                "sh",
                "-c",
                program,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )

        assert completed.returncode == 124
        assert "boundary_timeout" in outer_log.read_text(encoding="utf-8")
        descendant_pid = int(descendant_pid_file.read_text(encoding="utf-8"))
        _assert_process_gone(descendant_pid)


@pytest.mark.integration
def test_benchmark_timeout_kills_setsid_descendant(tmp_path: Path) -> None:
    log = tmp_path / "setsid.log"
    descendant_pid_file = tmp_path / "setsid-descendant.pid"
    program = (
        "trap 'exit 0' TERM; "
        f'setsid sh -c \'trap "" TERM; echo $$ > {descendant_pid_file!s}; '
        "while :; do sleep 30; done' & "
        "wait"
    )

    completed = subprocess.run(
        [RUNNER, "1", "1", str(log), "test/setsid", "--", "sh", "-c", program],
        check=False,
        capture_output=True,
        text=True,
        timeout=6,
    )

    assert completed.returncode == 124
    assert "deadline_reached=true" in log.read_text(encoding="utf-8")
    _assert_process_gone(int(descendant_pid_file.read_text(encoding="utf-8")))


@pytest.mark.integration
def test_second_term_does_not_interrupt_tree_cleanup(tmp_path: Path) -> None:
    log = tmp_path / "double-term.log"
    descendant_pid_file = tmp_path / "double-term-descendant.pid"
    program = (
        f'trap "" TERM; echo $$ > {descendant_pid_file!s}; while :; do sleep 30; done'
    )
    runner = subprocess.Popen(
        [RUNNER, "30", "1", str(log), "test/double-term", "--", "sh", "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 2
    while not descendant_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert descendant_pid_file.exists()

    runner.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    runner.send_signal(signal.SIGTERM)
    stdout, stderr = runner.communicate(timeout=5)

    assert runner.returncode == 143
    assert stdout == ""
    assert "boundary_signal label=test/double-term" in stderr
    _assert_process_gone(int(descendant_pid_file.read_text(encoding="utf-8")))
