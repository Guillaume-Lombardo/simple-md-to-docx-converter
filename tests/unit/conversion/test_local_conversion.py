"""Unit tests for the serverless local conversion pipeline (T106, D1)."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from markweave.conversion import local as local_module
from markweave.conversion.engine_versions import (
    EngineObservation,
    EngineProbeError,
    EngineVersionStatus,
    approved_engine_versions,
)
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.libreoffice import (
    PdfArtifact,
    PdfPage,
    PdfTraceabilityContext,
    PdfTraceabilityManifest,
)
from markweave.conversion.local import (
    LocalConversionError,
    LocalConversionRequest,
    LocalConverter,
    LocalOutput,
)
from markweave.conversion.processor import build_document_engines
from markweave.jobs.models import SourceKind
from tests.settings import local_conversion_settings

pytestmark = pytest.mark.unit

TEMPLATE = base64.b64decode(
    Path("examples/quickstart-template.docx.base64").read_bytes()
)
MERMAID = b"# Diagram\n\n```mermaid\nflowchart LR\n  A --> B\n```\n"


class FakeProbe:
    """Record required engines and return configured observations."""

    def __init__(self, **versions: str | None) -> None:
        self.versions = versions
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, str] = {}

    def observe(
        self,
        name: str,
        executable: str,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> EngineObservation:
        del deadline_monotonic
        self.calls.append((name, executable))
        assert callable(cancellation_requested)
        if name in self.failures:
            raise EngineProbeError(name, self.failures[name])
        approved = approved_engine_versions()[name]
        actual = self.versions.get(name, approved)
        if actual is None:
            status = EngineVersionStatus.UNKNOWN
        elif actual == approved:
            status = EngineVersionStatus.PINNED
        else:
            status = EngineVersionStatus.DIFFERENT
        return EngineObservation(name, approved, actual, status)


class FakeDocx:
    def __init__(self, output: bytes = b"docx-bytes") -> None:
        self.output = output
        self.calls: list[tuple[str, Any, bytes | None]] = []

    def convert(self, markdown: str, reference: bytes | None, **options: Any) -> bytes:
        self.calls.append(("markdown", markdown, reference))
        return self.output

    def convert_document(
        self, document: Any, reference: bytes | None, **options: Any
    ) -> bytes:
        self.calls.append(("document", document, reference))
        return self.output


class FakePdf:
    def __init__(self) -> None:
        self.contexts: list[PdfTraceabilityContext] = []

    def convert(
        self, docx: bytes, context: PdfTraceabilityContext, cancelled, **options: Any
    ) -> PdfArtifact:
        self.contexts.append(context)
        manifest = PdfTraceabilityManifest(
            schema_version=2,
            application_version=context.application_version,
            conversion_contract_version=context.conversion_contract_version,
            template_id=context.template_id,
            template_version=context.template_version,
            template_sha256=context.template_sha256,
            source_docx_sha256=hashlib.sha256(docx).hexdigest(),
            output_pdf_sha256=hashlib.sha256(b"%PDF").hexdigest(),
            output_pdf_bytes=4,
            pages=(PdfPage(595.0, 842.0),),
            pandoc_version=context.pandoc_version,
            pandoc_reader=context.pandoc_reader,
            mermaid_version=context.mermaid_version,
            chromium_version=context.chromium_version,
            libreoffice_version="engine-reported",
            font_manifest_sha256=context.font_manifest_sha256,
            export_filter="pdf:writer_pdf_Export",
            output_format="pdf",
            template_mode=context.template_mode,
        )
        return PdfArtifact(b"%PDF", manifest)


@pytest.fixture
def pipeline(mocker):
    docx, pdf = FakeDocx(), FakePdf()
    built: list[dict[str, Any]] = []

    def build(settings, **options):
        built.append(options)
        return replace(build_document_engines(settings, **options), docx=docx, pdf=pdf)

    mocker.patch.object(local_module, "build_document_engines", side_effect=build)
    return docx, pdf, built


def request(
    source: bytes = b"# Local\n",
    kind: SourceKind = SourceKind.MARKDOWN,
    outputs: tuple[LocalOutput, ...] = (LocalOutput.DOCX,),
    **options: Any,
) -> LocalConversionRequest:
    return LocalConversionRequest(source, kind, frozenset(outputs), **options)


def archive(markdown: bytes, **members: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("document.md", markdown)
        for name, content in members.items():
            package.writestr(name, content)
    return output.getvalue()


def test_docx_conversion_probes_only_pandoc_and_writes_content_free_manifest(
    pipeline,
) -> None:
    docx, _, built = pipeline
    probe = FakeProbe()
    converter = LocalConverter(local_conversion_settings(), {}, probe=probe)

    result = converter.convert(request(b"# Local\n"))

    assert probe.calls == [("pandoc", "pandoc")]
    assert docx.calls == [("markdown", "# Local\n", None)]
    assert built[0]["libreoffice_version"] == "not-used"
    assert result.outputs == {LocalOutput.DOCX: b"docx-bytes"}
    assert result.pinned
    assert result.warnings == ()
    manifest = json.loads(result.manifest)
    assert manifest == {
        "application_version": manifest["application_version"],
        "conversion_contract_version": "2",
        "engines": [
            {
                "actual_version": "3.10.2",
                "approved_version": "3.10.2",
                "name": "pandoc",
                "status": "pinned",
            }
        ],
        "engines_pinned": True,
        "mode": "local",
        "outputs": [
            {
                "bytes": 10,
                "format": "docx",
                "sha256": hashlib.sha256(b"docx-bytes").hexdigest(),
            }
        ],
        "pandoc_reader": (
            "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
        ),
        "pdf": None,
        "require_pinned": False,
        "schema_version": 1,
        "source": {
            "bytes": 8,
            "kind": "markdown",
            "sha256": hashlib.sha256(b"# Local\n").hexdigest(),
        },
        "template": {"mode": "pandoc-default"},
    }
    assert (
        result.manifest
        == json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    )


def test_pdf_with_template_records_actual_versions_and_local_template(
    pipeline,
) -> None:
    docx, pdf, built = pipeline
    probe = FakeProbe(libreoffice="7.6.4.1")
    settings = local_conversion_settings()

    result = LocalConverter(settings, {}, probe=probe).convert(
        request(
            MERMAID,
            outputs=(LocalOutput.PDF,),
            template=TEMPLATE,
        )
    )

    assert [name for name, _ in probe.calls] == [
        "pandoc",
        "libreoffice",
        "mermaid-cli",
        "chromium",
    ]
    assert built[0]["libreoffice_version"] == "7.6.4.1"
    assert docx.calls[0][2] == TEMPLATE
    assert result.outputs == {LocalOutput.PDF: b"%PDF"}
    assert result.warnings == (
        "LibreOffice 7.6.4.1 differs from the approved pin 26.2.5.2.",
    )
    assert not result.pinned
    context = pdf.contexts[0]
    assert context.template_mode == "local-file"
    assert context.template_sha256 == hashlib.sha256(TEMPLATE).hexdigest()
    assert (context.template_id, context.template_version) == (None, None)
    assert context.mermaid_version == approved_engine_versions()["mermaid-cli"]
    assert (
        context.font_manifest_sha256
        == hashlib.sha256(
            Path("toolchain/document-engines/fonts/manifest.json").read_bytes()
        ).hexdigest()
    )
    manifest = json.loads(result.manifest)
    assert manifest["template"] == {
        "mode": "local-file",
        "sha256": hashlib.sha256(TEMPLATE).hexdigest(),
    }
    assert manifest["engines_pinned"] is False
    assert manifest["pdf"]["template_mode"] == "local-file"
    assert [output["format"] for output in manifest["outputs"]] == ["pdf"]


def test_pdf_without_mermaid_marks_unused_engines(pipeline) -> None:
    _, pdf, _ = pipeline
    result = LocalConverter(
        local_conversion_settings(), {}, probe=FakeProbe(pandoc=None)
    ).convert(request(outputs=(LocalOutput.DOCX, LocalOutput.PDF)))

    context = pdf.contexts[0]
    assert (context.mermaid_version, context.chromium_version) == (
        "not-used",
        "not-used",
    )
    assert context.pandoc_version == "unknown"
    assert context.template_mode == "pandoc-default"
    assert set(result.outputs) == {LocalOutput.DOCX, LocalOutput.PDF}
    assert result.warnings == (
        "The Pandoc version could not be determined; the approved pin is 3.10.2.",
    )


@pytest.mark.parametrize("version", ["3.9.0", None])
def test_require_pinned_turns_the_warning_into_a_failure(
    pipeline, version: str | None
) -> None:
    docx, _, _ = pipeline
    with pytest.raises(LocalConversionError) as captured:
        LocalConverter(
            local_conversion_settings(), {}, probe=FakeProbe(pandoc=version)
        ).convert(request(require_pinned=True))
    assert captured.value.code == "engine_version_unpinned"
    assert docx.calls == []


def test_require_pinned_accepts_exact_pins(pipeline) -> None:
    result = LocalConverter(local_conversion_settings(), {}, probe=FakeProbe()).convert(
        request(require_pinned=True)
    )
    assert json.loads(result.manifest)["require_pinned"] is True


@pytest.mark.parametrize(
    ("reason", "code", "message"),
    [
        (
            "unavailable",
            "engine_missing",
            "The required LibreOffice engine is missing or cannot run.",
        ),
        (
            "timeout",
            "engine_probe_timeout",
            "The LibreOffice version check timed out.",
        ),
        ("cancelled", "conversion_cancelled", "The local conversion was cancelled."),
    ],
)
def test_required_engine_failures_are_clear(
    pipeline, reason: str, code: str, message: str
) -> None:
    probe = FakeProbe()
    probe.failures["libreoffice"] = reason
    with pytest.raises(LocalConversionError) as captured:
        LocalConverter(local_conversion_settings(), {}, probe=probe).convert(
            request(outputs=(LocalOutput.PDF,))
        )
    assert (captured.value.code, captured.value.message) == (code, message)


def test_mermaid_engines_are_required_only_for_diagrams(pipeline) -> None:
    probe = FakeProbe()
    probe.failures["mermaid-cli"] = "unavailable"
    converter = LocalConverter(local_conversion_settings(), {}, probe=probe)
    converter.convert(request(b"# No diagram\n"))
    with pytest.raises(LocalConversionError, match="Mermaid CLI engine is missing"):
        converter.convert(request(MERMAID))


def test_archive_sources_use_the_service_archive_and_image_limits(pipeline) -> None:
    docx, _, _ = pipeline
    LocalConverter(local_conversion_settings(), {}, probe=FakeProbe()).convert(
        request(archive(b"# Packaged\n"), SourceKind.ARCHIVE)
    )
    kind, document, _ = docx.calls[0]
    assert kind == "document"
    assert document.markdown == "# Packaged\n"
    with pytest.raises(ConversionError) as captured:
        LocalConverter(
            local_conversion_settings(conversion_max_files=1), {}, probe=FakeProbe()
        ).convert(
            request(archive(b"# Packaged\n", **{"extra.md": b"x"}), SourceKind.ARCHIVE)
        )
    assert captured.value.code is ConversionErrorCode.VALIDATION


@pytest.mark.parametrize(
    ("source", "kind", "code"),
    [
        (b"", SourceKind.MARKDOWN, "source_empty"),
        (b"x" * 33, SourceKind.MARKDOWN, "source_too_large"),
    ],
)
def test_source_bounds_are_enforced(
    pipeline, source: bytes, kind: SourceKind, code: str
) -> None:
    with pytest.raises(LocalConversionError) as captured:
        LocalConverter(
            local_conversion_settings(conversion_upload_max_bytes=32),
            {},
            probe=FakeProbe(),
        ).convert(request(source, kind))
    assert captured.value.code == code


@pytest.mark.parametrize(
    ("source", "kind", "message"),
    [
        (b"# not a zip", SourceKind.ARCHIVE, "Document archive is invalid."),
        (b"PK\x03\x04rest", SourceKind.MARKDOWN, "Markdown input is invalid."),
        (b"\xff\xfe", SourceKind.MARKDOWN, "Markdown input is not valid UTF-8."),
        (b"<b>raw</b>\n", SourceKind.MARKDOWN, "Markdown input contains raw HTML."),
        (b"![x](http://example.com/a.png)\n", SourceKind.MARKDOWN, None),
    ],
)
def test_invalid_input_is_rejected_before_any_engine_runs(
    pipeline, source: bytes, kind: SourceKind, message: str | None
) -> None:
    docx, _, _ = pipeline
    with pytest.raises(ConversionError) as captured:
        LocalConverter(local_conversion_settings(), {}, probe=FakeProbe()).convert(
            request(source, kind)
        )
    assert captured.value.code is ConversionErrorCode.VALIDATION
    if message is not None:
        assert str(captured.value) == message
    assert docx.calls == []


@pytest.mark.parametrize(
    ("template", "code"),
    [
        (b"not a docx", "template_invalid_package"),
        (b"x" * 33, "template_limit_exceeded"),
    ],
)
def test_template_uses_the_service_static_validation(
    pipeline, template: bytes, code: str
) -> None:
    probe = FakeProbe()
    with pytest.raises(LocalConversionError) as captured:
        LocalConverter(
            local_conversion_settings(template_max_archive_bytes=32), {}, probe=probe
        ).convert(request(template=template))
    assert captured.value.code == code
    assert probe.calls == []


def test_powerpoint_template_is_refused(pipeline, mocker) -> None:
    validated = mocker.Mock(kind=mocker.sentinel.pptx, sha256="0" * 64)
    mocker.patch.object(local_module, "validate_template", return_value=validated)
    with pytest.raises(LocalConversionError, match="must be a Word document"):
        LocalConverter(local_conversion_settings(), {}, probe=FakeProbe()).convert(
            request(template=TEMPLATE)
        )


def test_pdf_requires_a_readable_font_manifest(pipeline, tmp_path: Path) -> None:
    for settings, code in (
        (
            local_conversion_settings(conversion_font_manifest_path=None),
            "font_manifest_required",
        ),
        (
            local_conversion_settings(
                conversion_font_manifest_path=tmp_path / "missing.json"
            ),
            "font_manifest_unavailable",
        ),
    ):
        with pytest.raises(LocalConversionError) as captured:
            LocalConverter(settings, {}, probe=FakeProbe()).convert(
                request(outputs=(LocalOutput.PDF,))
            )
        assert captured.value.code == code


def test_cancellation_and_deadline_checkpoints(pipeline, mocker) -> None:
    converter = LocalConverter(local_conversion_settings(), {}, probe=FakeProbe())
    with pytest.raises(LocalConversionError) as captured:
        converter.convert(request(), cancellation_requested=lambda: True)
    assert captured.value.code == "conversion_cancelled"

    mocker.patch.object(local_module.time, "monotonic", return_value=100.0)
    with pytest.raises(LocalConversionError) as captured:
        converter.convert(request(), deadline_monotonic=99.0)
    assert captured.value.code == "conversion_timeout"

    probes = iter((False, False, True))
    with pytest.raises(LocalConversionError, match="cancelled"):
        converter.convert(request(), cancellation_requested=lambda: next(probes, True))


def test_engine_failures_propagate_with_their_stable_codes(pipeline, mocker) -> None:
    docx, _, _ = pipeline
    mocker.patch.object(
        docx,
        "convert",
        side_effect=ConversionError(
            ConversionErrorCode.PANDOC_TIMEOUT, "Pandoc conversion timed out."
        ),
    )
    with pytest.raises(ConversionError) as captured:
        LocalConverter(local_conversion_settings(), {}, probe=FakeProbe()).convert(
            request()
        )
    assert captured.value.code is ConversionErrorCode.PANDOC_TIMEOUT


def test_default_probe_uses_the_configured_engine_bounds(mocker) -> None:
    probe = mocker.patch.object(local_module, "EngineVersionProbe")
    config = mocker.patch.object(local_module, "EngineProbeConfig")
    LocalConverter(local_conversion_settings(), {"PATH": "/bin"})
    config.assert_called_once_with(10.0, 1.0, 0.1, None)
    probe.assert_called_once_with(config.return_value, {"PATH": "/bin"})


def test_requests_require_supported_outputs() -> None:
    with pytest.raises(ValueError, match="At least one"):
        LocalConversionRequest(b"# x", SourceKind.MARKDOWN, frozenset())
