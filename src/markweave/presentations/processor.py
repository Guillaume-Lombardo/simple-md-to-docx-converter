"""Presentation execution and portable source export inside the durable worker."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict

from markweave.conversion.archive import ArchiveLimits
from markweave.conversion.images import ImageLimits
from markweave.conversion.mermaid import (
    MermaidLimits,
    MermaidPreprocessingConverter,
    MermaidRenderer,
)
from markweave.conversion.pandoc import PandocConfig
from markweave.conversion.service import DocxConversionService
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobStep,
    SourceKind,
)
from markweave.jobs.ports import CancellationProbe
from markweave.presentations.models import PresentationOptions
from markweave.presentations.pandoc import PandocPptxConverter
from markweave.presentations.preparation import plan_presentation, prepare_source


class PresentationProcessor:
    def __init__(  # noqa: PLR0913 - bounded pipeline configuration
        self,
        *,
        pandoc_config: PandocConfig,
        environment: Mapping[str, str],
        renderer: MermaidRenderer,
        mermaid_limits: MermaidLimits,
        archive_limits: ArchiveLimits,
        image_limits: ImageLimits,
    ) -> None:
        self._config = pandoc_config
        self._environment = environment
        self._renderer = renderer
        self._mermaid_limits = mermaid_limits
        self._archive_limits = archive_limits
        self._image_limits = image_limits

    def process(  # noqa: PLR0913 - worker lifecycle callbacks
        self,
        job: ConversionJob,
        source: bytes,
        reference: bytes | None,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        document = prepare_source(
            source,
            archive=job.source_kind is SourceKind.ARCHIVE,
            archive_limits=self._archive_limits,
            image_limits=self._image_limits,
        )
        plan = plan_presentation(
            document, job.presentation_options or PresentationOptions()
        )
        converter = MermaidPreprocessingConverter(
            PandocPptxConverter(
                self._config,
                self._environment,
                slide_level=0 if plan.explicit_breaks else plan.options.slide_level,
            ),
            self._renderer,
            self._mermaid_limits,
            self._image_limits,
        )
        content = DocxConversionService(converter).convert_document(
            plan.document,
            reference,
            cancellation_requested=cancelled,
            deadline_monotonic=deadline_monotonic,
        )
        progress(JobStep.PPTX, 90)
        if job.output is JobOutput.PPTX_BUNDLE:
            content = portable_package(job, source, content, asdict(plan.options))
        progress(JobStep.PUBLISHING, 95)
        return JobProcessResult(content)


def portable_package(
    job: ConversionJob, source: bytes, presentation: bytes, options: dict[str, object]
) -> bytes:
    """Keep original source bytes, including comments, separate from edited slide extraction."""
    settings = {
        "schema_version": 1,
        "options": options,
        "template_mode": job.template_mode.value,
        "template_id": str(job.template_id) if job.template_id else None,
        "template_version_id": str(job.template_version_id)
        if job.template_version_id
        else None,
    }
    parts = [
        ("presentation.pptx", presentation),
        ("generation.json", json.dumps(settings, sort_keys=True, indent=2).encode()),
        (
            "README.md",
            b"# Re-editable presentation\n\nThe source directory contains the original input, not later PowerPoint edits.\nRe-upload source/document.md or source/source.zip to 2pptx.\nThe selected template is identified in generation.json and is not embedded.\n",
        ),
    ]
    if job.source_kind is SourceKind.ARCHIVE:
        parts.append(("source/source.zip", source))
    else:
        parts.append(("source/document.md", source))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()
