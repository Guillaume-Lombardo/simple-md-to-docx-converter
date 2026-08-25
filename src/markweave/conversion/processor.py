"""Production conversion processor for immutable job inputs."""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from markweave.config import ConfigurationError, Settings
from markweave.conversion.archive import ArchiveLimits
from markweave.conversion.errors import (
    ConversionError,
    ConversionErrorCode,
    validation_error,
)
from markweave.conversion.images import ImageLimits
from markweave.conversion.libreoffice import (
    LibreOfficeConfig,
    LibreOfficePdfConverter,
    PdfLimits,
    PdfTraceabilityContext,
)
from markweave.conversion.mermaid import (
    MermaidCliRenderer,
    MermaidConfig,
    MermaidLimits,
    MermaidPreprocessingConverter,
)
from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
from markweave.conversion.service import DocxConversionService
from markweave.conversion.validation import PANDOC_READER
from markweave.jobs.errors import JobProcessingCancelled
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobStep,
    SourceKind,
    source_kind_for_filename,
)
from markweave.jobs.ports import CancellationProbe
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)
from markweave.templates.models import TemplateVersion
from markweave.version import VERSION


@dataclass(frozen=True, slots=True)
class ProcessorTraceability:
    """Immutable application and toolchain identity attached to PDF output."""

    application_version: str
    conversion_contract_version: str
    pandoc_reader: str
    font_manifest_sha256: str


