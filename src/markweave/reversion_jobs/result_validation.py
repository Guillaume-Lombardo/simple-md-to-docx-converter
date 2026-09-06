"""Fail-closed validation for unpublished reverse-attempt results."""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, cast

from markweave.conversion.errors import ConversionError
from markweave.conversion.images import validate_normalized_png
from markweave.reversion_jobs.models import (
    ANYDOC_COMPONENT,
    ReversionJob,
    ReversionTraceMetadata,
)
from markweave.reversions.assets import NormalizedAsset
from markweave.reversions.errors import (
    ReverseConversionError,
    ReverseErrorCategory,
    reject,
)
from markweave.reversions.manifest import (
    DetectedFormat,
    ManifestResult,
    ManifestSource,
    SourceFamily,
    canonical_manifest_bytes,
)
from markweave.reversions.models import ReverseContentLimits, ReverseOutputMode
from markweave.reversions.package import build_reverse_package

_ASSET_PATH = re.compile(r"^assets/image-(\d{4,})\.png$")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_CANONICAL_EXTERNAL_ATTRIBUTES = 0o100644 << 16
_CANONICAL_CREATE_SYSTEM = 3
_MINIMUM_ZIP_ENTRIES = 2
_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_ZIP_MEDIA_TYPE = "application/zip"


@dataclass(frozen=True, slots=True)
class ValidatedReverseResult:
    """A bounded canonical result and its publication metadata."""

    content: bytes
    media_type: str
    extension: str
    sha256: str
    size: int
    trace: ReversionTraceMetadata


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate manifest key")
        value[key] = item
    return value


