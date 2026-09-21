"""Focused validation tests for the reverse lifecycle final-image driver."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
