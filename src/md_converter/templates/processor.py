"""Worker adapter that injects the exact frozen template bytes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.jobs.models import ConversionJob, JobProcessResult, JobStep
from md_converter.jobs.ports import JobProcessor, JobRepository
from md_converter.jobs.worker import ConversionWorker, WorkerPolicy, WorkerRuntime
from md_converter.storage import ObjectStore
from md_converter.templates.errors import (
    TemplateIntegrityError,
    TemplateStorageError,
    TemplateUnavailableError,
)
from md_converter.templates.models import TemplateVersion


class FrozenTemplateResolver(Protocol):
    """Integrity-verifying lookup for one persisted template/version pair."""

    def resolve_frozen_version(
        self, template_id: UUID, version_id: UUID
    ) -> tuple[TemplateVersion, bytes]: ...


class TemplateAwareProcessor(Protocol):
    """Production conversion boundary supplied with immutable template content."""

    def process_with_template(
        self,
        job: ConversionJob,
        template: TemplateVersion,
        template_content: bytes,
        *,
        cancelled: Callable[[], bool],
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
        cancelled: Callable[[], bool],
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        try:
            template, content = self._resolver.resolve_frozen_version(
                job.template_id, job.template_version_id
            )
        except TemplateIntegrityError, TemplateStorageError, TemplateUnavailableError:
            raise ConversionError(
                ConversionErrorCode.TEMPLATE_INTEGRITY,
                "Frozen template content could not be verified.",
            ) from None
        return self._processor.process_with_template(
            job,
            template,
            content,
            cancelled=cancelled,
            progress=progress,
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
) -> ConversionWorker:
    """Compose a worker that cannot bypass frozen-template resolution."""
    return ConversionWorker(
        worker_id=worker_id,
        runtime=WorkerRuntime(
            repository,
            objects,
            FrozenTemplateJobProcessor(resolver, processor),
            clock,
        ),
        policy=policy,
    )