def _mapping(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("Manifest object is invalid")
    return cast(dict[str, object], value)


def _asset_ordinal(path: str) -> int:
    match = cast(re.Match[str], _ASSET_PATH.fullmatch(path))
    return int(match[1])


def _zip_metadata(info: zipfile.ZipInfo) -> tuple[object, ...]:
    return (
        info.date_time,
        info.compress_type,
        info.create_system,
        info.external_attr,
        info.flag_bits,
        info.extra,
        info.comment,
        info.is_dir(),
        info.file_size == info.compress_size,
    )


def _trace_from_manifest(
    job: ReversionJob,
    mode: ReverseOutputMode,
    manifest_bytes: bytes,
) -> tuple[ReversionTraceMetadata, int]:
    try:
        decoded = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
        manifest = _mapping(
            decoded, {"schema_version", "engine", "source", "result", "execution"}
        )
        engine = _mapping(manifest["engine"], {"name", "version"})
        source = _mapping(manifest["source"], {"family", "detected_format"})
        result = _mapping(
            manifest["result"],
            {"mode", "asset_count", "asset_bytes", "unavailable_asset_count"},
        )
        execution = _mapping(manifest["execution"], {"local", "ocr", "hosted_fallback"})
        identity = (
            manifest["schema_version"],
            engine,
            source,
            result["mode"],
            execution,
        )
        expected_identity = (
            1,
            {"name": ANYDOC_COMPONENT[0], "version": ANYDOC_COMPONENT[1]},
            {
                "family": job.admission.family.value,
                "detected_format": job.admission.parser_format,
            },
            mode.value,
            {"local": True, "ocr": False, "hosted_fallback": False},
        )
        if identity != expected_identity:
            raise ValueError("Manifest identity is invalid")
        trace = ReversionTraceMetadata(
            schema_version=cast(int, manifest["schema_version"]),
            engine_name=cast(str, engine["name"]),
            engine_version=cast(str, engine["version"]),
            source_family=job.admission.family,
            detected_format=cast(str, source["detected_format"]),
            result_mode=mode,
            asset_count=cast(int, result["asset_count"]),
            asset_bytes=cast(int, result["asset_bytes"]),
            unavailable_asset_count=cast(int, result["unavailable_asset_count"]),
        )
        canonical = canonical_manifest_bytes(
            ManifestSource(
                cast(SourceFamily, source["family"]),
                cast(DetectedFormat, source["detected_format"]),
            ),
            ManifestResult(
                cast(Any, result["mode"]),
                trace.asset_count,
                trace.asset_bytes,
                trace.unavailable_asset_count,
            ),
        )
    except KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if canonical != manifest_bytes:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return trace, trace.unavailable_asset_count


def _markdown_trace(job: ReversionJob) -> ReversionTraceMetadata:
    return ReversionTraceMetadata(
        schema_version=1,
        engine_name=ANYDOC_COMPONENT[0],
        engine_version=ANYDOC_COMPONENT[1],
        source_family=job.admission.family,
        detected_format=job.admission.parser_format,
        result_mode=ReverseOutputMode.MARKDOWN,
        asset_count=0,
        asset_bytes=0,
        unavailable_asset_count=0,
    )


def _decode_markdown(content: bytes, maximum: int) -> str:
    if len(content) > maximum:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    try:
        markdown = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if "\x00" in markdown:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return markdown


def _zip_result(  # noqa: PLR0912 - closed archive validation boundary
    job: ReversionJob,
    mode: ReverseOutputMode,
    content: bytes,
    limits: ReverseContentLimits,
) -> tuple[ReversionTraceMetadata, str, str]:
    if len(content) > limits.max_package_bytes:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if archive.comment:
                raise ValueError("ZIP comment is invalid")
            infos = archive.infolist()
            if (
                not _MINIMUM_ZIP_ENTRIES
                <= len(infos)
                <= (limits.max_asset_count + _MINIMUM_ZIP_ENTRIES)
            ):
                raise ValueError("ZIP entry count is invalid")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("ZIP entries are duplicated")
            asset_names = names[1:-1]
            if any(_ASSET_PATH.fullmatch(name) is None for name in asset_names):
                raise ValueError("ZIP asset path is invalid")
            expected_names = [
                "document.md",
                *sorted(asset_names, key=_asset_ordinal),
                "manifest.json",
            ]
            if names != expected_names:
                raise ValueError("ZIP layout is invalid")
            expected_metadata = (
                _ZIP_TIMESTAMP,
                zipfile.ZIP_STORED,
                _CANONICAL_CREATE_SYSTEM,
                _CANONICAL_EXTERNAL_ATTRIBUTES,
                0,
                b"",
                b"",
                False,
                True,
            )
            if any(_zip_metadata(info) != expected_metadata for info in infos):
                raise ValueError("ZIP metadata is not canonical")
            if infos[0].file_size > limits.max_markdown_bytes:
                reject(ReverseErrorCategory.RESOURCE_LIMIT)
            asset_size = sum(info.file_size for info in infos[1:-1])
            if asset_size > limits.max_total_asset_output_bytes:
                reject(ReverseErrorCategory.RESOURCE_LIMIT)
            markdown = _decode_markdown(
                archive.read("document.md"), limits.max_markdown_bytes
            )
            assets = tuple(
                NormalizedAsset(PurePosixPath(name), archive.read(name))
                for name in asset_names
            )
            manifest_bytes = archive.read("manifest.json")
    except ReverseConversionError:
        raise
    except zipfile.BadZipFile, OSError, RuntimeError, ValueError, KeyError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    for asset in assets:
        try:
            validate_normalized_png(asset.content, limits.image_limits)
        except ConversionError:
            reject(ReverseErrorCategory.ASSET_INVALID)
    trace, unavailable_count = _trace_from_manifest(job, mode, manifest_bytes)
    if trace.asset_count != len(assets) or trace.asset_bytes != asset_size:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    rebuilt = build_reverse_package(
        markdown,
        assets,
        tuple(asset.path for asset in assets) + (None,) * unavailable_count,
        unavailable_asset_count=unavailable_count,
        source=ManifestSource(
            job.admission.family.value,
            cast(DetectedFormat, job.admission.parser_format),
        ),
        limits=limits.package_limits,
    )
    if rebuilt.content != content:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return trace, rebuilt.media_type, rebuilt.extension


def validate_reverse_result(
    job: ReversionJob,
    mode: ReverseOutputMode,
    content: bytes,
    limits: ReverseContentLimits,
) -> ValidatedReverseResult:
    """Validate one child result before storage or publication."""

    if (
        type(job) is not ReversionJob
        or type(mode) is not ReverseOutputMode
        or type(content) is not bytes
        or not content
        or type(limits) is not ReverseContentLimits
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if len(content) > limits.max_output_bytes:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    if mode is ReverseOutputMode.MARKDOWN:
        _decode_markdown(content, limits.max_markdown_bytes)
        trace = _markdown_trace(job)
        media_type = _MARKDOWN_MEDIA_TYPE
        extension = ".md"
    else:
        trace, media_type, extension = _zip_result(job, mode, content, limits)
    return ValidatedReverseResult(
        content,
        media_type,
        extension,
        sha256(content).hexdigest(),
        len(content),
        trace,
    )
