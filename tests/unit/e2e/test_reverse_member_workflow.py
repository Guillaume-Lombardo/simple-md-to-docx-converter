"""Validate the unit-slice member scenario evidence of the reverse driver."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from tests.e2e import reverse_lifecycle_workflow as workflow

pytestmark = pytest.mark.unit

RELEASED = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)


def _state(scenario: str = "member-persistent") -> dict[str, Any]:
    return {
        "schema": workflow._MEMBER_SCHEMA,
        "profile": "standalone",
        "scenario": scenario,
        "owner": f"t73-lifecycle-standalone-{scenario}",
        "job_id": str(uuid4()),
    }


def _metrics(
    ready: float = 1, fault: float = 0, transport: float = 0, unproven: float = 0
) -> dict[str, float]:
    return {
        "broker_ready": ready,
        "broker_fault": fault,
        "faults_termination_unproven": unproven,
        "faults_transport_failure": transport,
    }


def _report(scenario: str, unit_id: str, **overrides: object) -> dict[str, Any]:
    persistent = scenario == "member-persistent"
    report: dict[str, Any] = {
        "schema": workflow._MEMBER_REPORT_SCHEMA,
        "mode": scenario.removeprefix("member-"),
        "phase": "proven",
        "failure": None,
        "unit_id": unit_id,
        "armed_state": "created",
        "held_state": "exit_confirmed",
        "final_state": "removed",
        "released_at": RELEASED.isoformat(),
        "baseline_metrics": _metrics(transport=1),
        "held_metrics": _metrics(ready=0, fault=1, transport=1, unproven=1)
        if persistent
        else _metrics(transport=1),
        "final_metrics": _metrics(transport=1, unproven=1 if persistent else 0),
        "timings_millis": {},
    }
    report.update(overrides)
    return report


def _row(job_id: str, number: int, unit_id: str, recorded: datetime) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "attempt_id": str(uuid4()),
        "attempt_number": number,
        "unit_id": unit_id,
        "proof_id": str(uuid4()),
        "proof_unit_id": unit_id,
        "proof_recorded_at": recorded.replace(tzinfo=None).isoformat(),
    }


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value))
    return path


def _verify(  # noqa: PLR0913 - explicit scenario evidence
    tmp_path: Path,
    mocker: MockerFixture,
    *,
    scenario: str,
    attempt: int,
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    mocker.patch.object(workflow, "_owner", return_value=object())
    mocker.patch.object(
        workflow,
        "_wait_success",
        return_value={"state": "succeeded", "attempt": attempt},
    )
    mocker.patch.object(workflow, "_download", return_value=b"package")
    mocker.patch.object(workflow, "_inspect_package")
    state_file = _write(tmp_path / "state.json", state)
    diagnostics = _write(
        tmp_path / "diagnostics.json",
        {
            "schema": workflow._DIAGNOSTICS_SCHEMA,
            "profile": state["profile"],
            "scenario": scenario,
            "job_ids": [state["job_id"]],
            "attempts": rows,
        },
    )
    observer = _write(tmp_path / "report.json", report)
    workflow.verify_member(
        "http://127.0.0.1:1", "standalone", scenario, state_file, diagnostics, observer
    )


def test_persistent_member_requires_recovery_and_proof_after_release(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state = _state()
    unit_id = str(uuid4())
    rows = [
        _row(state["job_id"], 1, unit_id, RELEASED + timedelta(seconds=3)),
        _row(state["job_id"], 2, str(uuid4()), RELEASED + timedelta(seconds=6)),
    ]
    _verify(
        tmp_path,
        mocker,
        scenario="member-persistent",
        attempt=2,
        report=_report("member-persistent", unit_id),
        rows=rows,
        state=state,
    )


def test_transient_member_publishes_on_the_first_attempt(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state = _state("member-transient")
    unit_id = str(uuid4())
    _verify(
        tmp_path,
        mocker,
        scenario="member-transient",
        attempt=1,
        report=_report("member-transient", unit_id),
        rows=[_row(state["job_id"], 1, unit_id, RELEASED + timedelta(seconds=1))],
        state=state,
    )


@pytest.mark.parametrize(
    ("scenario", "attempt", "report_overrides", "row_offset", "message"),
    [
        ("member-transient", 2, {}, 1, "forced a recovery attempt"),
        ("member-persistent", 1, {}, 1, "did not force recovery"),
        ("member-persistent", 2, {}, -1, "preceded the member release"),
        (
            "member-transient",
            1,
            {"held_metrics": _metrics(transport=1, unproven=1)},
            1,
            "degraded the broker",
        ),
        (
            "member-transient",
            1,
            {"final_metrics": _metrics(transport=2)},
            1,
            "past its deadline",
        ),
        (
            "member-persistent",
            2,
            {"held_metrics": _metrics(ready=0, fault=1, transport=1)},
            1,
            "did not fault the broker",
        ),
        (
            "member-persistent",
            2,
            {"held_metrics": _metrics(ready=0, fault=1, transport=2, unproven=1)},
            1,
            "past its deadline",
        ),
        (
            "member-persistent",
            2,
            {"final_metrics": _metrics(ready=0)},
            1,
            "readiness did not recover",
        ),
        ("member-persistent", 2, {"held_state": "removed"}, 1, "did not prove"),
        ("member-persistent", 2, {"armed_state": "exit_confirmed"}, 1, "did not prove"),
        ("member-persistent", 2, {"failure": "RuntimeError"}, 1, "did not prove"),
    ],
)
def test_member_verification_rejects_unproven_evidence(  # noqa: PLR0913, PLR0917
    tmp_path: Path,
    mocker: MockerFixture,
    scenario: str,
    attempt: int,
    report_overrides: dict[str, object],
    row_offset: int,
    message: str,
) -> None:
    state = _state(scenario)
    unit_id = str(uuid4())
    rows = [
        _row(
            state["job_id"],
            number,
            unit_id if number == 1 else str(uuid4()),
            RELEASED + timedelta(seconds=row_offset),
        )
        for number in range(1, attempt + 1)
    ]
    with pytest.raises(workflow.WorkflowFailure, match=message):
        _verify(
            tmp_path,
            mocker,
            scenario=scenario,
            attempt=attempt,
            report=_report(scenario, unit_id, **report_overrides),
            rows=rows,
            state=state,
        )


def test_member_verification_requires_the_exact_observed_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state = _state()
    rows = [
        _row(state["job_id"], 1, str(uuid4()), RELEASED + timedelta(seconds=1)),
        _row(state["job_id"], 2, str(uuid4()), RELEASED + timedelta(seconds=2)),
    ]
    with pytest.raises(workflow.WorkflowFailure, match="lacks its exact proof"):
        _verify(
            tmp_path,
            mocker,
            scenario="member-persistent",
            attempt=2,
            report=_report("member-persistent", str(uuid4())),
            rows=rows,
            state=state,
        )


def test_member_verification_requires_both_evidence_files(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state_file = _write(tmp_path / "state.json", _state())
    with pytest.raises(workflow.WorkflowFailure, match="needs diagnostics"):
        workflow.verify_member(
            "http://127.0.0.1:1",
            "standalone",
            "member-persistent",
            state_file,
            tmp_path / "diagnostics.json",
            None,
        )


def test_member_state_binds_diagnostics_to_its_single_job(tmp_path: Path) -> None:
    state = _state()
    path = _write(tmp_path / "state.json", state)

    assert workflow._diagnostic_state(path) == {
        "profile": "standalone",
        "scenario": "member-persistent",
        "job_ids": [state["job_id"]],
        "recovery_job_id": state["job_id"],
    }
    tampered = _write(tmp_path / "tampered.json", {**state, "owner": "someone"})
    with pytest.raises(workflow.WorkflowFailure, match="identity is invalid"):
        workflow._diagnostic_state(tampered)
    unreadable = tmp_path / "missing.json"
    with pytest.raises(workflow.WorkflowFailure):
        workflow._diagnostic_state(unreadable)


def test_member_diagnostics_reject_foreign_or_gapped_attempts(tmp_path: Path) -> None:
    state = _state()
    base = {
        "schema": workflow._DIAGNOSTICS_SCHEMA,
        "profile": "standalone",
        "scenario": "member-persistent",
        "job_ids": [state["job_id"]],
    }
    foreign = _row(str(uuid4()), 1, str(uuid4()), RELEASED)
    path = _write(tmp_path / "foreign.json", {**base, "attempts": [foreign]})
    with pytest.raises(workflow.WorkflowFailure, match="schema is invalid"):
        workflow._member_attempts(path, state)
    gapped = _row(state["job_id"], 2, str(uuid4()), RELEASED)
    path = _write(tmp_path / "gapped.json", {**base, "attempts": [gapped]})
    with pytest.raises(workflow.WorkflowFailure, match="generations are invalid"):
        workflow._member_attempts(path, state)


def test_prepare_member_submits_one_job(tmp_path: Path, mocker: MockerFixture) -> None:
    admin = mocker.Mock()
    mocker.patch.object(workflow, "ServiceClient", return_value=admin)
    create_user = mocker.patch.object(workflow, "create_user")
    mocker.patch.object(workflow, "_owner", return_value="owner")
    mocker.patch.object(workflow, "_normalized_source", return_value=b"docx")
    job_id = str(uuid4())
    submit = mocker.patch.object(workflow, "_submit", return_value={"id": job_id})
    state_file = tmp_path / "state.json"

    workflow.prepare_member(
        "http://127.0.0.1:1", "distributed", "member-transient", state_file
    )

    create_user.assert_called_once()
    submit.assert_called_once_with(
        "owner", b"docx", "t137-member-distributed-member-transient"
    )
    assert workflow._read_member_state(state_file)["job_id"] == job_id
    with pytest.raises(workflow.WorkflowFailure, match="scenario is invalid"):
        workflow.prepare_member(
            "http://127.0.0.1:1", "distributed", "worker-restart", state_file
        )
