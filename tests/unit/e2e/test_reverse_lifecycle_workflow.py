"""Focused validation tests for the reverse lifecycle final-image driver."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.e2e import reverse_lifecycle_workflow as workflow

pytestmark = pytest.mark.unit


def _state() -> dict[str, Any]:
    jobs = [str(uuid4()) for _ in range(4)]
    return {
        "schema": workflow._SCHEMA,
        "profile": "standalone",
        "scenario": "worker-restart",
        "owner": "t73-lifecycle-standalone-worker-restart",
        "job_ids": jobs,
        "recovery_job_id": jobs[0],
        "shared_job_id": jobs[1],
        "forward_job_id": str(uuid4()),
        "forward_evidence": None,
    }


def _attempt(
    job_id: str, number: int, sequence: int, start: datetime
) -> dict[str, object]:
    attempt_id = str(uuid4())
    unit_id = str(uuid4())
    recorded = start + timedelta(seconds=1)
    return {
        "job_id": job_id,
        "attempt_id": attempt_id,
        "attempt_number": number,
        "leased_at": start.isoformat(),
        "lease_expires_at": (start + timedelta(seconds=30)).isoformat(),
        "create_sequence": sequence,
        "create_intent_at": start.isoformat(),
        "unit_id": unit_id,
        "proof_id": str(uuid4()),
        "proof_unit_id": unit_id,
        "proof_recorded_at": recorded.isoformat(),
        "proof_acknowledged_at": recorded.isoformat(),
    }


def test_diagnostics_require_serial_fenced_attempts(tmp_path) -> None:
    state = _state()
    start = datetime(2026, 9, 21, tzinfo=UTC)
    recovery_id = state["recovery_job_id"]
    attempts = [
        _attempt(recovery_id, 1, 1, start),
        _attempt(recovery_id, 2, 2, start + timedelta(seconds=2)),
    ]
    jobs = {recovery_id: {"attempt": 2}}
    for index, job_id in enumerate(state["job_ids"][1:], start=2):
        attempts.append(
            _attempt(job_id, 1, index + 1, start + timedelta(seconds=index * 2))
        )
        jobs[job_id] = {"attempt": 1}
    evidence = {
        "schema": workflow._DIAGNOSTICS_SCHEMA,
        "profile": "standalone",
        "scenario": "worker-restart",
        "job_ids": state["job_ids"],
        "attempts": attempts,
    }
    path = tmp_path / "diagnostics.json"
    path.write_text(json.dumps(evidence))
    workflow._validate_diagnostics(path, state, jobs)

    first = attempts[0]
    for field in (
        "create_intent_at",
        "unit_id",
        "proof_id",
        "proof_unit_id",
        "proof_recorded_at",
        "proof_acknowledged_at",
    ):
        first[field] = None
    path.write_text(json.dumps(evidence))
    with pytest.raises(workflow.WorkflowFailure, match="real-unit proof"):
        workflow._validate_diagnostics(path, state, jobs)

    attempts[0] = _attempt(recovery_id, 1, 1, start)
    attempts[1]["leased_at"] = (start + timedelta(milliseconds=500)).isoformat()
    path.write_text(json.dumps(evidence))
    with pytest.raises(workflow.WorkflowFailure, match="intervals overlap"):
        workflow._validate_diagnostics(path, state, jobs)


def test_state_rejects_duplicate_or_foreign_job_identity(tmp_path) -> None:
    state = _state()
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    assert workflow._read_state(path, "standalone", "worker-restart") == state

    state["job_ids"][1] = state["job_ids"][0]
    path.write_text(json.dumps(state))
    with pytest.raises(workflow.WorkflowFailure, match="job set"):
        workflow._read_state(path, "standalone", "worker-restart")

    state = _state()
    state["forward_evidence"] = {
        "job_id": str(uuid4()),
        "updated_at": "2026-09-21T00:00:00+00:00",
    }
    path.write_text(json.dumps(state))
    with pytest.raises(workflow.WorkflowFailure, match="forward evidence"):
        workflow._read_state(path, "standalone", "worker-restart")


def test_diagnostic_watcher_announces_after_first_targeted_query(
    tmp_path, mocker
) -> None:
    state = _state()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    output = tmp_path / "binding.json"
    ready = tmp_path / "watching.json"
    attempt_id = str(uuid4())
    values = {
        "job_id": state["recovery_job_id"],
        "attempt_id": attempt_id,
        "attempt_number": 1,
        "leased_at": None,
        "lease_expires_at": None,
        "create_sequence": None,
        "create_intent_at": None,
        "unit_id": None,
        "proof_id": None,
        "proof_unit_id": None,
        "proof_recorded_at": None,
        "proof_acknowledged_at": None,
    }
    row = SimpleNamespace(**values, _mapping=values)
    connection = mocker.MagicMock()

    def execute(_statement):
        if connection.execute.call_count == 1:
            assert not ready.exists()
            return SimpleNamespace(all=list)
        assert ready.exists()
        return SimpleNamespace(all=lambda: [row])

    connection.execute.side_effect = execute
    connection.__enter__.return_value = connection
    engine = mocker.Mock()
    engine.connect.return_value = connection
    mocker.patch.object(workflow, "create_database_engine", return_value=engine)
    mocker.patch.object(
        workflow,
        "Settings",
        return_value=SimpleNamespace(
            storage_profile=workflow.StorageProfile.STANDALONE,
            standalone_data_directory=tmp_path,
        ),
    )
    mocker.patch.object(workflow.time, "sleep")

    workflow.diagnostics(
        state_path,
        output,
        wait_for_recovery_attempt=True,
        ready_marker=ready,
    )

    assert connection.execute.call_count == 2
    assert json.loads(ready.read_text()) == {
        "schema": "t73-reverse-diagnostics-watcher-v1",
        "recovery_job_id": state["recovery_job_id"],
    }
    assert json.loads(output.read_text())["attempts"][0]["attempt_id"] == attempt_id
    engine.dispose.assert_called_once_with()


def test_result_receipt_is_exact_bounded_proof_bound_metadata(tmp_path, mocker) -> None:
    state = _state()
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state))
    receipt = tmp_path / "result.json"

    with pytest.raises(workflow.WorkflowFailure, match="persisted fencing"):
        workflow.verify(
            "http://unused",
            "standalone",
            "worker-restart",
            state_path,
            None,
            receipt,
        )
    assert not receipt.exists()

    package = b"inspected deterministic package"
    mocker.patch.object(workflow, "_owner", return_value=mocker.sentinel.owner)
    mocker.patch.object(
        workflow,
        "_wait_success",
        side_effect=lambda _client, job_id: {
            "attempt": 2 if job_id == state["recovery_job_id"] else 1
        },
    )
    wait_forward = mocker.patch.object(
        workflow,
        "_wait_forward_success",
        return_value={
            "id": state["forward_job_id"],
            "updated_at": "2026-09-21T00:00:00+00:00",
        },
    )
    mocker.patch.object(
        workflow,
        "_timestamp",
        side_effect=lambda _value, field: (
            datetime(2026, 9, 21, tzinfo=UTC)
            + (timedelta(seconds=1) if field == "reverse updated_at" else timedelta())
        ),
    )
    mocker.patch.object(workflow, "_download", return_value=package)
    mocker.patch.object(workflow, "_inspect_package")
    mocker.patch.object(workflow, "_normalized_source", return_value=b"source")
    mocker.patch.object(
        workflow, "_submit", return_value={"id": state["shared_job_id"]}
    )
    validated = mocker.patch.object(workflow, "_validate_diagnostics")
    diagnostics = tmp_path / "diagnostics.json"

    validated.side_effect = workflow.WorkflowFailure("invalid persisted proof")
    with pytest.raises(workflow.WorkflowFailure, match="invalid persisted proof"):
        workflow.verify(
            "http://unused",
            "standalone",
            "worker-restart",
            state_path,
            diagnostics,
            receipt,
        )
    assert not receipt.exists()
    retained_state = json.loads(state_path.read_text())
    assert retained_state["forward_evidence"] == {
        "job_id": state["forward_job_id"],
        "updated_at": "2026-09-21T00:00:00+00:00",
    }
    validated.reset_mock(side_effect=True)

    workflow.verify(
        "http://unused",
        "standalone",
        "worker-restart",
        state_path,
        diagnostics,
        receipt,
    )

    validated.assert_called_once()
    wait_forward.assert_called_once()
    assert json.loads(receipt.read_text()) == {
        "schema": workflow._RESULT_RECEIPT_SCHEMA,
        "profile": "standalone",
        "scenario": "worker-restart",
        "recovery_job_id": state["recovery_job_id"],
        "sha256": sha256(package).hexdigest(),
    }
    assert receipt.stat().st_mode & 0o777 == 0o600
