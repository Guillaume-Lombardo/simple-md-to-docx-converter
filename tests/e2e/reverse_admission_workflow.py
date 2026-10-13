"""Drive the final-image worker through a broker at admission limit one (T141).

The harness replaces the fixture broker with the same broker at admission limit
one whose connection, pending-exchange and handler slots release only after a
fixed window. The final-image worker performs every exchange of its idle
reconciliation and of one real reverse job back to back. The scenario passes
only when the worker reconnected inside the window and the broker refused no
admission, the job succeeded on its first attempt, and the worker recorded no
broker transport failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from scripts.e2e.admission_window_broker import REPORT_SCHEMA, WINDOWED_SLOTS
from scripts.e2e.reverse_broker import _member_metrics
from tests.e2e import reverse_lifecycle_workflow as lifecycle
from tests.e2e.service_workflow import ServiceClient, WorkflowFailure, create_user

_SCHEMA: Final = "t141-reverse-admission-v1"
_SCENARIO: Final = "admission-window"
_MAX_REPORT_BYTES: Final = 65_536
_RECONNECTION_TIMEOUT_SECONDS: Final = 60.0
# A real reverse job performs at least create, stage, collect, terminate, proof
# and acknowledgement exchanges after the idle reconciliation exchanges.
_MIN_JOB_EXCHANGES: Final = 6


def _read_report(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_REPORT_BYTES:
            raise WorkflowFailure("admission window report exceeds its byte limit")
        report = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("admission window report is unreadable") from error
    slots = report.get("slots") if isinstance(report, dict) else None
    if (
        not isinstance(report, dict)
        or report.get("schema") != REPORT_SCHEMA
        or not isinstance(report.get("window_seconds"), float)
        or report["window_seconds"] <= 0
        or not isinstance(slots, dict)
        or set(slots) != set(WINDOWED_SLOTS)
        or any(
            not isinstance(counts, dict)
            or set(counts) != {"acquired", "refused", "released", "immediate"}
            or any(type(value) is not int or value < 0 for value in counts.values())
            for counts in slots.values()
        )
    ):
        raise WorkflowFailure("admission window report is invalid")
    return report


def _exchanges(report: dict[str, Any]) -> int:
    return report["slots"]["_exchanges"]["acquired"]


def _require_no_refusal(report: dict[str, Any]) -> None:
    refused = {
        slot: counts["refused"]
        for slot, counts in report["slots"].items()
        if counts["refused"]
    }
    if refused:
        raise WorkflowFailure(
            f"the broker refused an immediate reconnection at its limit: {refused}"
        )


def _wait_reconnection(report_path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + _RECONNECTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if report_path.exists():
            report = _read_report(report_path)
            if _exchanges(report) >= 1:
                return report
        time.sleep(0.1)
    raise WorkflowFailure("the worker did not reconnect to the windowed broker")


def _read_state(path: Path, profile: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise WorkflowFailure("admission window state is unreadable") from error
    if (
        not isinstance(state, dict)
        or set(state)
        != {"schema", "profile", "owner", "job_id", "baseline", "baseline_exchanges"}
        or state.get("schema") != _SCHEMA
        or state.get("profile") != profile
        or state.get("owner") != lifecycle._username(profile, _SCENARIO)
        or not isinstance(state.get("baseline"), dict)
        or type(state.get("baseline_exchanges")) is not int
    ):
        raise WorkflowFailure("admission window state is invalid")
    try:
        state["job_id"] = str(UUID(state["job_id"]))
    except (TypeError, ValueError) as error:
        raise WorkflowFailure("admission window job identity is invalid") from error
    return state


def prepare(
    base_url: str, profile: str, state_file: Path, metrics_url: str, report: Path
) -> None:
    """Wait for the worker on the windowed broker, then submit one reverse job."""
    reconnected = _wait_reconnection(report)
    _require_no_refusal(reconnected)
    baseline = _member_metrics(metrics_url)
    admin = ServiceClient(base_url)
    admin.login("e2e-admin", "e2e-admin-password")
    create_user(admin, lifecycle._username(profile, _SCENARIO), lifecycle._PASSWORD)
    owner = lifecycle._owner(base_url, profile, _SCENARIO)
    job_id = lifecycle._job_id(
        lifecycle._submit(
            owner, lifecycle._normalized_source(), f"t141-admission-{profile}"
        )
    )
    lifecycle._write_json(
        state_file,
        {
            "schema": _SCHEMA,
            "profile": profile,
            "owner": lifecycle._username(profile, _SCENARIO),
            "job_id": job_id,
            "baseline": baseline,
            "baseline_exchanges": _exchanges(reconnected),
        },
    )


def verify(
    base_url: str, profile: str, state_file: Path, metrics_url: str, report: Path
) -> None:
    """Require a first-attempt result with no refused or failed exchange."""
    state = _read_state(state_file, profile)
    owner = lifecycle._owner(base_url, profile, _SCENARIO)
    job = lifecycle._wait_success(owner, state["job_id"])
    if job.get("attempt") != 1:
        raise WorkflowFailure("the reverse job needed recovery at the admission limit")
    first = lifecycle._download(owner, state["job_id"])
    if first != lifecycle._download(owner, state["job_id"]):
        raise WorkflowFailure("one immutable reverse result changed between downloads")
    lifecycle._inspect_package(first)
    evidence = _read_report(report)
    _require_no_refusal(evidence)
    if _exchanges(evidence) - state["baseline_exchanges"] < _MIN_JOB_EXCHANGES:
        raise WorkflowFailure("the reverse job did not exchange through the window")
    if not any(counts["immediate"] for counts in evidence["slots"].values()):
        raise WorkflowFailure("no reconnection arrived inside the release window")
    final = _member_metrics(metrics_url)
    if (
        (final["faults_transport_failure"] or 0.0)
        - (state["baseline"]["faults_transport_failure"] or 0.0)
        > 0
        or final["broker_fault"] != 0
        or final["broker_ready"] != 1
    ):
        raise WorkflowFailure("the worker observed a broker transport failure")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--profile", choices=("standalone", "distributed"), required=True
    )
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--metrics-url", required=True)
    parser.add_argument("--admission-report", type=Path, required=True)
    args = parser.parse_args()
    operation = prepare if args.operation == "prepare" else verify
    try:
        operation(
            args.base_url,
            args.profile,
            args.state_file,
            args.metrics_url,
            args.admission_report,
        )
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        WorkflowFailure,
    ) as error:
        print(f"T141 reverse admission E2E failed: {error}", file=sys.stderr)
        return 1
    print(f"T141 reverse admission {args.operation} passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
