"""Serverless local conversion through the service's document pipeline (D1).

The operator's own local file is converted without the HTTP API, an account,
persistence, or malware scanning. Input validation, archive and image handling,
Mermaid rendering, Pandoc, and LibreOffice are the exact service adapters,
composed with the same ``MARKWEAVE_*`` limits by ``build_document_engines``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from markweave.conversion.archive import ApprovedDocument, prepare_archive
from markweave.conversion.engine_versions import (
    EngineObservation,
    EngineProbeConfig,
    EngineProbeError,
    EngineVersionProbe,
    EngineVersionStatus,
    engine_display_name,
)
from markweave.conversion.errors import validation_error
from markweave.conversion.libreoffice import PdfTraceabilityContext
from markweave.conversion.local_settings import LocalConversionSettings
from markweave.conversion.mermaid import contains_mermaid
from markweave.conversion.processor import (
    CONVERSION_CONTRACT_VERSION,
    DocumentEngines,
    build_document_engines,
)
from markweave.conversion.validation import PANDOC_READER, validate_markdown
from markweave.jobs.models import SourceKind
from markweave.templates.errors import TemplateValidationError
from markweave.templates.models import TemplateKind
from markweave.templates.runtime import template_limits
from markweave.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    validate_template,
)
from markweave.version import VERSION

LOCAL_MANIFEST_SCHEMA_VERSION = 1
_ZIP_SIGNATURES = frozenset({b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"})
_NOT_USED = "not-used"


class LocalOutput(StrEnum):
    """Document formats the local command can publish."""

    DOCX = "docx"
    PDF = "pdf"


class LocalConversionError(RuntimeError):
    """A safe local failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class LocalConversionRequest:
    """One operator-owned source, optional reference DOCX, and requested outputs."""

    source: bytes
    source_kind: SourceKind
    outputs: frozenset[LocalOutput]
    template: bytes | None = None
    require_pinned: bool = False

    def __post_init__(self) -> None:
        if not self.outputs or not self.outputs <= set(LocalOutput):
            raise ValueError("At least one supported local output is required")


@dataclass(frozen=True, slots=True)
class LocalConversionResult:
    """Validated document bytes and their content-free traceability manifest."""

    outputs: dict[LocalOutput, bytes]
    manifest: bytes
    engines: tuple[EngineObservation, ...]
    warnings: tuple[str, ...] = field(default=())

    @property
    def pinned(self) -> bool:
        return all(engine.pinned for engine in self.engines)


