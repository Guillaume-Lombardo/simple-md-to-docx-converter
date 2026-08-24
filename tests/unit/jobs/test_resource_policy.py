"""T18 policy validation, assembly, and duration-budget behavior."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from md_converter.config import Settings
from md_converter.conversion.archive import ArchiveLimits
from md_converter.conversion.mermaid import MermaidLimits
from md_converter.jobs.errors import JobProcessingCancelled
from md_converter.jobs.models import JobProcessResult, JobState
from md_converter.jobs.policy import (
    ArchiveResourceBudget,
    DiagramResourceBudget,
    JobAdmissionPolicy,
    JobExecutionBudget,
    ResourceBudget,
    RetentionPolicy,
)
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.jobs.runner import WorkerSchedule
from md_converter.jobs.runtime import build_job_policies
from md_converter.jobs.service import JobServicePolicy
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.storage import ObjectStore
from tests.settings import template_settings
from tests.unit.jobs.test_job_models import job

pytestmark = pytest.mark.unit
NOW = datetime(2026, 8, 24, tzinfo=UTC)


@pytest.mark.parametrize(
    ("factory", "values"),
    (
        (JobAdmissionPolicy, (0, 1)),
        (JobAdmissionPolicy, (1, True)),
        (ArchiveResourceBudget, (0, 1, 1, 1)),
        (DiagramResourceBudget, (0,)),
        (ResourceBudget, (float("inf"), 1, 1)),
        (ResourceBudget, (1, 0, 1)),
        (RetentionPolicy, (1, 1, 1, 0)),
        (JobServicePolicy, (float("inf"),)),
        (JobServicePolicy, (1, float("inf"))),
        (WorkerSchedule, (1, float("inf"), 1, 1)),
        (WorkerPolicy, (1, 0.5, 1, 1, float("inf"))),
        (JobExecutionBudget, (1, 0, 2)),
    ),
)
def test_resource_policies_reject_unbounded_values(
    factory: Callable[..., Any], values: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        factory(*values)


def test_execution_budget_uses_inclusive_finite_monotonic_deadline() -> None:
    budget = JobExecutionBudget(2.5, 10.0, 12.5)
    assert budget.remaining_seconds(11.0) == 1.5
    assert budget.remaining_seconds(12.5) == 0
    assert budget.exhausted(13.0)
    with pytest.raises(ValueError, match="reading"):
        budget.remaining_seconds(float("nan"))


def test_document_budgets_constrain_archive_and_mermaid_adapters() -> None:
    archive = ArchiveResourceBudget(100, 200, 3, 4).constrain(
        ArchiveLimits(1_000, 20, 500, 2_000, 10.0, 800, 10)
    )
    assert archive.max_archive_bytes == 100
    assert archive.max_total_uncompressed_bytes == 200
    assert archive.max_entries == 20
    assert archive.max_files == 3
    assert archive.max_images == 4
    assert archive.max_member_uncompressed_bytes == 500

    mermaid = DiagramResourceBudget(2).constrain(
        MermaidLimits(10, 100, 200, 300, 400, 500, 600)
    )
    assert mermaid.max_diagrams == 2
    assert mermaid.max_source_bytes == 100


def test_settings_assemble_every_job_policy_without_defaults(tmp_path: Path) -> None:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="sec" + "ret",
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000,
        conversion_request_max_bytes=2_000,
        conversion_retry_after_seconds=3,
        job_result_retention_seconds=120,
    )
    policies = build_job_policies(settings)
    assert policies.admission == JobAdmissionPolicy(2, 10)
    assert policies.documents.archive == ArchiveResourceBudget(
        upload_bytes=1_000,
        decompressed_bytes=10_000_000,
        file_count=100,
        image_count=50,
    )
    assert policies.documents.diagrams == DiagramResourceBudget(diagram_count=20)
    assert policies.worker.max_job_duration_seconds == 60
    assert policies.service.max_job_duration_seconds == 60
    assert policies.schedule.cleanup_interval_seconds == 60
    assert policies.resources.worker_memory_bytes == 536_870_912


@pytest.mark.parametrize(
    "overrides",
    (
        {"template_request_max_bytes": 1_000_000},
        {"worker_heartbeat_seconds": 30.0},
    ),
)
def test_settings_reject_incoherent_resource_relationships(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "sec" + "ret",
        "storage_profile": "standalone",
        "standalone_data_directory": tmp_path,
        "conversion_upload_max_bytes": 1_000,
        "conversion_request_max_bytes": 2_000,
        "conversion_retry_after_seconds": 3,
        "job_result_retention_seconds": 120,
    }
    values.update(overrides)
    with pytest.raises(ValueError):
        Settings.model_validate(values)


def test_upload_and_decompressed_limits_are_independent(tmp_path: Path) -> None:
    settings = Settings(
        **template_settings(conversion_max_decompressed_bytes=500),
        initial_admin_username="admin",
        initial_admin_password="sec" + "ret",
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000,
        conversion_request_max_bytes=2_000,
        conversion_retry_after_seconds=3,
        job_result_retention_seconds=120,
    )
    policies = build_job_policies(settings)
    assert policies.documents.archive.upload_bytes == 1_000
    assert policies.documents.archive.decompressed_bytes == 500


def test_worker_turns_duration_exhaustion_into_safe_failure(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=JobRepository)
    objects = mocker.Mock(spec=ObjectStore)
    processor = mocker.Mock(spec=JobProcessor)
    claimed = job(
        state=JobState.RUNNING,
        attempt=1,
        lease_owner="budget-worker",
        lease_token=uuid4(),
        lease_expires_at=NOW + timedelta(seconds=30),
        heartbeat_at=NOW,
    )
    repository.claim.return_value = claimed
    repository.cancellation_requested.return_value = False
    clock = mocker.Mock(return_value=NOW)
    monotonic_clock = mocker.Mock(side_effect=(0.0, 2.0, 2.0))

    def process(*_args: object, **kwargs: object) -> JobProcessResult:
        cancelled = cast("Callable[[], bool]", kwargs["cancelled"])
        assert cancelled()
        raise JobProcessingCancelled

    processor.process.side_effect = process
    worker = ConversionWorker(
        worker_id="budget-worker",
        runtime=WorkerRuntime(
            repository,
            objects,
            processor,
            clock,
            monotonic_clock,
        ),
        policy=WorkerPolicy(30, 5, 120, 10, max_job_duration_seconds=1),
    )
    assert worker.run_once()
    failure = repository.fail.call_args.args[0]
    assert failure.code == "resource_budget_exceeded"
    assert failure.message == "Conversion exceeded its configured duration budget."
    repository.finish_cancelled.assert_not_called()
