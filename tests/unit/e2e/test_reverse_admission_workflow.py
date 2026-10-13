"""Validate the T141 admission-limit scenario evidence of the reverse driver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from scripts.e2e.admission_window_broker import REPORT_SCHEMA, WINDOWED_SLOTS
from tests.e2e import reverse_admission_workflow as workflow
from tests.e2e.service_workflow import WorkflowFailure

pytestmark = pytest.mark.unit

BASE_URL = "http://127.0.0.1:8080"
METRICS_URL = "http://127.0.0.1:8080/metrics"


def _report(
    exchanges: int = 10, refused: int = 0, immediate: int = 3
) -> dict[str, Any]:
    slots = {
        slot: {"acquired": exchanges, "refused": 0, "released": exchanges}
        | {"immediate": 0}
        for slot in WINDOWED_SLOTS
    }
    slots["_exchanges"]["immediate"] = immediate
    slots["_handlers"]["refused"] = refused
    return {"schema": REPORT_SCHEMA, "window_seconds": 0.15, "slots": slots}


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value))
    return path


def _metrics(
    transport: float = 0, fault: float = 0, ready: float = 1
) -> dict[str, float]:
    return {
        "broker_ready": ready,
        "broker_fault": fault,
        "faults_termination_unproven": 0,
        "faults_transport_failure": transport,
    }


def _state(path: Path, profile: str = "standalone", **overrides: Any) -> Path:
    state: dict[str, Any] = {
        "schema": workflow._SCHEMA,
        "profile": profile,
        "owner": f"t73-lifecycle-{profile}-admission-window",
        "job_id": str(uuid4()),
        "baseline": _metrics(transport=2),
        "baseline_exchanges": 2,
    }
    state.update(overrides)
    return _write(path, state)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report.update(schema="other"),
        lambda report: report.update(window_seconds=0),
        lambda report: report.update(window_seconds="0.15"),
        lambda report: report["slots"].pop("_handlers"),
        lambda report: report["slots"]["_exchanges"].pop("immediate"),
        lambda report: report["slots"]["_exchanges"].update(acquired=-1),
        lambda report: report["slots"]["_exchanges"].update(acquired=True),
    ],
)
def test_report_shape_is_validated(tmp_path: Path, mutation: Any) -> None:
    report = _report()
    mutation(report)

    with pytest.raises(WorkflowFailure, match="report is invalid"):
        workflow._read_report(_write(tmp_path / "report.json", report))


def test_unreadable_or_oversized_report_fails(tmp_path: Path) -> None:
    with pytest.raises(WorkflowFailure, match="unreadable"):
        workflow._read_report(tmp_path / "missing.json")
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b" " * 70_000)
    with pytest.raises(WorkflowFailure, match="byte limit"):
        workflow._read_report(oversized)


def test_refused_admission_names_its_slot() -> None:
    with pytest.raises(WorkflowFailure, match="_handlers"):
        workflow._require_no_refusal(_report(refused=2))
    workflow._require_no_refusal(_report())


def test_reconnection_wait_requires_one_windowed_exchange(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "report.json"
    mocker.patch.object(workflow.time, "sleep")
    clock = mocker.patch.object(
        workflow.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 61.0]
    )
    _write(path, _report(exchanges=0))
    with pytest.raises(WorkflowFailure, match="did not reconnect"):
        workflow._wait_reconnection(tmp_path / "absent.json")

    clock.side_effect = [0.0, 1.0]
    assert workflow._exchanges(workflow._wait_reconnection(_write(path, _report())))


def test_prepare_records_the_baseline_and_submits_one_job(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    report = _write(tmp_path / "report.json", _report(exchanges=4))
    mocker.patch.object(workflow, "_member_metrics", return_value=_metrics(2))
    admin = mocker.patch.object(workflow, "ServiceClient")
    create = mocker.patch.object(workflow, "create_user")
    mocker.patch.object(workflow.lifecycle, "_owner")
    mocker.patch.object(workflow.lifecycle, "_normalized_source", return_value=b"x")
    job_id = str(uuid4())
    submit = mocker.patch.object(
        workflow.lifecycle, "_submit", return_value={"id": job_id}
    )
    state_file = tmp_path / "state.json"

    workflow.prepare(BASE_URL, "distributed", state_file, METRICS_URL, report)

    admin.return_value.login.assert_called_once_with("e2e-admin", "e2e-admin-password")
    create.assert_called_once()
    assert submit.call_args.args[2] == "t141-admission-distributed"
    state = workflow._read_state(state_file, "distributed")
    assert state["job_id"] == job_id
    assert state["baseline_exchanges"] == 4
    assert state["baseline"] == _metrics(2)


def test_prepare_fails_before_submitting_after_a_refusal(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    report = _write(tmp_path / "report.json", _report(refused=1))
    submit = mocker.patch.object(workflow.lifecycle, "_submit")

    with pytest.raises(WorkflowFailure, match="refused"):
        workflow.prepare(
            BASE_URL, "standalone", tmp_path / "s.json", METRICS_URL, report
        )
    submit.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": "other"},
        {"profile": "distributed"},
        {"owner": "someone"},
        {"baseline": None},
        {"baseline_exchanges": "2"},
        {"job_id": "not-a-uuid"},
    ],
)
def test_state_identity_is_validated(tmp_path: Path, mutation: dict[str, Any]) -> None:
    with pytest.raises(WorkflowFailure, match=r"state is invalid|job identity"):
        workflow._read_state(_state(tmp_path / "s.json", **mutation), "standalone")
    with pytest.raises(WorkflowFailure, match="unreadable"):
        workflow._read_state(tmp_path / "missing.json", "standalone")


def _verify_setup(
    tmp_path: Path, mocker: MockerFixture, **options: Any
) -> tuple[Path, Path, Any]:
    report = options.get("report") or _report()
    attempt = options.get("attempt", 1)
    downloads = options.get("downloads", (b"zip", b"zip"))
    final = options.get("final") or _metrics(transport=2)
    state = _state(tmp_path / "state.json")
    report_path = _write(tmp_path / "report.json", report)
    mocker.patch.object(workflow.lifecycle, "_owner")
    mocker.patch.object(
        workflow.lifecycle, "_wait_success", return_value={"attempt": attempt}
    )
    mocker.patch.object(workflow.lifecycle, "_download", side_effect=downloads)
    inspect = mocker.patch.object(workflow.lifecycle, "_inspect_package")
    mocker.patch.object(workflow, "_member_metrics", return_value=final)
    return state, report_path, inspect


def test_verify_accepts_a_first_attempt_without_refusal_or_failure(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, report, inspect = _verify_setup(tmp_path, mocker)

    workflow.verify(BASE_URL, "standalone", state, METRICS_URL, report)

    inspect.assert_called_once_with(b"zip")


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"attempt": 2}, "needed recovery"),
        ({"downloads": (b"one", b"two")}, "changed between downloads"),
        ({"report": _report(refused=1)}, "refused"),
        ({"report": _report(exchanges=7)}, "did not exchange"),
        ({"report": _report(immediate=0)}, "inside the release window"),
        ({"final": _metrics(transport=3)}, "transport failure"),
        ({"final": _metrics(transport=2, fault=1)}, "transport failure"),
        ({"final": _metrics(transport=2, ready=0)}, "transport failure"),
    ],
)
def test_verify_rejects_every_missing_guarantee(
    tmp_path: Path, mocker: MockerFixture, options: dict[str, Any], message: str
) -> None:
    state, report, _inspect = _verify_setup(tmp_path, mocker, **options)

    with pytest.raises(WorkflowFailure, match=message):
        workflow.verify(BASE_URL, "standalone", state, METRICS_URL, report)


@pytest.mark.parametrize("operation", ["prepare", "verify"])
def test_main_reports_success_and_failure(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    operation: str,
) -> None:
    arguments = [
        "reverse_admission_workflow",
        operation,
        "--base-url",
        BASE_URL,
        "--profile",
        "standalone",
        "--state-file",
        str(tmp_path / "state.json"),
        "--metrics-url",
        METRICS_URL,
        "--admission-report",
        str(tmp_path / "report.json"),
    ]
    mocker.patch.object(workflow.sys, "argv", arguments)
    target = mocker.patch.object(workflow, operation)

    assert workflow.main() == 0
    target.assert_called_once()
    assert f"{operation} passed" in capsys.readouterr().out

    target.side_effect = WorkflowFailure("refused")
    assert workflow.main() == 1
    assert "refused" in capsys.readouterr().err
