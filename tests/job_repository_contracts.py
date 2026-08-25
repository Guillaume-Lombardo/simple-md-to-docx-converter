"""Reusable durable queue contract for both supported SQL profiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from markweave.jobs.errors import JobLeaseLostError
from markweave.jobs.models import (
    JobFailure,
    JobOutput,
    JobState,
    JobStep,
    JobSubmission,
    LeaseHeartbeat,
    SourceKind,
)
from markweave.persistence.jobs import SqlJobRepository

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
LEASE_END = NOW + timedelta(seconds=30)
RETENTION_END = NOW + timedelta(hours=1)
TEMPLATE_ID = UUID("00000000-0000-4000-8000-000000000015")
TEMPLATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000115")


def submission(
    owner_id: UUID,
    *,
    created_at: datetime = NOW,
    idempotency_digest: str | None = None,
) -> JobSubmission:
    return JobSubmission(
        id=uuid4(),
        owner_id=owner_id,
        source_object_id=uuid4(),
        template_id=TEMPLATE_ID,
        template_version_id=TEMPLATE_VERSION_ID,
        output=JobOutput.BOTH,
        component_versions=(("md-converter", "0.1.0"),),
        request_digest="1" * 64,
        idempotency_digest=idempotency_digest,
        created_at=created_at,
    )


def exercise_job_repository_contract(  # noqa: PLR0915
    repository: SqlJobRepository, owner_id: UUID, other_owner_id: UUID
) -> None:
    first_submission = submission(owner_id, idempotency_digest="2" * 64)
    first, replayed = repository.create(first_submission)
    assert not replayed
    assert first.state is JobState.QUEUED
    assert first.step is JobStep.QUEUED
    assert not first.source_ready
    assert first.source_filename == "source.md"
    assert first.source_kind is SourceKind.MARKDOWN
    assert first.source_sha256 == "0" * 64
    assert first.source_size == 1
    first = repository.activate_source(first.id, NOW)
    assert first.source_ready

    replay, replayed = repository.create(
        submission(owner_id, idempotency_digest="2" * 64)
    )
    assert replayed
    assert replay.id == first.id
    other_job, other_replayed = repository.create(
        submission(other_owner_id, idempotency_digest="2" * 64)
    )
    assert not other_replayed
    repository.activate_source(other_job.id, NOW)

    second, _ = repository.create(
        submission(owner_id, created_at=NOW + timedelta(seconds=1))
    )
    second = repository.activate_source(second.id, NOW + timedelta(seconds=1))
    page = repository.list_owner(owner_id, offset=0, limit=1)
    assert page.total == 2
    assert [item.id for item in page.items] == [second.id]
    assert repository.get(uuid4()) is None

    assert (
        repository.request_cancel(first.id, other_owner_id, NOW, RETENTION_END) is None
    )
    cancelled = repository.request_cancel(first.id, owner_id, NOW, RETENTION_END)
    assert cancelled is not None
    assert cancelled.state is JobState.CANCELLED
    assert cancelled.expires_at == RETENTION_END

    claimed = repository.claim("worker-a", NOW, LEASE_END)
    assert claimed is not None
    assert claimed.id != first.id
    assert claimed.state is JobState.RUNNING
    assert claimed.attempt == 1
    assert claimed.lease_token is not None
    other_claim = repository.claim("worker-b", NOW, LEASE_END)
    assert other_claim is not None

    heartbeat_at = NOW + timedelta(seconds=5)
    assert repository.heartbeat(
        LeaseHeartbeat(
            claimed.id,
            "worker-a",
            claimed.lease_token,
            heartbeat_at,
            heartbeat_at + timedelta(seconds=30),
            JobStep.RENDERING,
            40,
        )
    )
    newest_heartbeat = NOW + timedelta(seconds=10)
    assert repository.heartbeat(
        LeaseHeartbeat(
            claimed.id,
            "worker-a",
            claimed.lease_token,
            newest_heartbeat,
            newest_heartbeat + timedelta(seconds=30),
            JobStep.PDF,
            60,
        )
    )
    assert not repository.heartbeat(
        LeaseHeartbeat(
            claimed.id,
            "worker-a",
            claimed.lease_token,
            NOW + timedelta(seconds=6),
            NOW + timedelta(seconds=36),
            JobStep.DOCX,
            50,
        )
    )
    heartbeat_snapshot = repository.get(claimed.id)
    assert heartbeat_snapshot is not None
    assert heartbeat_snapshot.heartbeat_at == newest_heartbeat
    assert heartbeat_snapshot.progress == 60
    assert not repository.heartbeat(
        LeaseHeartbeat(
            claimed.id,
            "wrong-worker",
            claimed.lease_token,
            heartbeat_at,
            heartbeat_at + timedelta(seconds=30),
            JobStep.PDF,
            60,
        )
    )
    assert not repository.cancellation_requested(
        claimed.id, "worker-a", claimed.lease_token
    )
    heartbeat_at = newest_heartbeat
    cancelling = repository.request_cancel(
        claimed.id, claimed.owner_id, heartbeat_at, RETENTION_END
    )
    assert cancelling is not None and cancelling.cancel_requested
    assert repository.cancellation_requested(
        claimed.id, "worker-a", claimed.lease_token
    )
    finished = repository.finish_cancelled(
        claimed.id, "worker-a", claimed.lease_token, heartbeat_at, RETENTION_END
    )
    assert finished.state is JobState.CANCELLED

    successful_submission = submission(owner_id, created_at=NOW + timedelta(seconds=2))
    successful, _ = repository.create(successful_submission)
    repository.activate_source(successful.id, NOW)
    successful_claim = repository.claim("worker-success", NOW, LEASE_END)
    assert successful_claim is not None and successful_claim.id == successful.id
    assert successful_claim.lease_token is not None
    result_id = uuid4()
    manifest_id = uuid4()
    succeeded = repository.succeed(
        successful.id,
        "worker-success",
        successful_claim.lease_token,
        result_id,
        NOW,
        RETENTION_END,
        result_manifest_object_id=manifest_id,
    )
    assert succeeded.state is JobState.SUCCEEDED
    assert succeeded.result_object_id == result_id
    assert succeeded.result_manifest_object_id == manifest_id
    with pytest.raises(JobLeaseLostError):
        repository.succeed(
            successful.id,
            "worker-success",
            successful_claim.lease_token,
            uuid4(),
            NOW,
            RETENTION_END,
        )

    failed_submission = submission(owner_id, created_at=NOW + timedelta(seconds=3))
    failed, _ = repository.create(failed_submission)
    repository.activate_source(failed.id, NOW)
    failed_claim = repository.claim("worker-failure", NOW, LEASE_END)
    assert failed_claim is not None and failed_claim.id == failed.id
    assert failed_claim.lease_token is not None
    failure = repository.fail(
        JobFailure(
            failed.id,
            "worker-failure",
            failed_claim.lease_token,
            "safe_failure",
            "Conversion failed safely.",
            NOW,
            RETENTION_END,
        )
    )
    assert failure.state is JobState.FAILED
    assert failure.error_code == "safe_failure"

    recover_submission = submission(owner_id, created_at=NOW + timedelta(seconds=4))
    recoverable, _ = repository.create(recover_submission)
    repository.activate_source(recoverable.id, NOW)
    recover_claim = repository.claim("worker-recover", NOW, NOW + timedelta(seconds=1))
    assert recover_claim is not None and recover_claim.id == recoverable.id
    recovery_time = NOW + timedelta(seconds=2)
    assert (
        repository.recover_expired_leases(
            recovery_time, RETENTION_END, NOW - timedelta(days=1)
        )
        == 1
    )
    recovered = repository.get(recoverable.id)
    assert recovered is not None and recovered.state is JobState.QUEUED
    assert recovered.attempt == 1

    fenced_claim = repository.claim(
        "worker-recover", recovery_time, recovery_time + timedelta(seconds=30)
    )
    assert fenced_claim is not None and fenced_claim.id == recoverable.id
    assert recover_claim.lease_token is not None
    assert fenced_claim.lease_token is not None
    assert fenced_claim.lease_token != recover_claim.lease_token
    assert not repository.heartbeat(
        LeaseHeartbeat(
            recover_claim.id,
            "worker-recover",
            recover_claim.lease_token,
            recovery_time,
            recovery_time + timedelta(seconds=30),
            JobStep.DOCX,
            50,
        )
    )
    with pytest.raises(JobLeaseLostError):
        repository.succeed(
            recover_claim.id,
            "worker-recover",
            recover_claim.lease_token,
            uuid4(),
            recovery_time,
            RETENTION_END,
        )
    repository.fail(
        JobFailure(
            fenced_claim.id,
            "worker-recover",
            fenced_claim.lease_token,
            "safe_failure",
            "Conversion failed safely.",
            recovery_time,
            RETENTION_END,
        )
    )

    cancel_race, _ = repository.create(
        submission(owner_id, created_at=NOW + timedelta(seconds=5))
    )
    repository.activate_source(cancel_race.id, NOW)
    cancel_claim = repository.claim("cancel-race", NOW, LEASE_END)
    assert cancel_claim is not None and cancel_claim.lease_token is not None
    repository.request_cancel(cancel_claim.id, owner_id, NOW, RETENTION_END)
    cancelled_winner = repository.succeed(
        cancel_claim.id,
        "cancel-race",
        cancel_claim.lease_token,
        uuid4(),
        NOW,
        RETENTION_END,
    )
    assert cancelled_winner.state is JobState.CANCELLED

    abandoned, _ = repository.create(
        submission(owner_id, created_at=NOW - timedelta(days=1))
    )
    assert (
        repository.recover_expired_leases(NOW, RETENTION_END, NOW - timedelta(hours=1))
        == 1
    )
    abandoned = repository.get(abandoned.id)
    assert abandoned is not None and abandoned.state is JobState.FAILED
    assert abandoned.error_code == "source_upload_incomplete"

    expired_objects = repository.expire_terminal(
        "cleanup-a",
        RETENTION_END + timedelta(seconds=1),
        RETENTION_END + timedelta(seconds=31),
        2,
    )
    assert len(expired_objects) == 2
    for expired in expired_objects:
        assert repository.complete_cleanup(expired.job_id, expired.cleanup_token)
        assert not repository.complete_cleanup(expired.job_id, expired.cleanup_token)
    assert repository.expire_terminal(
        "cleanup-a",
        RETENTION_END + timedelta(seconds=1),
        RETENTION_END + timedelta(seconds=31),
        20,
    )
