"""Production component assembly for embedded and external workers."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest
from pytest_mock import MockerFixture

from md_converter.app import AppComponents
from md_converter.auth.ports import ReadinessProbe
from md_converter.auth.service import AuthenticationService
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.jobs.runner import (
    EmbeddedWorker,
    ExternalWorkerRuntime,
    WorkerSchedule,
)
from md_converter.jobs.runtime import JobPolicies
from md_converter.jobs.service import JobService
from md_converter.jobs.worker import WorkerPolicy
from md_converter.observability import QueueObserver
from md_converter.retention import RetentionService
from md_converter.storage import ObjectStore
from md_converter.templates.processor import TemplateAwareProcessor
from md_converter.templates.service import TemplateService

pytestmark = pytest.mark.unit


class _StopAfterWait:
    def __init__(self) -> None:
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def wait(self, timeout: float) -> bool:
        del timeout
        self.stopped = True
        return True


class _RecordingRetention:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def cleanup(self, *, limit: int) -> int:
        self.limits.append(limit)
        return 2


def _components(
    mocker: MockerFixture,
) -> tuple[AppComponents, _RecordingRetention]:
    policies = mocker.Mock(spec=JobPolicies)
    policies.worker = WorkerPolicy(10, 1, 100, 30)
    policies.schedule = WorkerSchedule(0.1, 1, 7, 0.2)
    repository = mocker.Mock(spec=JobRepository)
    repository.claim.return_value = None
    repository.recover_expired_leases.return_value = 0
    repository.expire_terminal.return_value = ()
    retention = _RecordingRetention()
    return AppComponents(
        authentication=mocker.Mock(spec=AuthenticationService),
        readiness=mocker.Mock(spec=ReadinessProbe),
        object_store=mocker.Mock(spec=ObjectStore),
        jobs=mocker.Mock(spec=JobService),
        templates=mocker.Mock(spec=TemplateService),
        job_policies=policies,
        retention=cast(RetentionService, retention),
        job_repository=repository,
        queue_observer=mocker.Mock(spec=QueueObserver),
        worker_metrics_port=0,
    ), retention


def test_external_worker_assembly_runs_component_retention_on_schedule(
    mocker: MockerFixture,
) -> None:
    components, retention = _components(mocker)
    monotonic_clock = mocker.Mock(side_effect=(0.0, 2.0))
    loop = components.build_external_worker_loop(
        worker_id="external-1",
        processor=mocker.Mock(spec=TemplateAwareProcessor),
        clock=lambda: datetime(2026, 8, 24, tzinfo=UTC),
        monotonic_clock=monotonic_clock,
    )

    loop.run(_StopAfterWait())

    assert retention.limits == [7]


def test_embedded_worker_uses_the_same_complete_production_assembly(
    mocker: MockerFixture,
) -> None:
    components, _retention = _components(mocker)
    embedded = components.build_embedded_worker(
        worker_id="embedded-1",
        processor=mocker.Mock(spec=TemplateAwareProcessor),
        thread_name="embedded-production",
    )
    assert isinstance(embedded, EmbeddedWorker)

    incomplete = AppComponents(
        authentication=mocker.Mock(spec=AuthenticationService),
        readiness=mocker.Mock(spec=ReadinessProbe),
        object_store=mocker.Mock(spec=ObjectStore),
        jobs=mocker.Mock(spec=JobService),
    )
    with pytest.raises(RuntimeError, match="policies"):
        incomplete.build_external_worker_loop(
            worker_id="external-incomplete",
            processor=mocker.Mock(spec=JobProcessor),
        )
    with pytest.raises(RuntimeError, match="components"):
        replace(components, retention=None).build_conversion_worker(
            worker_id="worker-incomplete",
            processor=mocker.Mock(spec=TemplateAwareProcessor),
        )

    runtime = components.build_external_worker_runtime(
        worker_id="external-metrics",
        processor=mocker.Mock(spec=TemplateAwareProcessor),
    )
    assert isinstance(runtime, ExternalWorkerRuntime)
    with pytest.raises(RuntimeError, match="queue observation"):
        replace(components, queue_observer=None).build_external_worker_runtime(
            worker_id="external-metrics",
            processor=mocker.Mock(spec=TemplateAwareProcessor),
        )
