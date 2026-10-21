"""Focused stream-contract regressions for the reverse CLI final-image driver."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from tests.e2e import reverse_cli_workflow as workflow

pytestmark = pytest.mark.unit


def _result(*, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(("markweave",), 0, stdout, stderr)


def test_human_success_requires_stdout_without_stderr() -> None:
    workflow._require_human(
        _result(stdout="Reverse job is succeeded.\n"), "Reverse job"
    )

    with pytest.raises(workflow.WorkflowFailure, match="human command output"):
        workflow._require_human(
            _result(stderr="Reverse job is succeeded.\n"), "Reverse job"
        )


def test_human_error_requires_stderr_without_stdout() -> None:
    workflow._require_human_error(
        _result(stderr="error: The source extension is not supported.\n"),
        "not supported",
    )

    with pytest.raises(workflow.WorkflowFailure, match="human error output"):
        workflow._require_human_error(
            _result(stdout="error: The source extension is not supported.\n"),
            "not supported",
        )


def test_owner_denial_is_a_stderr_only_human_error() -> None:
    workflow._require_human_error(_result(stderr="error: Not found.\n"), "not found")

    with pytest.raises(workflow.WorkflowFailure, match="human error output"):
        workflow._require_human_error(
            _result(stdout="error: Not found.\n"), "not found"
        )


def test_json_error_reads_stderr_and_requires_the_stable_code(mocker) -> None:
    mocker.patch.object(
        workflow,
        "_command",
        return_value=_result(
            stderr='{"error":{"code":"needs_ocr","message":"OCR is unavailable."}}\n'
        ),
    )

    error = workflow._error_json(("markweave",), ("--json", "jobs", "reverse"))
    workflow._require_error_code(error, "needs_ocr")

    with pytest.raises(workflow.WorkflowFailure, match="reverse CLI error code"):
        workflow._require_error_code(error, "source_type_invalid")


def test_main_dispatches_structured_pptx_phase(mocker, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reverse_cli_workflow.py",
            "--container",
            "final-app",
            "--profile",
            "standalone",
            "--phase",
            "structured-pptx",
        ],
    )
    structured = mocker.patch.object(workflow, "_exercise_structured_pptx")

    assert workflow.main() == 0

    structured.assert_called_once_with("final-app", "standalone")


_JOB_ID = "00000000-0000-4000-8000-000000000901"
_CORRELATION_ID = "00000000-0000-4000-8000-000000000902"
_EXPIRES_AT = "2026-10-21T10:01:00+00:00"


def _expiry_state(tmp_path, profile: str = "distributed"):
    path = tmp_path / "reverse-expiry.json"
    path.write_text(
        json.dumps(
            {
                "schema": workflow._EXPIRY_SCHEMA,
                "profile": profile,
                "job_id": _JOB_ID,
                "correlation_id": _CORRELATION_ID,
                "expires_at": _EXPIRES_AT,
            }
        ),
        encoding="utf-8",
    )
    return path


def _shown(**changes: str) -> dict[str, str]:
    return {
        "id": _JOB_ID,
        "correlation_id": _CORRELATION_ID,
        "state": "expired",
        "expires_at": _EXPIRES_AT,
        "updated_at": "2026-10-21T10:01:01+00:00",
    } | changes


def _clock(*offsets: float):
    deadline = datetime.fromisoformat(_EXPIRES_AT)
    values = iter(offsets)
    last = [0.0]

    def now() -> datetime:
        last[0] = next(values, last[0])
        return deadline + timedelta(seconds=last[0])

    return now


def _expiry_doubles(mocker, shown: list[dict[str, str]]):
    mocker.patch.object(workflow, "_login")
    mocker.patch.object(workflow, "_exec_prefix", return_value=("markweave",))
    mocker.patch.object(workflow, "_json", side_effect=shown)
    command = mocker.patch.object(
        workflow, "_command", return_value=_result(stderr="error: Conflict.\n")
    )
    mocker.patch.object(
        workflow,
        "_error_json",
        return_value={"error": {"code": "reversion_conflict"}},
    )
    mocker.patch.object(workflow, "_container_path_exists", return_value=False)
    cleanup = mocker.patch.object(workflow, "_cleanup")
    return command, cleanup


def test_submit_expiry_records_the_retained_result_identity(mocker, tmp_path) -> None:
    mocker.patch.object(workflow, "_copy_source")
    mocker.patch.object(workflow, "_create_users")
    mocker.patch.object(workflow, "_login")
    mocker.patch.object(workflow, "_exec_prefix", return_value=("markweave",))
    mocker.patch.object(
        workflow,
        "_json",
        side_effect=[
            {"id": _JOB_ID, "state": "queued"},
            _shown(state="succeeded"),
        ],
    )
    path = tmp_path / "reverse-expiry.json"

    workflow._submit_expiry("final-app", "distributed", path)

    assert workflow._read_expiry_state(path, "distributed")["expires_at"] == (
        _EXPIRES_AT
    )
    assert path.stat().st_mode & 0o777 == 0o600


def test_verify_expiry_polls_then_requires_denial_and_cleans_up(
    mocker, tmp_path
) -> None:
    command, cleanup = _expiry_doubles(mocker, [_shown(state="succeeded"), _shown()])
    sleep = mocker.Mock()

    workflow._verify_expiry(
        "final-app",
        "distributed",
        _expiry_state(tmp_path),
        now=_clock(-5, -5, 2),
        sleep=sleep,
    )

    sleep.assert_called_once_with(1)
    assert command.call_args.kwargs == {"expected": 1}
    cleanup.assert_called_once_with("final-app")


@pytest.mark.parametrize(
    ("shown", "clock", "message"),
    [
        (
            [_shown(state="succeeded")],
            (0, workflow._EXPIRY_GRACE_SECONDS + 1),
            "did not expire within retention bound",
        ),
        ([_shown(state="failed")], (0, 0), "left succeeded state"),
        ([_shown(correlation_id=_JOB_ID)], (0, 0), "did not expire in place"),
        (
            [_shown(expires_at="2026-10-21T10:02:00+00:00")],
            (0, 0),
            "retention deadline changed",
        ),
        (
            [_shown(updated_at="2026-10-21T10:00:59+00:00")],
            (0, 0),
            "before its retention deadline",
        ),
    ],
)
def test_verify_expiry_rejects_late_early_or_substituted_expiry(
    mocker, tmp_path, shown, clock, message
) -> None:
    _expiry_doubles(mocker, shown)

    with pytest.raises(workflow.WorkflowFailure, match=message):
        workflow._verify_expiry(
            "final-app",
            "distributed",
            _expiry_state(tmp_path),
            now=_clock(*clock),
            sleep=mocker.Mock(),
        )


def test_expiry_state_rejects_another_profile_or_identity(tmp_path) -> None:
    with pytest.raises(workflow.WorkflowFailure, match="expiry state is invalid"):
        workflow._read_expiry_state(_expiry_state(tmp_path), "standalone")
    path = _expiry_state(tmp_path)
    state = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(state | {"job_id": "job"}), encoding="utf-8")
    with pytest.raises(workflow.WorkflowFailure, match="identity is invalid"):
        workflow._read_expiry_state(path, "distributed")


@pytest.mark.parametrize(
    ("phase", "target"),
    [("expiry-submit", "_submit_expiry"), ("expiry-verify", "_verify_expiry")],
)
def test_main_dispatches_expiry_phases_with_their_state_file(
    mocker, monkeypatch, tmp_path, phase, target
) -> None:
    arguments = [
        "reverse_cli_workflow.py",
        "--container",
        "final-app",
        "--profile",
        "standalone",
        "--phase",
        phase,
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit):
        workflow.main()
    state = tmp_path / "state.json"
    monkeypatch.setattr(sys, "argv", [*arguments, "--state-file", str(state)])
    dispatched = mocker.patch.object(workflow, target)

    assert workflow.main() == 0

    dispatched.assert_called_once_with("final-app", "standalone", state)