class EngineProbe(Protocol):
    """Port observing one required engine's version."""

    def observe(
        self,
        name: str,
        executable: str,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> EngineObservation: ...


class LocalConverter:
    """Run one local conversion with explicit deadline and cancellation probes."""

    def __init__(
        self,
        settings: LocalConversionSettings,
        environment: Mapping[str, str] | None = None,
        *,
        probe: EngineProbe | None = None,
    ) -> None:
        self._settings = settings
        self._environment = dict(os.environ if environment is None else environment)
        self._probe: EngineProbe = probe or EngineVersionProbe(
            EngineProbeConfig(
                settings.template_engine_timeout_seconds,
                settings.template_engine_termination_grace_seconds,
                settings.conversion_pdf_cancellation_poll_seconds,
                settings.template_engine_workspace_root,
            ),
            self._environment,
        )

    def convert(
        self,
        request: LocalConversionRequest,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> LocalConversionResult:
        """Validate, probe required engines, convert, and build the manifest."""

        cancelled = cancellation_requested or (lambda: False)
        self._checkpoint(deadline_monotonic, cancelled)
        template_sha256 = self._validate_template(request.template)
        pdf_requested = LocalOutput.PDF in request.outputs
        font_manifest_sha256 = self._font_manifest_sha256() if pdf_requested else ""
        engines = self._observe(
            ("pandoc", "libreoffice") if pdf_requested else ("pandoc",),
            deadline_monotonic=deadline_monotonic,
            cancelled=cancelled,
        )
        pipeline = build_document_engines(
            self._settings,
            libreoffice_version=_version(engines, "libreoffice"),
            environment=self._environment,
        )
        markdown, document = self._prepare_source(request, pipeline)
        if contains_mermaid(markdown):
            engines += self._observe(
                ("mermaid-cli", "chromium"),
                deadline_monotonic=deadline_monotonic,
                cancelled=cancelled,
            )
        warnings = tuple(
            _pin_warning(engine) for engine in engines if not engine.pinned
        )
        if request.require_pinned and warnings:
            raise LocalConversionError("engine_version_unpinned", warnings[0])
        self._checkpoint(deadline_monotonic, cancelled)
        if document is None:
            docx = pipeline.docx.convert(
                markdown,
                request.template,
                deadline_monotonic=deadline_monotonic,
                cancellation_requested=cancelled,
            )
        else:
            docx = pipeline.docx.convert_document(
                document,
                request.template,
                deadline_monotonic=deadline_monotonic,
                cancellation_requested=cancelled,
            )
        self._checkpoint(deadline_monotonic, cancelled)
        outputs: dict[LocalOutput, bytes] = {}
        if LocalOutput.DOCX in request.outputs:
            outputs[LocalOutput.DOCX] = docx
        pdf_manifest: dict[str, Any] | None = None
        if pdf_requested:
            artifact = pipeline.pdf.convert(
                docx,
                PdfTraceabilityContext(
                    application_version=VERSION,
                    conversion_contract_version=CONVERSION_CONTRACT_VERSION,
                    template_id=None,
                    template_version=None,
                    template_sha256=template_sha256,
                    pandoc_version=_version(engines, "pandoc"),
                    pandoc_reader=PANDOC_READER,
                    mermaid_version=_version(engines, "mermaid-cli"),
                    chromium_version=_version(engines, "chromium"),
                    font_manifest_sha256=font_manifest_sha256,
                    template_mode=(
                        "local-file"
                        if template_sha256 is not None
                        else "pandoc-default"
                    ),
                ),
                cancelled,
                deadline_monotonic=deadline_monotonic,
            )
            self._checkpoint(deadline_monotonic, cancelled)
            outputs[LocalOutput.PDF] = artifact.pdf
            pdf_manifest = json.loads(artifact.manifest.canonical_json())
        manifest = _manifest(
            request,
            template_sha256=template_sha256,
            engines=engines,
            outputs=outputs,
            pdf_manifest=pdf_manifest,
        )
        return LocalConversionResult(outputs, manifest, engines, warnings)

    def _validate_template(self, template: bytes | None) -> str | None:
        if template is None:
            return None
        try:
            validated = validate_template(
                template,
                TemplateFontDeclaration(()),
                template_limits(self._settings),
                APPROVED_FONT_POLICY,
            )
        except TemplateValidationError as error:
            raise LocalConversionError(
                f"template_{error.code.value}",
                f"The Word template is invalid ({error.code.value.replace('_', ' ')}).",
            ) from None
        if validated.kind is not TemplateKind.DOCX:
            raise LocalConversionError(
                "template_invalid_package", "The template must be a Word document."
            )
        return validated.sha256

    def _prepare_source(
        self, request: LocalConversionRequest, pipeline: DocumentEngines
    ) -> tuple[str, ApprovedDocument | None]:
        source = request.source
        if not source:
            raise LocalConversionError(
                "source_empty", "The conversion source is empty."
            )
        if len(source) > self._settings.conversion_upload_max_bytes:
            raise LocalConversionError(
                "source_too_large",
                "The conversion source exceeds the configured upload limit.",
            )
        is_zip = source[:4] in _ZIP_SIGNATURES
        if request.source_kind is SourceKind.ARCHIVE:
            if not is_zip:
                raise validation_error("Document archive is invalid.")
            document = prepare_archive(
                source, pipeline.archive_limits, pipeline.image_limits
            )
            return document.markdown, document
        if is_zip:
            raise validation_error("Markdown input is invalid.")
        try:
            markdown = source.decode("utf-8")
        except UnicodeDecodeError:
            raise validation_error("Markdown input is not valid UTF-8.") from None
        validate_markdown(markdown)
        return markdown, None

    def _font_manifest_sha256(self) -> str:
        path = self._settings.conversion_font_manifest_path
        if path is None:
            raise LocalConversionError(
                "font_manifest_required",
                "PDF output requires MARKWEAVE_CONVERSION_FONT_MANIFEST_PATH.",
            )
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise LocalConversionError(
                "font_manifest_unavailable",
                "The conversion font manifest is unavailable.",
            ) from None

    def _observe(
        self,
        required: tuple[str, ...],
        *,
        deadline_monotonic: float | None,
        cancelled: Callable[[], bool],
    ) -> tuple[EngineObservation, ...]:
        settings = self._settings
        executables = {
            "pandoc": settings.template_pandoc_executable,
            "libreoffice": settings.template_libreoffice_executable,
            "mermaid-cli": settings.conversion_mermaid_executable,
            "chromium": settings.conversion_chromium_executable,
        }
        observations: list[EngineObservation] = []
        for name in required:
            try:
                observations.append(
                    self._probe.observe(
                        name,
                        executables[name],
                        deadline_monotonic=deadline_monotonic,
                        cancellation_requested=cancelled,
                    )
                )
            except EngineProbeError as error:
                raise _probe_error(error) from None
        return tuple(observations)

    @staticmethod
    def _checkpoint(
        deadline_monotonic: float | None, cancelled: Callable[[], bool]
    ) -> None:
        if cancelled():
            raise LocalConversionError(
                "conversion_cancelled", "The local conversion was cancelled."
            )
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise LocalConversionError(
                "conversion_timeout", "The local conversion timed out."
            )


def _version(engines: tuple[EngineObservation, ...], name: str) -> str:
    """Return safe traceability metadata for one engine, used or not."""

    for engine in engines:
        if engine.name == name:
            return engine.actual_version or "unknown"
    return _NOT_USED


def _probe_error(error: EngineProbeError) -> LocalConversionError:
    display = engine_display_name(error.name)
    if error.reason == "cancelled":
        return LocalConversionError(
            "conversion_cancelled", "The local conversion was cancelled."
        )
    if error.reason == "timeout":
        return LocalConversionError(
            "engine_probe_timeout", f"The {display} version check timed out."
        )
    return LocalConversionError(
        "engine_missing",
        f"The required {display} engine is missing or cannot run.",
    )


def _pin_warning(engine: EngineObservation) -> str:
    display = engine_display_name(engine.name)
    if engine.status is EngineVersionStatus.UNKNOWN:
        return (
            f"The {display} version could not be determined; the approved pin is "
            f"{engine.approved_version}."
        )
    return (
        f"{display} {engine.actual_version} differs from the approved pin "
        f"{engine.approved_version}."
    )


def _manifest(
    request: LocalConversionRequest,
    *,
    template_sha256: str | None,
    engines: tuple[EngineObservation, ...],
    outputs: dict[LocalOutput, bytes],
    pdf_manifest: dict[str, Any] | None,
) -> bytes:
    """Serialize content-free reproduction metadata without paths or timestamps."""

    payload: dict[str, Any] = {
        "schema_version": LOCAL_MANIFEST_SCHEMA_VERSION,
        "mode": "local",
        "application_version": VERSION,
        "conversion_contract_version": CONVERSION_CONTRACT_VERSION,
        "pandoc_reader": PANDOC_READER,
        "source": {
            "kind": request.source_kind.value,
            "bytes": len(request.source),
            "sha256": hashlib.sha256(request.source).hexdigest(),
        },
        "template": (
            {"mode": "local-file", "sha256": template_sha256}
            if template_sha256 is not None
            else {"mode": "pandoc-default"}
        ),
        "engines": [engine.as_manifest() for engine in engines],
        "engines_pinned": all(engine.pinned for engine in engines),
        "require_pinned": request.require_pinned,
        "outputs": [
            {
                "format": output.value,
                "bytes": len(outputs[output]),
                "sha256": hashlib.sha256(outputs[output]).hexdigest(),
            }
            for output in LocalOutput
            if output in outputs
        ],
        "pdf": pdf_manifest,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
