"""Deterministic unit coverage for the final-image service E2E driver."""

from __future__ import annotations

import io
import json
import stat
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from tests.e2e import service_workflow as workflow


def identifier() -> str:
    return str(uuid.uuid4())


def checkpoint() -> dict[str, str]:
    job_id = identifier()
    return {
        "schema": "t21-service-checkpoint-v1",
        "profile": "standalone",
        "owner": "e2e-admin",
        "location": f"/api/v1/conversions/{job_id}",
        "output": "both",
        "job_id": job_id,
        "correlation_id": identifier(),
        "template_id": identifier(),
        "template_version_id": identifier(),
        "result_sha256": "a" * 64,
    }


@pytest.mark.unit
def test_service_client_requires_safe_absolute_http_url() -> None:
    client = workflow.ServiceClient("https://example.test/service")
    assert client._prefix == "/service"
    for invalid in ("example.test", "ftp://example.test", "https:///missing"):
        with pytest.raises(ValueError, match="absolute HTTP"):
            workflow.ServiceClient(invalid)
    with pytest.raises(ValueError, match="query or fragment"):
        workflow.ServiceClient("https://example.test/?secret=no")


@pytest.mark.unit
def test_parser_preserves_every_worker_metrics_endpoint() -> None:
    arguments = workflow.build_parser().parse_args(
        [
            "exercise",
            "--base-url",
            "https://api.test",
            "--profile",
            "distributed",
            "--worker-metrics-url",
            "https://worker-one.test/metrics",
            "--worker-metrics-url",
            "https://worker-two.test/metrics",
        ]
    )
    assert arguments.worker_metrics_url == [
        "https://worker-one.test/metrics",
        "https://worker-two.test/metrics",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("attempt", (1, 3))
def test_recovery_rejects_missing_or_duplicate_reclaims(attempt: int) -> None:
    with pytest.raises(workflow.WorkflowFailure, match="reclaimed exactly once"):
        workflow.validate_recovery_attempt({"attempt": attempt}, previous_attempt=1)


@pytest.mark.unit
def test_recovery_accepts_exactly_one_reclaim() -> None:
    workflow.validate_recovery_attempt({"attempt": 2}, previous_attempt=1)


@pytest.mark.unit
def test_conversion_artifact_is_private_and_bounded_to_job_identity(
    tmp_path: Path,
) -> None:
    job_id = identifier()
    workflow.retain_conversion_artifact(
        tmp_path, job_id=job_id, output="both", content=b"synthetic result"
    )
    directory = tmp_path / "conversion-results"
    artifact = directory / f"{job_id}.zip"
    assert artifact.read_bytes() == b"synthetic result"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


@pytest.mark.unit
def test_expect_reports_only_status_and_stable_code() -> None:
    result = workflow.HttpResult(
        409,
        {},
        b'{"error":{"code":"CONVERSION_CONFLICT","message":"private body"}}',
        (),
    )
    with pytest.raises(workflow.WorkflowFailure) as failure:
        workflow.expect(result, 202, "submit")
    assert str(failure.value) == (
        "submit: HTTP 409, expected 202, code CONVERSION_CONFLICT"
    )
    assert "private body" not in str(failure.value)


@pytest.mark.unit
def test_json_request_forwards_template_precondition_before_auth_check(
    mocker: MockerFixture,
) -> None:
    client = mocker.Mock(spec=workflow.ServiceClient)
    client.request.return_value = workflow.HttpResult(
        403,
        {},
        b'{"error":{"code":"FORBIDDEN","message":"safe"}}',
        (),
    )
    result = workflow.json_request(
        client,
        "PATCH",
        f"/api/v1/templates/{identifier()}",
        {"name": "Forbidden", "description": "Forbidden"},
        expected=403,
        operation="owner denial",
        headers={"If-Match": 'W/"template-revision-1"'},
    )
    assert workflow.error_payload(result)["code"] == "FORBIDDEN"
    assert client.request.call_args.kwargs["headers"] == {
        "If-Match": 'W/"template-revision-1"'
    }
    assert client.request.call_args.kwargs["mutate"] is True


@pytest.mark.unit
def test_submission_contract_is_isolated_under_concurrent_validation() -> None:
    submissions: list[tuple[dict[str, Any], str, str]] = []
    for _index in range(100):
        job_id = identifier()
        correlation = identifier()
        submissions.append(
            (
                {"id": job_id, "correlation_id": correlation},
                f"/api/v1/conversions/{job_id}",
                correlation,
            )
        )

    def validate(submission: tuple[dict[str, Any], str, str]) -> None:
        job, location, correlation = submission
        workflow.validate_submission_contract(job, location, correlation)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(validate, submission) for submission in submissions]
    assert all(future.result() is None for future in futures)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("location", "correlation", "job_correlation", "message"),
    (
        ("", "valid", "valid", "Location header is missing"),
        ("/wrong", "valid", "valid", "Location does not match job identity"),
        ("expected", "", "valid", "correlation header is missing"),
        ("expected", "not-a-uuid", "not-a-uuid", "correlation UUID is invalid"),
        (
            "expected",
            "00000000-0000-1000-8000-000000000001",
            "00000000-0000-1000-8000-000000000001",
            "correlation UUID is not version 4",
        ),
        (
            "expected",
            "generated",
            "different",
            "durable correlation identifiers differ",
        ),
    ),
)
def test_submission_contract_reports_each_invariant_precisely(
    location: str, correlation: str, job_correlation: str, message: str
) -> None:
    job_id = identifier()
    generated = identifier()
    resolved_location = (
        f"/api/v1/conversions/{job_id}" if location == "expected" else location
    )
    resolved_correlation = generated if correlation == "generated" else correlation
    resolved_job_correlation = (
        generated if job_correlation == "valid" else job_correlation
    )
    with pytest.raises(workflow.WorkflowFailure, match=message):
        workflow.validate_submission_contract(
            {"id": job_id, "correlation_id": resolved_job_correlation},
            resolved_location,
            resolved_correlation,
        )


