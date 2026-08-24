"""Worker adapter that injects the exact frozen template bytes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from md_converter.jobs.models import ConversionJob, JobProcessResult, JobStep
from md_converter.jobs.ports import JobProcessor
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
        template, content = self._resolver.resolve_frozen_version(
            job.template_id, job.template_version_id
        )
        return self._processor.process_with_template(
            job,
            template,
            content,
            cancelled=cancelled,
            progress=progress,
        )
