"""Unit coverage for the package-native production conversion processor."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from markweave.config import ConfigurationError, Settings
from markweave.conversion.archive import ArchiveLimits
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits
from markweave.conversion.libreoffice import (
    PdfArtifact,
    PdfPage,
    PdfTraceabilityManifest,
)
from markweave.conversion.processor import (
    ProcessorTraceability,
    ProductionTemplateAwareProcessor,
    build_production_processor,
)
from markweave.jobs.errors import JobProcessingCancelled
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobState,
    JobStep,
    SourceKind,
)
from markweave.storage import ObjectKey, ObjectNotFoundError, ObjectScope
from markweave.templates.models import TemplateVersion
from tests.settings import template_settings

pytestmark = pytest.mark.unit


class _Cancellation:
    budget = None

    def __init__(self, *values: bool) -> None:
        self._values = list(values)

    def __call__(self) -> bool:
        return self._values.pop(0) if self._values else False


def _job(
    output: JobOutput,
    source: bytes = b"# Frozen\n",
    *,
    filename: str = "source.md",
    kind: SourceKind = SourceKind.MARKDOWN,
) -> ConversionJob:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    return ConversionJob(
        id=uuid4(),
        owner_id=uuid4(),
        source_object_id=uuid4(),
        template_id=uuid4(),
        template_version_id=uuid4(),
        output=output,
        component_versions=(
            ("chromium", "151.0.7922.173"),
            ("libreoffice", "26.2.5.2"),
            ("md-converter", "0.1.0"),
            ("mermaid-cli", "11.16.0"),
            ("pandoc", "3.10.2"),
        ),
        state=JobState.RUNNING,
        step=JobStep.VALIDATING,
        progress=0,
        request_digest="1" * 64,
        idempotency_digest=None,
        created_at=now,
        updated_at=now,
        attempt=1,
        lease_owner="worker-1",
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(minutes=1),
        source_filename=filename,
        source_kind=kind,
        source_sha256=hashlib.sha256(source).hexdigest(),
        source_size=len(source),
    )


def _template(job: ConversionJob, content: bytes) -> TemplateVersion:
    return TemplateVersion(
        id=job.template_version_id,
        template_id=job.template_id,
        number=3,
        object_owner_id=uuid4(),
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        created_by=uuid4(),
    )


def _manifest() -> PdfTraceabilityManifest:
    return PdfTraceabilityManifest(
        schema_version=1,
        application_version="0.1.0",
        conversion_contract_version="1",
        template_id=str(uuid4()),
        template_version="3",
        template_sha256="2" * 64,
        source_docx_sha256="3" * 64,
        output_pdf_sha256="4" * 64,
        output_pdf_bytes=3,
        pages=(PdfPage(612, 792),),
        pandoc_version="3.10.2",
        pandoc_reader="commonmark_x",
        mermaid_version="11.16.0",
        chromium_version="151.0.7922.173",
        libreoffice_version="26.2.5.2",
        font_manifest_sha256="5" * 64,
        export_filter="pdf:writer_pdf_Export",
        output_format="pdf",
    )


def _processor(mocker, source: bytes):
    objects = mocker.Mock()
    objects.get.return_value = source
    docx = mocker.Mock()
    docx.convert.return_value = b"docx"
    docx.convert_archive.return_value = b"archive-docx"
    pdf = mocker.Mock()
    pdf.convert.return_value = PdfArtifact(b"pdf", _manifest())
    processor = ProductionTemplateAwareProcessor(
        objects=objects,
        docx=docx,
        pdf=pdf,
        archive_limits=ArchiveLimits(1000, 10, 1000, 2000, 100, 1000, 3),
        image_limits=ImageLimits(1000, 100, 100, 10_000, 100, 10),
        traceability=ProcessorTraceability("0.1.0", "1", "commonmark_x", "5" * 64),
    )
    return processor, objects, docx, pdf


@pytest.mark.parametrize("output", [JobOutput.PDF, JobOutput.BOTH])
def test_processor_uses_frozen_source_template_and_traceability(
    mocker, output: JobOutput
) -> None:
    job = _job(output)
    template_content = b"template"
    processor, objects, docx, pdf = _processor(mocker, b"# Frozen\n")
    progress = mocker.Mock()

    result = processor.process_with_template(
        job,
        _template(job, template_content),
        template_content,
        cancelled=_Cancellation(),
        deadline_monotonic=42.0,
        progress=progress,
    )

    objects.get.assert_called_once_with(
        ObjectKey(ObjectScope.UPLOAD, job.owner_id, job.source_object_id)
    )
    docx.convert.assert_called_once_with(
        "# Frozen\n",
        template_content,
        deadline_monotonic=42.0,
        cancellation_requested=mocker.ANY,
    )
    context = pdf.convert.call_args.args[1]
    assert context.template_id == str(job.template_id)
    assert context.template_version == "3"
    assert context.template_sha256 == hashlib.sha256(template_content).hexdigest()
    assert (
        result.progress_manifest == pdf.convert.return_value.manifest.canonical_json()
    )
    assert progress.call_args_list[-1].args == (JobStep.PUBLISHING, 95)
    if output is JobOutput.PDF:
        assert result.content == b"pdf"
    else:
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            assert archive.namelist() == [
                "document.docx",
                "document.pdf",
                "traceability.json",
            ]
            assert archive.read("document.docx") == b"docx"


def test_processor_archive_and_combined_output_are_deterministic(mocker) -> None:
    source = b"PK\x03\x04archive"
    job = _job(
        JobOutput.BOTH,
        source,
        filename="source.zip",
        kind=SourceKind.ARCHIVE,
    )
    processor, _objects, docx, _pdf = _processor(mocker, source)
    template = _template(job, b"template")
    arguments = (job, template, b"template")
    keywords = {
        "cancelled": _Cancellation(),
        "deadline_monotonic": None,
        "progress": mocker.Mock(),
    }

    first = processor.process_with_template(*arguments, **keywords).content
    keywords["cancelled"] = _Cancellation()
    second = processor.process_with_template(*arguments, **keywords).content

    assert first == second
    docx.convert_archive.assert_called_with(
        b"PK\x03\x04archive",
        b"template",
        processor._archive_limits,
        processor._image_limits,
        deadline_monotonic=None,
        cancellation_requested=mocker.ANY,
    )


def test_processor_accepts_ordinary_markdown_starting_with_pk(mocker) -> None:
    source = b"PK prose is ordinary Markdown.\n"
    job = _job(JobOutput.DOCX, source)
    processor, _objects, docx, _pdf = _processor(mocker, source)

    assert processor.process_with_template(
        job,
        _template(job, b"template"),
        b"template",
        cancelled=_Cancellation(),
        deadline_monotonic=None,
        progress=mocker.Mock(),
    ) == JobProcessResult(b"docx")
    docx.convert.assert_called_once_with(
        "PK prose is ordinary Markdown.\n",
        b"template",
        deadline_monotonic=None,
        cancellation_requested=mocker.ANY,
    )


@pytest.mark.parametrize("signature", (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
def test_processor_rejects_real_zip_signatures_for_markdown(
    mocker, signature: bytes
) -> None:
    source = signature + b"payload"
    job = _job(JobOutput.DOCX, source)
    processor, _objects, _docx, _pdf = _processor(mocker, source)

    with pytest.raises(ConversionError) as caught:
        processor.process_with_template(
            job,
            _template(job, b"template"),
            b"template",
            cancelled=_Cancellation(),
            deadline_monotonic=None,
            progress=mocker.Mock(),
        )
    assert caught.value.code is ConversionErrorCode.SOURCE_INTEGRITY


def test_processor_publishes_docx_without_starting_pdf(mocker) -> None:
    source = b"# Document\n"
    job = _job(JobOutput.DOCX, source)
    processor, _objects, _docx, pdf = _processor(mocker, source)

    result = processor.process_with_template(
        job,
        _template(job, b"template"),
        b"template",
        cancelled=_Cancellation(),
        deadline_monotonic=None,
        progress=mocker.Mock(),
    )

    assert result == JobProcessResult(b"docx")
    pdf.convert.assert_not_called()


def test_processor_rejects_missing_non_utf8_and_cancelled_sources(mocker) -> None:
    job = _job(JobOutput.DOCX, b"\xff")
    processor, objects, _docx, _pdf = _processor(mocker, b"\xff")
    template = _template(job, b"template")
    call = {
        "cancelled": _Cancellation(),
        "deadline_monotonic": None,
        "progress": mocker.Mock(),
    }
    with pytest.raises(ConversionError) as invalid:
        processor.process_with_template(job, template, b"template", **call)
    assert invalid.value.code is ConversionErrorCode.VALIDATION

    objects.get.side_effect = ObjectNotFoundError
    with pytest.raises(ConversionError) as missing:
        processor.process_with_template(job, template, b"template", **call)
    assert missing.value.code is ConversionErrorCode.SOURCE_INTEGRITY

    with pytest.raises(JobProcessingCancelled):
        processor.process_with_template(
            job,
            template,
            b"template",
            cancelled=_Cancellation(True),
            deadline_monotonic=None,
            progress=mocker.Mock(),
        )


def test_processor_maps_active_docx_engine_interruption_to_worker_cancellation(
    mocker,
) -> None:
    source = b"# Blocking\n"
    job = _job(JobOutput.DOCX, source)
    processor, _objects, docx, _pdf = _processor(mocker, source)
    docx.convert.side_effect = ConversionError(
        ConversionErrorCode.PANDOC_FAILURE, "Pandoc conversion was interrupted."
    )

    with pytest.raises(JobProcessingCancelled):
        processor.process_with_template(
            job,
            _template(job, b"template"),
            b"template",
            cancelled=_Cancellation(False, False, True),
            deadline_monotonic=None,
            progress=mocker.Mock(),
        )


@pytest.mark.parametrize("tamper", ["hash", "size", "filename_kind", "content_kind"])
def test_processor_rejects_every_frozen_source_integrity_mismatch(
    mocker, tamper: str
) -> None:
    source = b"# Immutable\n"
    job = _job(JobOutput.DOCX, source)
    if tamper == "hash":
        object.__setattr__(job, "source_sha256", "f" * 64)
    elif tamper == "size":
        object.__setattr__(job, "source_size", len(source) + 1)
    elif tamper == "filename_kind":
        object.__setattr__(job, "source_filename", "source.zip")
    else:
        object.__setattr__(job, "source_kind", SourceKind.ARCHIVE)
        object.__setattr__(job, "source_filename", "source.zip")
    processor, _objects, _docx, _pdf = _processor(mocker, source)

    with pytest.raises(ConversionError) as caught:
        processor.process_with_template(
            job,
            _template(job, b"template"),
            b"template",
            cancelled=_Cancellation(),
            deadline_monotonic=None,
            progress=mocker.Mock(),
        )

    assert caught.value.code is ConversionErrorCode.SOURCE_INTEGRITY


def test_production_processor_assembles_locked_adapters_and_manifest(
    mocker, tmp_path: Path
) -> None:
    manifest = tmp_path / "font-manifest.json"
    manifest.write_bytes(b'{"fonts":[]}')
    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password="test-" + "password",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        storage_profile="standalone",
        standalone_data_directory=tmp_path / "data",
        **template_settings(conversion_font_manifest_path=manifest),
    )

    processor = build_production_processor(settings, mocker.Mock())

    assert isinstance(processor, ProductionTemplateAwareProcessor)
    assert (
        processor._traceability.font_manifest_sha256
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )

    manifest.unlink()
    with pytest.raises(ConfigurationError, match="font manifest"):
        build_production_processor(settings, mocker.Mock())
