"""Frozen-template injection at the worker processor boundary."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.jobs.models import JobProcessResult
from markweave.jobs.policy import JobExecutionBudget
from markweave.jobs.worker import ConversionWorker, WorkerPolicy
from markweave.templates.errors import TemplateIntegrityError
from markweave.templates.models import TemplateVersion
from markweave.templates.processor import (
    FrozenTemplateJobProcessor,
    build_template_conversion_worker,
)
from tests.unit.jobs.test_job_models import job


class NoBudgetProbe:
    """Non-cancelled probe satisfying the worker processor contract."""

    budget = None

    def __call__(self) -> bool:
        return False


@pytest.mark.unit
def test_processor_resolves_and_passes_exact_frozen_version(
    mocker: MockerFixture,
) -> None:
    frozen_job = job()
    assert frozen_job.template_version_id is not None
    assert frozen_job.template_id is not None
    version = TemplateVersion(
        frozen_job.template_version_id,
        frozen_job.template_id,
        7,
        uuid4(),
        "a" * 64,
        4,
        datetime.now(UTC),
        uuid4(),
        declared_fonts=("Calibri",),
        resolved_fonts=(("Calibri", "Carlito"),),
        validation_trace=("static_ooxml",),
    )
    resolver = mocker.Mock()
    resolver.resolve_frozen_version.return_value = (version, b"docx")
    delegate = mocker.Mock()
    delegate.process_with_template.return_value = JobProcessResult(b"result")
    cancelled = mocker.Mock(return_value=False)
    cancelled.budget = JobExecutionBudget(10.0, 20.0, 30.0)
    progress = mocker.Mock()

    result = FrozenTemplateJobProcessor(resolver, delegate).process(
        frozen_job, cancelled=cancelled, progress=progress
    )

    assert result.content == b"result"
    assert result.template_version == "7"
    assert result.template_sha256 == "a" * 64
    resolver.resolve_frozen_version.assert_called_once_with(
        frozen_job.template_id, frozen_job.template_version_id
    )
    delegate.process_with_template.assert_called_once_with(
        frozen_job,
        version,
        b"docx",
        cancelled=cancelled,
        deadline_monotonic=30.0,
        progress=progress,
    )


@pytest.mark.unit
def test_processor_sanitizes_integrity_failure_and_factory_wraps(
    mocker: MockerFixture,
) -> None:
    resolver = mocker.Mock()
    resolver.resolve_frozen_version.side_effect = TemplateIntegrityError
    with pytest.raises(Exception) as raised:
        FrozenTemplateJobProcessor(resolver, mocker.Mock()).process(
            job(), cancelled=NoBudgetProbe(), progress=lambda _step, _value: None
        )
    assert getattr(raised.value, "code", None) == "template_integrity"

    worker = build_template_conversion_worker(
        worker_id="worker",
        repository=mocker.Mock(),
        objects=mocker.Mock(),
        resolver=resolver,
        processor=mocker.Mock(),
        clock=lambda: datetime.now(UTC),
        policy=WorkerPolicy(2, 1, 60, 1),
    )
    assert isinstance(worker, ConversionWorker)


@pytest.mark.unit
def test_processor_bypasses_resolver_for_pandoc_default(mocker: MockerFixture) -> None:
    template_free_job = job(template_id=None, template_version_id=None)
    resolver = mocker.Mock()
    delegate = mocker.Mock()
    delegate.process_without_template.return_value = JobProcessResult(b"result")

    result = FrozenTemplateJobProcessor(resolver, delegate).process(
        template_free_job,
        cancelled=NoBudgetProbe(),
        progress=mocker.Mock(),
    )

    assert result.content == b"result"
    assert result.template_version is None
    assert result.template_sha256 is None
    resolver.resolve_frozen_version.assert_not_called()
    delegate.process_without_template.assert_called_once()
