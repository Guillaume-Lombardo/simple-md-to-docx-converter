"""Quarterly exercise wrapper delegates only to the production restore command."""

from __future__ import annotations

import sys

import pytest

from scripts import run_restore_exercise

pytestmark = pytest.mark.unit


def test_exercise_wrapper_forwards_structured_restore_arguments(
    monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    command = mocker.patch.object(
        run_restore_exercise, "markweave_main", return_value=0
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_restore_exercise.py",
            "--timeout",
            "30",
            "--",
            "--profile",
            "standalone",
            "--source",
            "/sets/id",
            "--data-directory",
            "/isolated/data",
            "--offline-proof",
            "window",
            "--report-directory",
            "/reports",
            "--evidence-id",
            "ready",
            "--yes",
        ],
    )
    assert run_restore_exercise.main() == 0
    assert command.call_args.args[0] == [
        "--non-interactive",
        "--timeout",
        "30.0",
        "restore",
        "--profile",
        "standalone",
        "--source",
        "/sets/id",
        "--data-directory",
        "/isolated/data",
        "--offline-proof",
        "window",
        "--report-directory",
        "/reports",
        "--evidence-id",
        "ready",
        "--yes",
    ]


def test_exercise_wrapper_rejects_arbitrary_programs_before_execution(
    monkeypatch: pytest.MonkeyPatch, mocker
) -> None:
    command = mocker.patch.object(run_restore_exercise, "markweave_main")
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_restore_exercise.py", "--", "/bin/sh", "-c", "unsafe"],
    )
    with pytest.raises(SystemExit) as raised:
        run_restore_exercise.main()
    assert raised.value.code == 2
    command.assert_not_called()
