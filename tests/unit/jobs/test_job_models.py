"""Unit coverage for conversion job invariants."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from md_converter.jobs.models import (
    ConversionJob,
    JobOutput,
    JobState,
    JobStep,
    result_object_id,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 24, tzinfo=UTC)


def job(**changes: object) -> ConversionJob:
    base = ConversionJob(
        id=uuid4(),
        owner_id=uuid4(),
        source_object_id=uuid4(),
        template_id=uuid4(),
        template_version_id=uuid4(),
        output=JobOutput.PDF,
        component_versions=(("md-converter", "0.1.0"),),
        state=JobState.QUEUED,
        step=JobStep.QUEUED,
        progress=0,
        request_digest="1" * 64,
        idempotency_digest=None,
        created_at=NOW,
        updated_at=NOW,
    )
    return replace(base, **changes)


def test_job_accepts_valid_queued_running_succeeded_and_failed_snapshots() -> None:
    assert not job().terminal
    assert not job(
        state=JobState.RUNNING,
        step=JobStep.VALIDATING,
        attempt=1,
        lease_owner="worker-1",
        lease_token=uuid4(),
        lease_expires_at=NOW + timedelta(seconds=10),
        heartbeat_at=NOW,
    ).terminal
    assert job(
        state=JobState.SUCCEEDED,
        step=JobStep.COMPLETE,
        progress=100,
        result_object_id=uuid4(),
        expires_at=NOW + timedelta(days=1),
    ).terminal
    assert job(
        state=JobState.FAILED,
        error_code="CONVERSION_FAILED",
        error_message="Conversion failed.",
        expires_at=NOW + timedelta(days=1),
    ).terminal


@pytest.mark.parametrize(
    "changes",
    (
        {"progress": -1},
        {"progress": 101},
        {"attempt": -1},
        {"state": JobState.RUNNING},
        {"lease_owner": "worker"},
        {"state": JobState.SUCCEEDED, "progress": 100},
        {"result_object_id": uuid4()},
        {"state": JobState.FAILED},
        {"error_code": "PRIVATE", "error_message": "private"},
        {"created_at": NOW.replace(tzinfo=None)},
    ),
)
def test_job_rejects_invalid_snapshots(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        job(**changes)


def test_attempt_result_identifiers_are_distinct_and_component_versions_are_strict() -> (
    None
):
    job_id = uuid4()
    assert result_object_id(job_id, 1) != result_object_id(job_id, 2)
    assert result_object_id(job_id, 1) == result_object_id(job_id, 1)
    with pytest.raises(ValueError, match="positive"):
        result_object_id(job_id, 0)
    with pytest.raises(ValueError, match="sorted"):
        job(component_versions=(("pandoc", "3.10.2"), ("app", "0.1.0")))
    with pytest.raises(ValueError, match="unique"):
        job(component_versions=(("app", "0.1.0"), ("app", "0.2.0")))
