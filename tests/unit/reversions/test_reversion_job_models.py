"""Unit coverage for closed durable reverse-job models."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

import markweave.reversion_jobs.ports
from markweave.reversion_jobs.models import (
    ReversionAttempt,
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseRecoveryResult,
    ReversionTraceMetadata,
    reversion_result_object_id,
)
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from markweave.reversions.formats import FormatFamily
from markweave.reversions.models import ReverseOutputMode
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    POLICY_SPECIFICATION,
    PRINCIPAL,
    proof,
    submission,
    trace,
)


@pytest.mark.unit
def test_lease_recovery_result_separates_outcomes_and_total_progress() -> None:
    result = ReversionLeaseRecoveryResult(2, 3, 4)

    assert result.outcomes == (2, 3, 4)
    assert result.progressed == 9
    with pytest.raises(ValueError, match="non-negative integers"):
        ReversionLeaseRecoveryResult(0, -1, 0)
    with pytest.raises(ValueError, match="non-negative integers"):
        ReversionLeaseRecoveryResult(0, True, 0)


@pytest.mark.unit
def test_reverse_queue_policy_requires_separately_injected_positive_values() -> None:
    assert markweave.reversion_jobs.ports.OwnerReversionRepository
    assert ReversionAdmissionPolicy(2, 5).global_queue_capacity == 5
    for values in ((0, 1), (1, 0), (True, 1)):
        with pytest.raises(ValueError):
            ReversionAdmissionPolicy(*values)


@pytest.mark.unit
def test_trace_metadata_is_closed_and_content_free() -> None:
    trace = ReversionTraceMetadata(
        1,
        "firecrawl-anydoc",
        "0.2.4",
        FormatFamily.WORD,
        "docx",
        ReverseOutputMode.MARKDOWN,
        0,
        0,
        0,
    )
    assert trace.local and not trace.ocr and not trace.hosted_fallback
    with pytest.raises(ValueError):
        replace(trace, engine_version="0.2.5")
    with pytest.raises(ValueError):
        replace(trace, asset_count=1)
    with pytest.raises(TypeError):
        replace(trace, filename="private.docx")
    csv_trace = replace(trace, source_family=FormatFamily.CSV, detected_format="csv")
    assert csv_trace.detected_format == "csv"
    with pytest.raises(ValueError):
        replace(trace, detected_format=None)
    with pytest.raises(ValueError):
        replace(trace, source_family=FormatFamily.CSV, detected_format="docx")
    mixed = replace(
        trace,
        result_mode=ReverseOutputMode.MARKDOWN_WITH_ASSETS,
        asset_count=2,
        asset_bytes=256,
        unavailable_asset_count=1,
    )
    assert mixed.unavailable_asset_count == 1
    unavailable = replace(
        trace,
        result_mode=ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
        unavailable_asset_count=1,
    )
    assert unavailable.unavailable_asset_count == 1


@pytest.mark.unit
def test_submission_and_result_identifier_reject_private_identity_lookalikes() -> None:
    valid = submission(uuid4())
    assert valid.created_at == NOW
    for changes in (
        {"source_stem": "../private"},
        {"admission": "docx"},
        {"source_sha256": "A" * 64},
        {"source_size": 0},
        {"component_versions": (("markweave", "0.6.1"),)},
        {"request_digest": "short"},
        {"correlation_id": "contains a space"},
        {"created_at": NOW.replace(tzinfo=None)},
    ):
        with pytest.raises(ValueError):
            replace(valid, **changes)
    with pytest.raises(ValueError):
        reversion_result_object_id(valid.id, 0)


@pytest.mark.unit
def test_attempt_validates_intent_proof_and_recovery_bundles() -> None:
    attempt_id = uuid4()
    base = ReversionAttempt(
        uuid4(),
        1,
        attempt_id,
        "worker",
        uuid4(),
        NOW,
        NOW,
        LEASE_END,
        PRINCIPAL.principal_id,
        1,
    )
    assert not base.recovery_blocked
    for changes in (
        {"attempt_number": 0},
        {"worker_id": ""},
        {"principal_id": "not-a-uuid"},
        {"create_sequence": 0},
        {"leased_at": NOW.replace(tzinfo=None)},
        {"unit_id": uuid4()},
        {"proof_recorded_at": NOW},
        {"recovery_owner": "worker"},
        {
            "recovery_owner": "",
            "recovery_token": uuid4(),
            "recovery_expires_at": LEASE_END,
        },
    ):
        with pytest.raises(ValueError):
            replace(base, **changes)
    intent = replace(
        base,
        create_intent_at=NOW,
        policy_revision="reverse-policy-v1",
        policy_specification=POLICY_SPECIFICATION,
    )
    assert intent.recovery_blocked
    with pytest.raises(ValueError):
        replace(intent, policy_revision=None)
    termination = proof(attempt_id, uuid4())
    proven = replace(
        intent,
        termination_proof=termination,
        proof_recorded_at=NOW + timedelta(seconds=1),
    )
    assert proven.unit_id == termination.unit_id and not proven.recovery_blocked
    with pytest.raises(ValueError):
        replace(intent, termination_proof=termination)
    with pytest.raises(ValueError):
        replace(
            intent,
            termination_proof=replace(termination, attempt_id=uuid4()),
            proof_recorded_at=NOW,
        )
    with pytest.raises(ValueError):
        replace(
            intent,
            termination_proof=termination,
            proof_recorded_at=NOW,
            proof_recovery_token=str(uuid4()),
        )
    with pytest.raises(ValueError):
        replace(
            proven,
            proof_acknowledged_at=NOW,
        )


@pytest.mark.unit
def test_job_validates_state_bundles_and_exact_anydoc_version() -> None:
    source = submission(uuid4())
    base = ReversionJob(
        source.id,
        source.owner_id,
        source.source_object_id,
        source.source_stem,
        source.admission,
        source.source_sha256,
        source.source_size,
        source.component_versions,
        source.request_digest,
        source.idempotency_digest,
        source.correlation_id,
        ReversionJobState.QUEUED,
        ReversionJobStep.QUEUED,
        NOW,
        NOW,
    )
    assert not base.terminal
    with pytest.raises(ValueError):
        replace(base, attempt=-1)
    with pytest.raises(ValueError):
        replace(base, admission="docx")
    with pytest.raises(ValueError):
        replace(base, source_size=0)
    with pytest.raises(ValueError):
        replace(base, state=ReversionJobState.RUNNING)
    running = replace(
        base,
        state=ReversionJobState.RUNNING,
        attempt=1,
        lease_owner="worker",
        lease_token=uuid4(),
        lease_expires_at=LEASE_END,
        heartbeat_at=NOW,
        current_attempt_id=uuid4(),
    )
    with pytest.raises(ValueError):
        replace(running, state=ReversionJobState.QUEUED)
    with pytest.raises(ValueError):
        replace(base, state=ReversionJobState.SUCCEEDED)
    with pytest.raises(ValueError):
        replace(base, result_object_id=uuid4())
    with pytest.raises(ValueError):
        replace(base, state=ReversionJobState.FAILED)
    with pytest.raises(ValueError):
        replace(base, error_code="failure", error_message="safe")
    succeeded = replace(
        base,
        state=ReversionJobState.SUCCEEDED,
        step=ReversionJobStep.COMPLETE,
        result_mode=ReverseOutputMode.MARKDOWN,
        result_object_id=uuid4(),
        result_sha256="a" * 64,
        result_size=1,
        trace=trace(),
    )
    assert succeeded.terminal
    with pytest.raises(ValueError):
        replace(succeeded, result_size=0)
    with pytest.raises(ValueError):
        replace(
            succeeded,
            trace=replace(
                trace(),
                result_mode=ReverseOutputMode.MARKDOWN_WITH_ASSETS,
                asset_count=1,
                asset_bytes=1,
            ),
        )
    with pytest.raises(ValueError, match="exact trace"):
        replace(succeeded, trace="not-a-trace")
    with pytest.raises(ValueError, match="does not match source admission"):
        replace(succeeded, trace=replace(trace(), detected_format="pptx"))
