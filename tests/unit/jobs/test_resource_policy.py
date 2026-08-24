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
from md_converter.jobs.errors import JobProcessingCancelled
from md_converter.jobs.models import JobProcessResult, JobState
from md_converter.jobs.policy import (
    DocumentResourceBudget,
    JobAdmissionPolicy,
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
        (DocumentResourceBudget, (2, 1, 1, 1, 1)),
        (ResourceBudget, (float("inf"), 1, 1)),
        (ResourceBudget, (1, 0, 1)),
        (RetentionPolicy, (1, 1, 1, 0)),
        (JobServicePolicy, (float("inf"),)),
        (WorkerSchedule, (1, float("inf"), 1, 1)),
        (WorkerPolicy, (1, 0.5, 1, 1, float("inf"))),
    ),
)
def test_resource_policies_reject_unbounded_values(
    factory: Callable[..., Any], values: tuple[object, ...]
) -> None:
    with pytest.raises(ValueError):
        factory(*values)


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
    assert policies.documents.file_count == 100
    assert policies.worker.max_job_duration_seconds == 60
    assert policies.schedule.cleanup_interval_seconds == 60
    assert policies.resources.worker_memory_bytes == 536_870_912


@pytest.mark.parametrize(
    "overrides",
    (
        {"conversion_max_decompressed_bytes": 999},
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
    clock = mocker.Mock(
        side_effect=(NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=2))
    )

    def process(*_args: object, **kwargs: object) -> JobProcessResult:
        cancelled = cast("Callable[[], bool]", kwargs["cancelled"])
        assert cancelled()
        raise JobProcessingCancelled

    processor.process.side_effect = process
    worker = ConversionWorker(
        worker_id="budget-worker",
        runtime=WorkerRuntime(repository, objects, processor, clock),
        policy=WorkerPolicy(30, 5, 120, 10, max_job_duration_seconds=1),
    )
    assert worker.run_once()
    failure = repository.fail.call_args.args[0]
    assert failure.code == "resource_budget_exceeded"
    assert failure.message == "Conversion exceeded its configured duration budget."
    repository.finish_cancelled.assert_not_called()
