"""Fenced result and traceability-manifest publication."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from markweave.jobs.models import (
    SHA256_CHARACTERS,
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobState,
    JobStep,
    result_manifest_object_id,
    result_object_id,
)
from markweave.jobs.ports import JobRepository
from markweave.jobs.worker_execution import JobHeartbeatService
from markweave.storage import ObjectKey, ObjectScope, ObjectStore

_TRACEABILITY_SCHEMA_V1 = 1
_TRACEABILITY_SCHEMA_V2 = 2
_TRACEABILITY_V1_KEYS = frozenset(
    {
        "schema_version",
        "application_version",
        "conversion_contract_version",
        "template_id",
        "template_version",
        "template_sha256",
        "source_docx_sha256",
        "output_pdf_sha256",
        "output_pdf_bytes",
        "pages",
        "pandoc_version",
        "pandoc_reader",
        "mermaid_version",
        "chromium_version",
        "libreoffice_version",
        "font_manifest_sha256",
        "export_filter",
        "output_format",
    }
)
_TRACEABILITY_V2_KEYS = _TRACEABILITY_V1_KEYS | {"template_mode"}


def _reject_json_constant(_value: str) -> None:
    raise ValueError


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_CHARACTERS
        and all(character in "0123456789abcdef" for character in value)
    )


def _has_valid_template_traceability(
    decoded: dict[str, object], job: ConversionJob, result: JobProcessResult
) -> bool:
    schema_version = decoded.get("schema_version")
    template_id = decoded.get("template_id")
    template_version = decoded.get("template_version")
    template_sha256 = decoded.get("template_sha256")
    if schema_version == _TRACEABILITY_SCHEMA_V1:
        return (
            job.template_id is not None
            and template_id == str(job.template_id)
            and template_version == result.template_version
            and template_sha256 == result.template_sha256
            and _is_sha256(result.template_sha256)
        )
    if schema_version != _TRACEABILITY_SCHEMA_V2:
        return False
    template_mode = decoded.get("template_mode")
    if template_mode == "pandoc-default":
        return job.template_id is None and all(
            value is None
            for value in (
                template_id,
                template_version,
                template_sha256,
                result.template_version,
                result.template_sha256,
            )
        )
    return (
        template_mode == "versioned"
        and job.template_id is not None
        and template_id == str(job.template_id)
        and template_version == result.template_version
        and template_sha256 == result.template_sha256
        and _is_sha256(result.template_sha256)
    )


def _is_canonical_traceability_manifest(  # noqa: PLR0911
    content: bytes, job: ConversionJob, result: JobProcessResult
) -> bool:
    try:
        decoded = json.loads(content, parse_constant=_reject_json_constant)
    except json.JSONDecodeError, UnicodeDecodeError, ValueError:
        return False
    if not isinstance(decoded, dict):
        return False
    schema_version = decoded.get("schema_version")
    if type(schema_version) is not int:
        return False
    expected_keys = {
        _TRACEABILITY_SCHEMA_V1: _TRACEABILITY_V1_KEYS,
        _TRACEABILITY_SCHEMA_V2: _TRACEABILITY_V2_KEYS,
    }.get(schema_version)
    if expected_keys is None or frozenset(decoded) != expected_keys:
        return False
    if (
        decoded.get("output_format") != "pdf"
        or type(decoded.get("output_pdf_bytes")) is not int
        or decoded["output_pdf_bytes"] <= 0
        or not _has_valid_template_traceability(decoded, job, result)
        or not all(
            _is_sha256(decoded.get(name))
            for name in (
                "source_docx_sha256",
                "output_pdf_sha256",
                "font_manifest_sha256",
            )
        )
    ):
        return False
    pages = decoded.get("pages")
    if not isinstance(pages, list) or not pages:
        return False
    for page in pages:
        if not isinstance(page, dict) or set(page) != {"width_points", "height_points"}:
            return False
        if any(
            type(page.get(name)) not in {int, float} or page[name] <= 0
            for name in ("width_points", "height_points")
        ):
            return False
    return (
        json.dumps(
            decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        == content
    )


def _validated_manifest(job: ConversionJob, result: JobProcessResult) -> bytes | None:
    manifest = result.progress_manifest
    requires_manifest = job.output in {JobOutput.PDF, JobOutput.BOTH}
    if requires_manifest:
        if manifest is None or not _is_canonical_traceability_manifest(
            manifest, job, result
        ):
            raise RuntimeError(
                "PDF conversion processor returned no canonical traceability manifest"
            )
    elif manifest is not None:
        raise RuntimeError("DOCX conversion processor returned a traceability manifest")
    return manifest


@dataclass(frozen=True, slots=True)
class PublishedJob:
    """Effective state returned by the fenced terminal transition."""

    state: JobState
    step: JobStep


@dataclass(frozen=True, slots=True)
class JobPublicationService:
    """Publish output bytes and compensate every uncommitted object."""

    repository: JobRepository
    objects: ObjectStore
    worker_id: str
    clock: Callable[[], datetime]
    result_retention_seconds: float

    def publish(
        self,
        job: ConversionJob,
        lease_token: UUID,
        result: JobProcessResult,
        heartbeat: JobHeartbeatService,
    ) -> PublishedJob:
        publication_id = result_object_id(job.id, job.attempt)
        result_key = ObjectKey(ObjectScope.RESULT, job.owner_id, publication_id)
        manifest = _validated_manifest(job, result)
        manifest_id = (
            result_manifest_object_id(job.id, job.attempt)
            if manifest is not None
            else None
        )
        manifest_key = (
            ObjectKey(ObjectScope.RESULT_MANIFEST, job.owner_id, manifest_id)
            if manifest_id is not None
            else None
        )
        try:
            self.objects.put(result_key, result.content)
            if manifest_key is not None and manifest is not None:
                self.objects.put(manifest_key, manifest)
            with heartbeat.guarded():
                finished_at = self.clock()
                finished = self.repository.succeed(
                    job.id,
                    self.worker_id,
                    lease_token,
                    publication_id,
                    finished_at,
                    finished_at + timedelta(seconds=self.result_retention_seconds),
                    result_manifest_object_id=manifest_id,
                )
                heartbeat.request_stop()
        except BaseException:
            self.objects.delete(result_key)
            if manifest_key is not None:
                self.objects.delete(manifest_key)
            raise
        state = (
            finished.state
            if isinstance(finished, ConversionJob)
            else JobState.SUCCEEDED
        )
        step = (
            finished.step if isinstance(finished, ConversionJob) else JobStep.COMPLETE
        )
        if state is JobState.CANCELLED:
            self.objects.delete(result_key)
            if manifest_key is not None:
                self.objects.delete(manifest_key)
        return PublishedJob(state, step)
