"""Worker adapter that injects the exact frozen template bytes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from time import monotonic
from typing import Protocol, cast
from uuid import UUID

from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.jobs.models import ConversionJob, JobProcessResult, JobStep
from markweave.jobs.ports import CancellationProbe, JobProcessor, JobRepository
from markweave.jobs.worker import (
    ConversionWorker,
    MaintenanceCleaner,
    WorkerPolicy,
    WorkerRuntime,
)
from markweave.observability import OperationalMetrics
from markweave.storage import ObjectStore
from markweave.templates.errors import (
    TemplateIntegrityError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from markweave.templates.models import TemplateVersion


class FrozenTemplateResolver(Protocol):
    """Integrity-verifying lookup for one persisted template/version pair."""

    def resolve_frozen_version(
        self, template_id: UUID, version_id: UUID
    ) -> tuple[TemplateVersion, bytes]: ...


class TemplateAwareProcessor(Protocol):
    """Production conversion boundary supplied with immutable template content."""

    def process_with_template(  # noqa: PLR0913 - explicit worker boundary
        self,
        job: ConversionJob,
        template: TemplateVersion,
        template_content: bytes,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult: ...


class TemplateOptionalProcessor(TemplateAwareProcessor, Protocol):
    """Extended processor capable of using Pandoc's native reference document."""

    def process_without_template(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult: ...


class FrozenTemplateJobProcessor(JobProcessor):
    """Resolve exactly the pair frozen at submission before processing starts."""

    def __init__(
        self, resolver: FrozenTemplateResolver, processor: TemplateAwareProcessor
    ) -> None:
        self._resolver = resolver
        self._processor = processor

    def process(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        deadline = (
            cancelled.budget.deadline_monotonic
            if cancelled.budget is not None
            else None
        )
        if job.template_id is None:
            processor = cast("TemplateOptionalProcessor", self._processor)
            return replace(
                processor.process_without_template(
                    job,
                    cancelled=cancelled,
                    deadline_monotonic=deadline,
                    progress=progress,
                ),
                template_version=None,
                template_sha256=None,
            )
        if job.template_version_id is None:
            raise AssertionError("Conversion job contains a partial template pair")
        try:
            template, content = self._resolver.resolve_frozen_version(
                job.template_id, job.template_version_id
            )
        except TemplateIntegrityError, TemplateStorageError, TemplateUnavailableError:
            raise ConversionError(
                ConversionErrorCode.TEMPLATE_INTEGRITY,
                "Frozen template content could not be verified.",
            ) from None
        return replace(
            self._processor.process_with_template(
                job,
                template,
                content,
                cancelled=cancelled,
                deadline_monotonic=deadline,
                progress=progress,
            ),
            template_version=str(template.number),
            template_sha256=template.sha256,
        )


def build_template_conversion_worker(  # noqa: PLR0913
    *,
    worker_id: str,
    repository: JobRepository,
    objects: ObjectStore,
    resolver: FrozenTemplateResolver,
    processor: TemplateAwareProcessor,
    clock: Callable[[], datetime],
    policy: WorkerPolicy,
    maintenance: MaintenanceCleaner | None = None,
    monotonic_clock: Callable[[], float] = monotonic,
    metrics: OperationalMetrics | None = None,
) -> ConversionWorker:
    """Compose a worker that cannot bypass frozen-template resolution."""
    return ConversionWorker(
        worker_id=worker_id,
        runtime=WorkerRuntime(
            repository,
            objects,
            FrozenTemplateJobProcessor(resolver, processor),
            clock,
            monotonic_clock=monotonic_clock,
            maintenance=maintenance,
            metrics=metrics,
        ),
        policy=policy,
    )