@pytest.mark.unit
def test_checkpoint_round_trip_is_content_free_and_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    expected = checkpoint()
    workflow.write_state(path, expected)
    assert workflow.read_state(path, expected_profile="standalone") == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rendered = path.read_text(encoding="utf-8")
    assert "password" not in rendered
    assert "source content" not in rendered


@pytest.mark.unit
def test_checkpoint_rejects_profile_schema_and_identifier_changes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.json"
    payload = checkpoint()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(workflow.WorkflowFailure, match="profile or version"):
        workflow.read_state(path, expected_profile="distributed")
    payload["job_id"] = "../private"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(workflow.WorkflowFailure, match="location mismatch"):
        workflow.read_state(path, expected_profile="standalone")
    payload = checkpoint() | {"secret": "must-not-be-accepted"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(workflow.WorkflowFailure, match="invalid schema"):
        workflow.read_state(path, expected_profile="standalone")


@pytest.mark.unit
def test_recovery_state_requires_exact_durable_identity(tmp_path: Path) -> None:
    job_id = identifier()
    expected = {
        "schema": "t21-service-recovery-v1",
        "profile": "distributed",
        "location": f"/api/v1/conversions/{job_id}",
        "output": "docx",
        "job_id": job_id,
        "correlation_id": identifier(),
        "attempt": "1",
    }
    path = tmp_path / "recovery.json"
    workflow.write_state(path, expected)
    assert (
        workflow.read_recovery_state(path, expected_profile="distributed") == expected
    )
    expected["attempt"] = "not-an-integer"
    path.write_text(json.dumps(expected), encoding="utf-8")
    with pytest.raises(workflow.WorkflowFailure, match="durable identity"):
        workflow.read_recovery_state(path, expected_profile="distributed")


@pytest.mark.unit
def test_docx_validation_accepts_required_parts_and_rejects_other_bytes() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            archive.writestr(name, b"safe")
    workflow.validate_docx(output.getvalue(), "test document")
    with pytest.raises(workflow.WorkflowFailure, match="invalid OpenXML"):
        workflow.validate_docx(b"not a ZIP", "test document")


@pytest.mark.unit
def test_real_engine_failure_sources_are_bounded_and_deterministic() -> None:
    mermaid = workflow.long_mermaid_source(8)
    assert mermaid.count(b"```mermaid") == 8
    assert len(mermaid) < 10_000
    assert mermaid == workflow.long_mermaid_source(8)
    multipage = workflow.multipage_markdown(400)
    assert multipage.count(b"Bounded final-image") == 400 * 8
    assert 50_000 < len(multipage) < 1_000_000
    assert multipage == workflow.multipage_markdown(400)


@pytest.mark.unit
def test_real_engine_failure_sources_reject_unbounded_counts() -> None:
    for diagrams in (0, 21):
        with pytest.raises(ValueError, match="diagram count"):
            workflow.long_mermaid_source(diagrams)
    for paragraphs in (99, 1_001):
        with pytest.raises(ValueError, match="paragraph count"):
            workflow.multipage_markdown(paragraphs)


@pytest.mark.unit
def test_terminal_cancellation_does_not_require_transient_request_flag() -> None:
    job_id = identifier()
    workflow.require_cancelled_terminal(
        {"id": job_id, "state": "cancelled", "cancel_requested": False},
        {"id": job_id, "state": "running", "cancel_requested": False},
    )
    with pytest.raises(workflow.WorkflowFailure, match="cancelled state missing"):
        workflow.require_cancelled_terminal(
            {"id": job_id, "state": "succeeded"},
            {"id": job_id, "state": "running"},
        )
    with pytest.raises(workflow.WorkflowFailure, match="durable identity changed"):
        workflow.require_cancelled_terminal(
            {"id": identifier(), "state": "cancelled"},
            {"id": job_id, "state": "running"},
        )


@pytest.mark.unit
def test_cli_validation_requires_command_inputs_and_positive_timeout(
    tmp_path: Path,
) -> None:
    parser = workflow.build_parser()
    missing_template = parser.parse_args(
        ["checkpoint", "--base-url", "http://service", "--profile", "standalone"]
    )
    with pytest.raises(SystemExit):
        workflow.validate_arguments(parser, missing_template)
    invalid_timeout = parser.parse_args(
        [
            "verify-recovery",
            "--base-url",
            "http://service",
            "--profile",
            "distributed",
            "--state-file",
            str(tmp_path / "state"),
            "--timeout-seconds",
            "0",
        ]
    )
    with pytest.raises(SystemExit):
        workflow.validate_arguments(parser, invalid_timeout)


@pytest.mark.unit
def test_failure_artifact_is_bounded_private_and_sanitized_by_caller(
    tmp_path: Path,
) -> None:
    workflow.write_failure_artifact(
        tmp_path, profile="standalone", message="safe failure" * 100
    )
    path = tmp_path / "service-failure.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["profile"] == "standalone"
    assert len(payload["message"]) == 300
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
