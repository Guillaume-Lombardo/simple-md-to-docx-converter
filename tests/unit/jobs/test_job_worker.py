"""Unit coverage for lease ownership and atomic worker publication."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.jobs.errors import JobLeaseLostError, JobProcessingCancelled
from markweave.jobs.models import (
    ConversionJob,
    ExpiredJobObjects,
    JobOutput,
    JobProcessResult,
    JobState,
    JobStep,
    result_manifest_object_id,
    result_object_id,
)
from markweave.jobs.ports import CancellationProbe, JobProcessor, JobRepository
from markweave.jobs.worker import (
    ConversionWorker,
    MaintenanceCleaner,
    WorkerPolicy,
    WorkerRuntime,
)
from markweave.jobs.worker_publication import _is_sha256
from markweave.observability import (
    OperationalMetrics,
    QueueSnapshot,
    current_correlation_id,
)
from markweave.storage import ObjectScope, ObjectStore, ObjectStoreError
from tests.unit.jobs.test_job_models import job

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 24, tzinfo=UTC)
TRACEABILITY_TEMPLATE_ID = UUID("00000000-0000-4000-8000-000000000029")
TRACEABILITY_TEMPLATE_VERSION = "3"
TRACEABILITY_TEMPLATE_SHA256 = "2" * 64


def versioned_result(content: bytes, manifest: bytes | None = None) -> JobProcessResult:
    return JobProcessResult(
        content,
        manifest,
        template_version=TRACEABILITY_TEMPLATE_VERSION,
        template_sha256=TRACEABILITY_TEMPLATE_SHA256,
    )


def test_publication_digest_validation_requires_lowercase_sha256() -> None:
    assert _is_sha256("a" * 64)
    assert not _is_sha256("A" * 64)
    assert not _is_sha256("a" * 63)
    assert not _is_sha256(None)


def worker(mocker: MockerFixture) -> tuple[ConversionWorker, Any, Any, Any]:
    repository = mocker.Mock(spec=JobRepository)
    repository.cancellation_requested.return_value = False
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


def test_periodic_cleanup_runs_additional_bounded_retention(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.expire_terminal.return_value = ()
    maintenance = mocker.Mock(spec=MaintenanceCleaner)
    maintenance.cleanup.return_value = 2
    instance = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            mocker.Mock(spec=ObjectStore),
            mocker.Mock(spec=JobProcessor),
            mocker.Mock(return_value=NOW),
            maintenance=maintenance,
        ),
        policy=WorkerPolicy(10, 1, 100, 30),
    )
    assert instance.cleanup(limit=7) == 2
    maintenance.cleanup.assert_called_once_with(limit=7)


def test_worker_records_recovery_and_expiration_counts(mocker: MockerFixture) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.recover_expired_leases.return_value = 3
    repository.expire_terminal.return_value = (
        ExpiredJobObjects(uuid4(), uuid4(), uuid4(), uuid4(), (uuid4(),)),
    )
    metrics = OperationalMetrics()
    instance = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            mocker.Mock(spec=ObjectStore),
            mocker.Mock(spec=JobProcessor),
            mocker.Mock(return_value=NOW),
            metrics=metrics,
        ),
        policy=WorkerPolicy(10, 1, 100, 30),
    )

    assert instance.recover() == 3
    assert instance.cleanup(limit=2) == 1
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert "md_converter_job_recoveries_total 3" in rendered
    assert "md_converter_job_expirations_total 1" in rendered


def running_job() -> ConversionJob:
    return job(
        template_id=TRACEABILITY_TEMPLATE_ID,
        output=JobOutput.DOCX,
        state=JobState.RUNNING,
        step=JobStep.VALIDATING,
        attempt=1,
        lease_owner="worker-1",
        lease_token=uuid4(),
        lease_expires_at=NOW + timedelta(seconds=10),
        heartbeat_at=NOW,
    )


def traceability_manifest() -> bytes:
    return json.dumps(
        {
            "application_version": "0.1.0",
            "chromium_version": "151.0.7922.173",
            "conversion_contract_version": "1",
            "export_filter": "pdf:writer_pdf_Export",
            "font_manifest_sha256": "5" * 64,
            "libreoffice_version": "26.2.5.2",
            "mermaid_version": "11.16.0",
            "output_format": "pdf",
            "output_pdf_bytes": 3,
            "output_pdf_sha256": "4" * 64,
            "pages": [{"height_points": 792, "width_points": 612}],
            "pandoc_reader": "commonmark_x",
            "pandoc_version": "3.10.2",
            "schema_version": 1,
            "source_docx_sha256": "3" * 64,
            "template_id": str(TRACEABILITY_TEMPLATE_ID),
            "template_sha256": TRACEABILITY_TEMPLATE_SHA256,
            "template_version": TRACEABILITY_TEMPLATE_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def invalid_traceability_manifest(**changes: object) -> bytes:
    decoded = json.loads(traceability_manifest())
    decoded.update(changes)
    return json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()


def traceability_manifest_v2(template_mode: str) -> bytes:
    decoded = json.loads(traceability_manifest())
    decoded["schema_version"] = 2
    decoded["template_mode"] = template_mode
    if template_mode == "pandoc-default":
        decoded["template_id"] = None
        decoded["template_version"] = None
        decoded["template_sha256"] = None
    return json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()


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


def test_worker_atomically_publishes_and_compensates_traceability_sidecar(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    claimed = replace(running_job(), output=JobOutput.PDF)
    repository.claim.return_value = claimed
    repository.heartbeat.return_value = True
    processor.process.return_value = versioned_result(b"pdf", traceability_manifest())

    assert instance.run_once()

    result_key, manifest_key = [call.args[0] for call in objects.put.call_args_list]
    assert result_key.scope is ObjectScope.RESULT
    assert manifest_key.scope is ObjectScope.RESULT_MANIFEST
    assert manifest_key.object_id == result_manifest_object_id(claimed.id, 1)
    assert (
        repository.succeed.call_args.kwargs["result_manifest_object_id"]
        == manifest_key.object_id
    )

    objects.reset_mock()
    repository.succeed.reset_mock()
    objects.put.side_effect = (None, ObjectStoreError())
    with pytest.raises(ObjectStoreError):
        instance.run_once()
    assert [call.args[0] for call in objects.delete.call_args_list] == [
        result_key,
        manifest_key,
    ]
    repository.succeed.assert_not_called()


@pytest.mark.parametrize(
    "manifest_kind",
    (
        "v1-versioned",
        "v2-versioned",
        "v2-pandoc-default",
    ),
)
def test_worker_accepts_traceability_manifest_v1_and_v2(
    mocker: MockerFixture, manifest_kind: str
) -> None:
    instance, repository, _objects, processor = worker(mocker)
    claimed = replace(running_job(), output=JobOutput.PDF)
    if manifest_kind == "v1-versioned":
        manifest = traceability_manifest()
    elif manifest_kind == "v2-versioned":
        manifest = traceability_manifest_v2("versioned")
    else:
        claimed = replace(claimed, template_id=None, template_version_id=None)
        manifest = traceability_manifest_v2("pandoc-default")
    repository.claim.return_value = claimed
    repository.heartbeat.return_value = True
    processor.process.return_value = (
        JobProcessResult(b"pdf", manifest)
        if manifest_kind == "v2-pandoc-default"
        else versioned_result(b"pdf", manifest)
    )

    assert instance.run_once()
    repository.succeed.assert_called_once()


@pytest.mark.parametrize(
    "manifest",
    (traceability_manifest(), traceability_manifest_v2("versioned")),
)
def test_worker_rejects_versioned_manifest_for_pandoc_default_job(
    mocker: MockerFixture, manifest: bytes
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = replace(
        running_job(),
        output=JobOutput.PDF,
        template_id=None,
        template_version_id=None,
    )
    repository.heartbeat.return_value = True
    processor.process.return_value = versioned_result(b"pdf", manifest)

    with pytest.raises(RuntimeError, match="canonical traceability"):
        instance.run_once()

    objects.put.assert_not_called()
    repository.succeed.assert_not_called()


@pytest.mark.parametrize(
    ("output", "manifest"),
    (
        (JobOutput.PDF, None),
        (JobOutput.BOTH, None),
        (JobOutput.PDF, b'{"not":"canonical"}'),
        (JobOutput.PDF, b"[]"),
        (JobOutput.PDF, invalid_traceability_manifest(schema_version=True)),
        (JobOutput.PDF, invalid_traceability_manifest(template_id="")),
        (JobOutput.PDF, invalid_traceability_manifest(template_version="")),
        (JobOutput.PDF, invalid_traceability_manifest(template_id=str(uuid4()))),
        (JobOutput.PDF, invalid_traceability_manifest(schema_version=2)),
        (
            JobOutput.PDF,
            invalid_traceability_manifest(
                schema_version=2,
                template_mode="pandoc-default",
            ),
        ),
        (
            JobOutput.PDF,
            traceability_manifest_v2("unsupported"),
        ),
        (JobOutput.PDF, invalid_traceability_manifest(output_format="docx")),
        (JobOutput.PDF, invalid_traceability_manifest(output_pdf_bytes=True)),
        (JobOutput.PDF, invalid_traceability_manifest(output_pdf_bytes=0)),
        (JobOutput.PDF, invalid_traceability_manifest(template_sha256="invalid")),
        (JobOutput.PDF, invalid_traceability_manifest(template_sha256="9" * 64)),
        (JobOutput.PDF, invalid_traceability_manifest(template_version="4")),
        (JobOutput.PDF, invalid_traceability_manifest(pages=None)),
        (JobOutput.PDF, invalid_traceability_manifest(pages=[])),
        (JobOutput.PDF, invalid_traceability_manifest(pages=["invalid"])),
        (
            JobOutput.PDF,
            invalid_traceability_manifest(pages=[{"width_points": 612}]),
        ),
        (
            JobOutput.PDF,
            invalid_traceability_manifest(
                pages=[{"height_points": 792, "width_points": "612"}]
            ),
        ),
        (
            JobOutput.PDF,
            invalid_traceability_manifest(
                pages=[{"height_points": 0, "width_points": 612}]
            ),
        ),
        (
            JobOutput.PDF,
            json.dumps(json.loads(traceability_manifest())).encode(),
        ),
        (
            JobOutput.PDF,
            invalid_traceability_manifest(output_pdf_bytes=float("nan")),
        ),
        (JobOutput.DOCX, traceability_manifest()),
    ),
)
def test_worker_rejects_result_manifest_cardinality_before_publication(
    mocker: MockerFixture,
    output: JobOutput,
    manifest: bytes | None,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = replace(running_job(), output=output)
    repository.heartbeat.return_value = True
    processor.process.return_value = versioned_result(b"result", manifest)

    with pytest.raises(RuntimeError, match="traceability manifest"):
        instance.run_once()

    objects.put.assert_not_called()
    repository.succeed.assert_not_called()
    repository.fail.assert_not_called()
    repository.finish_cancelled.assert_not_called()


def test_shutdown_interrupts_active_processing_without_terminal_transition(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = running_job()
    repository.heartbeat.return_value = True
    shutdown = mocker.Mock(return_value=False)

    def process(
        _job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        del progress
        shutdown.return_value = True
        assert cancelled()
        raise JobProcessingCancelled

    processor.process.side_effect = process

    assert instance.run_once(shutdown_requested=shutdown)
    objects.put.assert_not_called()
    repository.succeed.assert_not_called()
    repository.fail.assert_not_called()
    repository.finish_cancelled.assert_not_called()


def test_source_integrity_failure_is_durable_and_worker_accepts_next_job(
    mocker: MockerFixture,
) -> None:
    instance, repository, _objects, processor = worker(mocker)
    claimed = running_job()
    repository.claim.return_value = claimed
    repository.heartbeat.return_value = True
    processor.process.side_effect = (
        ConversionError(
            ConversionErrorCode.SOURCE_INTEGRITY,
            "Frozen source content could not be verified.",
        ),
        JobProcessResult(b"next-result"),
    )

    assert instance.run_once()
    assert repository.fail.call_args.args[0].code == "source_integrity"
    assert instance.run_once()
    repository.succeed.assert_called_once()


def test_worker_records_correlated_step_durations(mocker: MockerFixture) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.cancellation_requested.return_value = False
    claimed = running_job()
    repository.claim.return_value = claimed
    repository.heartbeat.return_value = True
    objects = mocker.Mock(spec=ObjectStore)
    processor = mocker.Mock(spec=JobProcessor)
    processor.process.side_effect = lambda _job, *, cancelled, progress: (
        progress(JobStep.DOCX, 70) or JobProcessResult(b"result")
    )
    clock_values = iter((0.0, 1.0, 2.0))
    metrics = OperationalMetrics()
    observed_correlations: list[str | None] = []
    emitted = mocker.patch("markweave.jobs.worker.log_event")
    emitted.side_effect = lambda *_args, **_kwargs: observed_correlations.append(
        current_correlation_id()
    )
    instance = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            objects,
            processor,
            mocker.Mock(return_value=NOW),
            monotonic_clock=lambda: next(clock_values),
            metrics=metrics,
        ),
        policy=WorkerPolicy(10, 1, 100, 30),
    )

    assert instance.run_once()
    assert observed_correlations == [claimed.correlation_id, claimed.correlation_id]
    assert current_correlation_id() is None
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert 'md_converter_job_step_duration_seconds_sum{step="validating"} 1' in rendered
    assert 'md_converter_job_step_duration_seconds_sum{step="docx"} 1' in rendered


def test_worker_records_safe_failure_metric(mocker: MockerFixture) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = False
    processor = mocker.Mock(spec=JobProcessor)
    processor.process.side_effect = ConversionError(
        ConversionErrorCode.INVALID_PDF, "Conversion output is invalid."
    )
    metrics = OperationalMetrics()
    clock_values = iter((0.0, 1.0))
    mocker.patch("markweave.jobs.worker.log_event")
    instance = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            mocker.Mock(spec=ObjectStore),
            processor,
            mocker.Mock(return_value=NOW),
            monotonic_clock=lambda: next(clock_values),
            metrics=metrics,
        ),
        policy=WorkerPolicy(10, 1, 100, 30),
    )

    assert instance.run_once()
    assert 'md_converter_job_failures_total{code="invalid_pdf"} 1' in metrics.render(
        QueueSnapshot(0, 0, 0)
    )


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


@pytest.mark.parametrize(
    ("output", "manifest"),
    (
        (JobOutput.DOCX, None),
        (JobOutput.PDF, traceability_manifest()),
    ),
)
def test_worker_removes_published_objects_when_atomic_cancellation_wins(
    mocker: MockerFixture, output: JobOutput, manifest: bytes | None
) -> None:
    instance, repository, objects, processor = worker(mocker)
    claimed = replace(running_job(), output=output)
    repository.claim.return_value = claimed
    repository.cancellation_requested.return_value = False
    processor.process.return_value = (
        JobProcessResult(b"result")
        if output is JobOutput.DOCX
        else versioned_result(b"result", manifest)
    )
    repository.succeed.return_value = job(
        id=claimed.id,
        owner_id=claimed.owner_id,
        state=JobState.CANCELLED,
        expires_at=NOW + timedelta(seconds=100),
    )
    assert instance.run_once()
    assert [call.args[0] for call in objects.delete.call_args_list] == [
        call.args[0] for call in objects.put.call_args_list
    ]


def test_worker_rejects_a_processor_that_returns_no_result(
    mocker: MockerFixture,
) -> None:
    instance, repository, objects, processor = worker(mocker)
    repository.claim.return_value = running_job()
    processor.process.return_value = None

    with pytest.raises(RuntimeError, match="returned no result"):
        instance.run_once()

    objects.put.assert_not_called()
    repository.succeed.assert_not_called()


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
    expired = ExpiredJobObjects(
        uuid4(), uuid4(), uuid4(), uuid4(), (uuid4(),), (uuid4(),)
    )
    repository.expire_terminal.return_value = (expired,)
    objects.delete.side_effect = ObjectStoreError
    with pytest.raises(ObjectStoreError):
        instance.cleanup(limit=1)
    repository.complete_cleanup.assert_not_called()

    objects.delete.side_effect = None
    repository.complete_cleanup.return_value = True
    assert instance.cleanup(limit=1) == 1
    assert any(
        call.args[0].scope is ObjectScope.RESULT_MANIFEST
        for call in objects.delete.call_args_list
    )
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


def test_monotonic_budget_is_visible_and_ignores_wall_clock_jumps(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = False
    objects = mocker.Mock(spec=ObjectStore)
    processor = mocker.Mock(spec=JobProcessor)
    wall_clock = mocker.Mock(
        side_effect=(NOW, NOW + timedelta(days=30), NOW + timedelta(days=30))
    )
    monotonic_clock = mocker.Mock(side_effect=(10.0, 10.5, 10.5, 10.5))

    def process(
        _job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        del progress
        assert cancelled.budget is not None
        assert cancelled.budget.started_monotonic == 10.0
        assert cancelled.budget.deadline_monotonic == 11.0
        assert cancelled.budget.remaining_seconds(10.25) == 0.75
        assert not cancelled()
        return JobProcessResult(b"result")

    processor.process.side_effect = process
    worker = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            objects,
            processor,
            wall_clock,
            monotonic_clock,
        ),
        policy=WorkerPolicy(10, 1, 100, 30, max_job_duration_seconds=1),
    )
    assert worker.run_once()
    repository.succeed.assert_called_once()
    repository.fail.assert_not_called()
    repository.finish_cancelled.assert_not_called()


@pytest.mark.parametrize(
    ("durable_cancelled", "expected_transition"),
    ((False, "failed"), (True, "cancelled")),
)
def test_durable_cancellation_precedes_duration_and_duration_precedes_error(
    mocker: MockerFixture,
    durable_cancelled: bool,
    expected_transition: str,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = durable_cancelled
    processor = mocker.Mock(spec=JobProcessor)
    processor.process.side_effect = ConversionError(
        ConversionErrorCode.INVALID_DOCX, "Conversion output is invalid."
    )
    worker = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            mocker.Mock(spec=ObjectStore),
            processor,
            mocker.Mock(return_value=NOW),
            mocker.Mock(side_effect=(0.0, 2.0)),
        ),
        policy=WorkerPolicy(10, 1, 100, 30, max_job_duration_seconds=1),
    )
    assert worker.run_once()
    if expected_transition == "cancelled":
        repository.finish_cancelled.assert_called_once()
        repository.fail.assert_not_called()
    else:
        failure = repository.fail.call_args.args[0]
        assert failure.code == "resource_budget_exceeded"
        repository.finish_cancelled.assert_not_called()


def test_duration_precedes_unexpected_error_but_not_lease_loss(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = False
    processor = mocker.Mock(spec=JobProcessor)
    processor.process.side_effect = RuntimeError("processor defect")
    monotonic_clock = mocker.Mock(side_effect=(0.0, 2.0))
    runtime = WorkerRuntime(
        repository,
        mocker.Mock(spec=ObjectStore),
        processor,
        mocker.Mock(return_value=NOW),
        monotonic_clock,
    )
    worker = ConversionWorker(
        worker_id="worker-1",
        runtime=runtime,
        policy=WorkerPolicy(10, 1, 100, 30, max_job_duration_seconds=1),
    )
    assert worker.run_once()
    assert repository.fail.call_args.args[0].code == "resource_budget_exceeded"

    repository.reset_mock()
    repository.claim.return_value = running_job()
    repository.heartbeat.return_value = False

    def lose_lease(
        _job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        del cancelled
        progress(JobStep.DOCX, 50)
        return JobProcessResult(b"result")

    processor.process.side_effect = lose_lease
    monotonic_clock.side_effect = (0.0, 2.0)
    with pytest.raises(JobLeaseLostError):
        worker.run_once()
    repository.fail.assert_not_called()
    repository.finish_cancelled.assert_not_called()


def test_unexpected_error_before_deadline_remains_visible(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = running_job()
    repository.cancellation_requested.return_value = False
    processor = mocker.Mock(spec=JobProcessor)
    processor.process.side_effect = RuntimeError("processor defect")
    worker = ConversionWorker(
        worker_id="worker-1",
        runtime=WorkerRuntime(
            repository,
            mocker.Mock(spec=ObjectStore),
            processor,
            mocker.Mock(return_value=NOW),
            mocker.Mock(side_effect=(0.0, 0.5)),
        ),
        policy=WorkerPolicy(10, 1, 100, 30, max_job_duration_seconds=1),
    )
    with pytest.raises(RuntimeError, match="processor defect"):
        worker.run_once()
    repository.fail.assert_not_called()
    repository.finish_cancelled.assert_not_called()
