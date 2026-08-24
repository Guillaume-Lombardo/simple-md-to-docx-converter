"""Unit coverage for lease ownership and atomic worker publication."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.jobs.errors import JobLeaseLostError, JobProcessingCancelled
from md_converter.jobs.models import (
    ConversionJob,
    ExpiredJobObjects,
    JobProcessResult,
    JobState,
    JobStep,
    result_object_id,
)
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.storage import ObjectScope, ObjectStore, ObjectStoreError
from tests.unit.jobs.test_job_models import job

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 24, tzinfo=UTC)


def worker(mocker: MockerFixture) -> tuple[ConversionWorker, Any, Any, Any]:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    processor = mocker.Mock(spec=JobProcessor)
    clock = mocker.Mock(return_value=NOW)
    instance = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(repository, objects, processor, clock),
        policy=WorkerPolicy(
            lease_seconds=10,
            heartbeat_seconds=1,
            result_retention_seconds=100,
            incomplete_submission_seconds=30,
        ),
    )
    return instance, repository, objects, processor


def running_job() -> ConversionJob:
    return job(
        state=JobState.RUNNING,
        step=JobStep.VALIDATING,
        attempt=1,
        lease_owner="worker-1",
        lease_token=uuid4(),
        lease_expires_at=NOW + timedelta(seconds=10),
        heartbeat_at=NOW,
    )


def test_worker_claims_heartbeats_and_publishes_only_after_processing(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    claimed = running_job()
    repository.claim.return_value = claimed
    repository.heartbeat.return_value = True
    repository.cancellation_requested.return_value = False
    processor.process.return_value = JobProcessResult(b"result")

    def process(
        _job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert not cancelled()
        progress(JobStep.PDF, 80)
        return JobProcessResult(b"result")

    processor.process.side_effect = process
    assert instance.run_once()
    heartbeat = repository.heartbeat.call_args.args[0]
    assert heartbeat.step is JobStep.PDF
    assert heartbeat.progress == 80
    assert objects.put.call_args.args[0].scope is ObjectScope.RESULT
    assert objects.put.call_args.args[1] == b"result"
    repository.succeed.assert_called_once()
    assert objects.put.call_args.args[0].object_id == result_object_id(claimed.id, 1)
    objects.delete.assert_not_called()


def test_worker_handles_empty_queue_cancellation_and_safe_conversion_failure(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = None
    assert not instance.run_once()

    claimed = running_job()
    repository.claim.return_value = claimed
    processor.process.side_effect = JobProcessingCancelled
    assert instance.run_once()
    repository.finish_cancelled.assert_called_once()
    objects.put.assert_not_called()

    processor.process.side_effect = ConversionError(
        ConversionErrorCode.INVALID_PDF, "Conversion output is invalid."
    )
    assert instance.run_once()
    failure = repository.fail.call_args.args[0]
    assert failure.code == "invalid_pdf"
    assert failure.message == "Conversion output is invalid."


def test_worker_does_not_publish_after_late_cancel_or_lost_lease(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = running_job()
    processor.process.return_value = JobProcessResult(b"result")
    repository.cancellation_requested.return_value = True
    assert instance.run_once()
    repository.finish_cancelled.assert_called_once()
    objects.put.assert_not_called()

    repository.cancellation_requested.return_value = False
    repository.heartbeat.return_value = False

    def lose_lease(
        _job: ConversionJob,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        progress(JobStep.DOCX, 50)
        return JobProcessResult(b"never")

    processor.process.side_effect = lose_lease
    with pytest.raises(JobLeaseLostError):
        instance.run_once()
    objects.put.assert_not_called()


def test_worker_removes_unpublished_result_when_terminal_transition_fails(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = False
    processor.process.return_value = JobProcessResult(b"result")
    repository.succeed.side_effect = JobLeaseLostError("lost")
    with pytest.raises(JobLeaseLostError):
        instance.run_once()
    assert objects.delete.call_args.args[0] == objects.put.call_args.args[0]


def test_worker_removes_result_when_atomic_cancellation_wins(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    claimed = running_job()
    repository.claim.return_value = claimed
    repository.cancellation_requested.return_value = False
    processor.process.return_value = JobProcessResult(b"result")
    repository.succeed.return_value = job(
        id=claimed.id,
        owner_id=claimed.owner_id,
        state=JobState.CANCELLED,
        expires_at=NOW + timedelta(seconds=100),
    )
    assert instance.run_once()
    assert objects.delete.call_args.args[0] == objects.put.call_args.args[0]


def test_worker_recovery_and_bounded_cleanup_delegate_explicit_values(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, _processor = worker(mocker)
    repository.recover_expired_leases.return_value = 2
    assert instance.recover() == 2
    repository.recover_expired_leases.assert_called_once_with(
        NOW, NOW + timedelta(seconds=100), NOW - timedelta(seconds=30)
    )

    expired = ExpiredJobObjects(uuid4(), uuid4(), uuid4(), uuid4(), (uuid4(),))
    source_only = ExpiredJobObjects(uuid4(), uuid4(), uuid4(), uuid4(), ())
    repository.expire_terminal.return_value = (expired, source_only)
    repository.complete_cleanup.return_value = True
    assert instance.cleanup(limit=5) == 2
    repository.expire_terminal.assert_called_once_with(
        "worker-1", NOW, NOW + timedelta(seconds=10), 5
    )
    assert [call.args[0].scope for call in objects.delete.call_args_list] == [
        ObjectScope.UPLOAD,
        ObjectScope.RESULT,
        ObjectScope.UPLOAD,
    ]
    assert [call.args for call in repository.complete_cleanup.call_args_list] == [
        (expired.job_id, expired.cleanup_token),
        (source_only.job_id, source_only.cleanup_token),
    ]
    with pytest.raises(ValueError):
        instance.cleanup(limit=0)


def test_cleanup_remains_retryable_until_object_deletion_is_acknowledged(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, _processor = worker(mocker)
    expired = ExpiredJobObjects(uuid4(), uuid4(), uuid4(), uuid4(), (uuid4(),))
    repository.expire_terminal.return_value = (expired,)
    objects.delete.side_effect = ObjectStoreError
    with pytest.raises(ObjectStoreError):
        instance.cleanup(limit=1)
    repository.complete_cleanup.assert_not_called()

    objects.delete.side_effect = None
    repository.complete_cleanup.return_value = True
    assert instance.cleanup(limit=1) == 1
    repository.complete_cleanup.assert_called_once_with(
        expired.job_id, expired.cleanup_token
    )

    repository.complete_cleanup.reset_mock()
    repository.complete_cleanup.return_value = False
    assert instance.cleanup(limit=1) == 1


@pytest.mark.parametrize(
    "arguments",
    (
        ("", 10, 1, 100, 30),
        ("x" * 256, 10, 1, 100, 30),
        ("worker", 0, 1, 100, 30),
        ("worker", 10, 10, 100, 30),
        ("worker", 10, 1, True, 30),
    ),
)
def test_worker_configuration_is_explicit_and_bounded(
    mocker: MockerFixture, arguments: tuple[str, float, float, float, float]
) -> None:
    worker_id, lease, heartbeat, retention, incomplete = arguments
    runtime = WorkerRuntime(
        mocker.Mock(spec=JobRepository),
        mocker.Mock(spec=ObjectStore),
        mocker.Mock(spec=JobProcessor),
        mocker.Mock(return_value=NOW),
    )
    with pytest.raises(ValueError):
        ConversionWorker(
            worker_id=worker_id,
            runtime=runtime,
            policy=WorkerPolicy(lease, heartbeat, retention, incomplete),
        )
