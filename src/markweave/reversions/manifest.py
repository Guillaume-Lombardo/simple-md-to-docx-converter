"""Canonical content-free reverse-conversion traceability manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.options import PPTX_EXTRACTOR

SourceFamily = Literal[
    "word", "powerpoint", "excel", "opendocument", "rtf", "epub", "csv", "pdf"
]
DetectedFormat = Literal[
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xlsx",
    "odt",
    "ods",
    "odp",
    "rtf",
    "epub",
    "csv",
    "pdf",
]
ResultMode = Literal["markdown_with_assets", "markdown_with_unavailable_assets"]

_FAMILY_FORMATS: dict[str, frozenset[str]] = {
    "word": frozenset(("doc", "docx")),
    "powerpoint": frozenset(("ppt", "pptx")),
    "excel": frozenset(("xlsx",)),
    "opendocument": frozenset(("odt", "ods", "odp")),
    "rtf": frozenset(("rtf",)),
    "epub": frozenset(("epub",)),
    "csv": frozenset(("csv",)),
    "pdf": frozenset(("pdf",)),
}


@dataclass(frozen=True)
class ManifestSource:
    """Normalized content-detected source identity."""

    family: SourceFamily
    detected_format: DetectedFormat


@dataclass(frozen=True)
class ManifestResult:
    """Content-free package counters."""

    mode: ResultMode
    asset_count: int
    asset_bytes: int
    unavailable_asset_count: int


def _validate_result(result: ManifestResult) -> None:
    values = (result.asset_count, result.asset_bytes, result.unavailable_asset_count)
    if any(type(value) is not int or value < 0 for value in values):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if result.mode == "markdown_with_assets":
        if result.asset_count < 1 or result.asset_bytes < 1:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
    elif result.mode == "markdown_with_unavailable_assets":
        if (
            result.asset_count != 0
            or result.asset_bytes != 0
            or result.unavailable_asset_count < 1
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
    else:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)


def canonical_manifest_bytes(
    source: ManifestSource,
    result: ManifestResult,
    *,
    extractor: str | None = None,
) -> bytes:
    """Return the sole canonical schema-v1 manifest serialization."""

    if source.detected_format not in _FAMILY_FORMATS.get(source.family, frozenset()):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    _validate_result(result)
    if extractor is not None and (
        extractor != PPTX_EXTRACTOR
        or source.family != "powerpoint"
        or source.detected_format != "pptx"
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    manifest = {
        "schema_version": 1,
        "engine": {"name": "firecrawl-anydoc", "version": "0.2.4"},
        "source": {
            "family": source.family,
            "detected_format": source.detected_format,
        },
        "result": {
            "mode": result.mode,
            "asset_count": result.asset_count,
            "asset_bytes": result.asset_bytes,
            "unavailable_asset_count": result.unavailable_asset_count,
        },
        "execution": {"local": True, "ocr": False, "hosted_fallback": False},
    }
    if extractor is not None:
        manifest["extractor"] = extractor
    return (
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
