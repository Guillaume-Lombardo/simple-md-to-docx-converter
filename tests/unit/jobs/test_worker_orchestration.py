"""Focused tests for independently composed worker orchestration services."""

from contextlib import nullcontext
from datetime import UTC, datetime
from threading import Event

import pytest
from pytest_mock import MockerFixture

from markweave.jobs.errors import JobLeaseLostError
from markweave.jobs.models import JobProcessResult
from markweave.jobs.ports import JobProcessor, JobRepository
from markweave.jobs.worker_execution import (
    ClaimedJob,
    JobClaimService,
    JobExecutionService,
    JobFailureService,
    JobHeartbeatService,
    ProcessingOutcome,
)
from markweave.jobs.worker_publication import JobPublicationService
from markweave.storage import ObjectScope, ObjectStore
from tests.unit.jobs.test_job_worker import running_job

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 30, tzinfo=UTC)


def test_claim_service_establishes_fencing_and_monotonic_budget(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    claimed_job = running_job()
    repository.claim.return_value = claimed_job
    service = JobClaimService(
        repository,
        "worker-1",
        mocker.Mock(return_value=NOW),
        mocker.Mock(return_value=7.0),
        10,
        3,
    )

    claimed = service.claim()

    assert claimed is not None
    assert claimed.lease_token == claimed_job.lease_token
    assert claimed.budget is not None
    assert claimed.budget.started_monotonic == 7.0
    assert claimed.budget.deadline_monotonic == 10.0


def test_execution_service_preserves_unexpected_processor_failure(
    mocker: MockerFixture,
) -> None:
    processor = mocker.Mock(spec=JobProcessor)
    failure = RuntimeError("processor failed")
    processor.process.side_effect = failure

    outcome = JobExecutionService(processor).execute(
        running_job(),
        cancelled=mocker.Mock(return_value=False),
        progress=mocker.Mock(),
    )

    assert outcome == ProcessingOutcome(None, failure)


def test_heartbeat_service_propagates_keepalive_lease_loss(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    heartbeat_attempted = Event()
    repository.heartbeat.side_effect = lambda _heartbeat: (
        heartbeat_attempted.set() or False
    )
    claimed_job = running_job()
    assert claimed_job.lease_token is not None
    claimed = ClaimedJob(claimed_job, claimed_job.lease_token, None, 0.0)
    heartbeat = JobHeartbeatService(
        claimed=claimed,
        repository=repository,
        worker_id="worker-1",
        clock=mocker.Mock(return_value=NOW),
        monotonic_clock=mocker.Mock(return_value=0.0),
        lease_seconds=1,
        heartbeat_seconds=0.001,
        metrics=None,
        shutdown_requested=lambda: False,
    )

    heartbeat.start()
    try:
        assert heartbeat_attempted.wait(1)
        with (
            pytest.raises(JobLeaseLostError, match="lease was lost"),
            heartbeat.guarded(),
        ):
            pass
        assert heartbeat.cancellation()
    finally:
        heartbeat.stop()


def test_failure_service_applies_durable_cancellation_before_publication(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    claimed_job = running_job()
    assert claimed_job.lease_token is not None
    claimed = ClaimedJob(claimed_job, claimed_job.lease_token, None, 0.0)
    heartbeat = mocker.Mock(spec=JobHeartbeatService)
    heartbeat.guarded.return_value = nullcontext()
    heartbeat.durable_cancellation_requested.return_value = True
    service = JobFailureService(repository, "worker-1", lambda: NOW, 60, None)

    result = service.resolve(
        claimed,
        ProcessingOutcome(JobProcessResult(b"unpublished"), None),
        heartbeat,
    )

    assert result is None
    repository.finish_cancelled.assert_called_once()
    repository.fail.assert_not_called()
    heartbeat.request_stop.assert_called_once_with()


def test_publication_service_compensates_lost_fenced_transition(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.succeed.side_effect = JobLeaseLostError("lease lost")
    objects = mocker.Mock(spec=ObjectStore)
    heartbeat = mocker.Mock(spec=JobHeartbeatService)
    heartbeat.guarded.return_value = nullcontext()
    claimed_job = running_job()
    assert claimed_job.lease_token is not None
    service = JobPublicationService(
        repository,
        objects,
        "worker-1",
        lambda: NOW,
        60,
    )

    with pytest.raises(JobLeaseLostError, match="lease lost"):
        service.publish(
            claimed_job,
            claimed_job.lease_token,
            JobProcessResult(b"result"),
            heartbeat,
        )

    published_key = objects.put.call_args.args[0]
    assert published_key.scope is ObjectScope.RESULT
    objects.delete.assert_called_once_with(published_key)
    heartbeat.request_stop.assert_not_called()