class ProductionTemplateAwareProcessor:
    """Load a frozen source and run the validated document-engine pipeline."""

    def __init__(  # noqa: PLR0913 - explicit adapter composition
        self,
        *,
        objects: ObjectStore,
        docx: DocxConversionService,
        pdf: LibreOfficePdfConverter,
        archive_limits: ArchiveLimits,
        image_limits: ImageLimits,
        traceability: ProcessorTraceability,
    ) -> None:
        self._objects = objects
        self._docx = docx
        self._pdf = pdf
        self._archive_limits = archive_limits
        self._image_limits = image_limits
        self._traceability = traceability

    def process_with_template(  # noqa: PLR0913 - explicit worker boundary
        self,
        job: ConversionJob,
        template: TemplateVersion,
        template_content: bytes,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        self._require_active(cancelled)
        source = self._load_source(job)
        progress(JobStep.VALIDATING, 10)
        self._require_active(cancelled)
        progress(JobStep.RENDERING, 30)
        try:
            docx = self._convert_docx(
                source,
                job.source_kind,
                template_content,
                deadline_monotonic=deadline_monotonic,
                cancellation_requested=cancelled,
            )
        except ConversionError:
            self._require_active(cancelled)
            raise
        progress(JobStep.DOCX, 70)
        self._require_active(cancelled)
        if job.output is JobOutput.DOCX:
            progress(JobStep.PUBLISHING, 95)
            return JobProcessResult(docx)

        artifact = self._pdf.convert(
            docx,
            self._traceability_context(job, template),
            cancellation_requested=cancelled,
            deadline_monotonic=deadline_monotonic,
        )
        manifest = artifact.manifest.canonical_json()
        progress(JobStep.PDF, 90)
        self._require_active(cancelled)
        progress(JobStep.PUBLISHING, 95)
        if job.output is JobOutput.PDF:
            return JobProcessResult(artifact.pdf, manifest)
        return JobProcessResult(
            _combined_archive(docx, artifact.pdf, manifest),
            manifest,
        )

    def _load_source(self, job: ConversionJob) -> bytes:
        if (
            job.source_filename is None
            or job.source_kind is None
            or job.source_sha256 is None
            or job.source_size is None
        ):
            raise _source_integrity_error()
        try:
            if source_kind_for_filename(job.source_filename) is not job.source_kind:
                raise _source_integrity_error()
        except ValueError:
            raise _source_integrity_error() from None
        try:
            source = self._objects.get(
                ObjectKey(ObjectScope.UPLOAD, job.owner_id, job.source_object_id)
            )
        except ObjectNotFoundError, ObjectStoreError:
            raise _source_integrity_error() from None
        if (
            len(source) != job.source_size
            or hashlib.sha256(source).hexdigest() != job.source_sha256
        ):
            raise _source_integrity_error()
        return source

    def _convert_docx(
        self,
        source: bytes,
        source_kind: SourceKind | None,
        template_content: bytes,
        *,
        deadline_monotonic: float | None,
        cancellation_requested: CancellationProbe,
    ) -> bytes:
        if source_kind is SourceKind.ARCHIVE:
            if source[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
                raise _source_integrity_error()
            return self._docx.convert_archive(
                source,
                template_content,
                self._archive_limits,
                self._image_limits,
                deadline_monotonic=deadline_monotonic,
                cancellation_requested=cancellation_requested,
            )
        if source_kind is not SourceKind.MARKDOWN or source[:4] in {
            b"PK\x03\x04",
            b"PK\x05\x06",
            b"PK\x07\x08",
        }:
            raise _source_integrity_error()
        try:
            markdown = source.decode("utf-8")
        except UnicodeDecodeError:
            raise validation_error("Markdown input is not valid UTF-8.") from None
        return self._docx.convert(
            markdown,
            template_content,
            deadline_monotonic=deadline_monotonic,
            cancellation_requested=cancellation_requested,
        )

    def _traceability_context(
        self, job: ConversionJob, template: TemplateVersion
    ) -> PdfTraceabilityContext:
        versions = dict(job.component_versions)
        return PdfTraceabilityContext(
            application_version=self._traceability.application_version,
            conversion_contract_version=self._traceability.conversion_contract_version,
            template_id=str(template.template_id),
            template_version=str(template.number),
            template_sha256=template.sha256,
            pandoc_version=versions["pandoc"],
            pandoc_reader=self._traceability.pandoc_reader,
            mermaid_version=versions["mermaid-cli"],
            chromium_version=versions["chromium"],
            font_manifest_sha256=self._traceability.font_manifest_sha256,
        )

    @staticmethod
    def _require_active(cancelled: CancellationProbe) -> None:
        if cancelled():
            raise JobProcessingCancelled


def _source_integrity_error() -> ConversionError:
    return ConversionError(
        ConversionErrorCode.SOURCE_INTEGRITY,
        "Frozen source content could not be verified.",
    )


def build_production_processor(
    settings: Settings, objects: ObjectStore
) -> ProductionTemplateAwareProcessor:
    """Compose the locked image toolchain from validated production settings."""

    try:
        font_manifest = settings.conversion_font_manifest_path.read_bytes()
    except OSError:
        raise ConfigurationError("Conversion font manifest is unavailable") from None
    workspace = settings.template_engine_workspace_root
    image_limits = ImageLimits(
        settings.conversion_image_max_source_bytes,
        settings.conversion_image_max_width_pixels,
        settings.conversion_image_max_height_pixels,
        settings.conversion_image_max_pixels,
        settings.conversion_image_max_svg_elements,
        settings.conversion_image_max_svg_depth,
    )
    archive_limits = ArchiveLimits(
        max_archive_bytes=settings.conversion_upload_max_bytes,
        max_entries=settings.conversion_max_files,
        max_member_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_total_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_compression_ratio=settings.conversion_max_compression_ratio,
        max_markdown_bytes=settings.conversion_upload_max_bytes,
        max_images=settings.conversion_max_images,
        max_files=settings.conversion_max_files,
    )
    pandoc = PandocDocxConverter(
        PandocConfig(
            settings.template_pandoc_executable,
            settings.template_engine_timeout_seconds,
            settings.template_engine_termination_grace_seconds,
            workspace,
            settings.conversion_pdf_cancellation_poll_seconds,
        ),
        os.environ,
    )
    mermaid = MermaidPreprocessingConverter(
        pandoc,
        MermaidCliRenderer(
            MermaidConfig(
                settings.conversion_mermaid_executable,
                settings.conversion_chromium_executable,
                settings.template_engine_timeout_seconds,
                settings.template_engine_termination_grace_seconds,
                settings.conversion_mermaid_max_width_pixels,
                settings.conversion_mermaid_max_height_pixels,
                workspace,
                settings.conversion_pdf_cancellation_poll_seconds,
            ),
            os.environ,
        ),
        MermaidLimits(
            settings.conversion_max_diagrams,
            settings.conversion_mermaid_max_source_bytes,
            settings.conversion_mermaid_max_total_source_bytes,
            settings.conversion_mermaid_max_output_bytes,
            settings.conversion_mermaid_max_total_output_bytes,
            settings.conversion_mermaid_max_width_pixels,
            settings.conversion_mermaid_max_height_pixels,
        ),
        image_limits,
    )
    pdf = LibreOfficePdfConverter(
        LibreOfficeConfig(
            settings.template_libreoffice_executable,
            "26.2.5.2",
            settings.template_engine_timeout_seconds,
            settings.template_engine_termination_grace_seconds,
            settings.conversion_pdf_cancellation_poll_seconds,
            workspace,
        ),
        PdfLimits(
            max_docx_bytes=settings.conversion_max_decompressed_bytes,
            max_docx_entries=settings.template_max_entries,
            max_docx_member_uncompressed_bytes=settings.template_max_member_bytes,
            max_docx_total_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
            max_docx_compression_ratio=settings.conversion_max_compression_ratio,
            max_pdf_bytes=settings.conversion_pdf_max_bytes,
            max_pdf_decoded_stream_bytes=(
                settings.conversion_pdf_max_decoded_stream_bytes
            ),
            max_pages=settings.conversion_pdf_max_pages,
            max_pdf_objects=settings.conversion_pdf_max_objects,
            max_pdf_object_depth=settings.conversion_pdf_max_object_depth,
        ),
        os.environ,
    )
    return ProductionTemplateAwareProcessor(
        objects=objects,
        docx=DocxConversionService(mermaid),
        pdf=pdf,
        archive_limits=archive_limits,
        image_limits=image_limits,
        traceability=ProcessorTraceability(
            application_version=VERSION,
            conversion_contract_version="1",
            pandoc_reader=PANDOC_READER,
            font_manifest_sha256=hashlib.sha256(font_manifest).hexdigest(),
        ),
    )


def _combined_archive(docx: bytes, pdf: bytes, manifest: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name, content in (
            ("document.docx", docx),
            ("document.pdf", pdf),
            ("traceability.json", manifest),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            info.create_system = 3
            archive.writestr(info, content, compresslevel=9)
    return output.getvalue()
