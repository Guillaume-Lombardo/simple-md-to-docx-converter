"""Unit tests for the canonical content-free reverse manifest."""

from __future__ import annotations

import json
from typing import cast

import pytest

from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.manifest import (
    DetectedFormat,
    ManifestResult,
    ManifestSource,
    ResultMode,
    SourceFamily,
    canonical_manifest_bytes,
)

pytestmark = pytest.mark.unit


def test_manifest_has_one_exact_canonical_serialization() -> None:
    content = canonical_manifest_bytes(
        ManifestSource("word", "docx"),
        ManifestResult("markdown_with_assets", 2, 17, 1),
    )
    assert content == (
        b'{"schema_version":1,"engine":{"name":"firecrawl-anydoc","version":"0.2.4"},'
        b'"source":{"family":"word","detected_format":"docx"},"result":'
        b'{"mode":"markdown_with_assets","asset_count":2,"asset_bytes":17,'
        b'"unavailable_asset_count":1},"execution":{"local":true,"ocr":false,'
        b'"hosted_fallback":false}}\n'
    )
    assert json.loads(content)["execution"] == {
        "local": True,
        "ocr": False,
        "hosted_fallback": False,
    }


@pytest.mark.parametrize(
    ("family", "detected_format"),
    [("word", "pptx"), ("unknown", "docx")],
)
def test_family_and_detected_format_must_match(
    family: str, detected_format: str
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        canonical_manifest_bytes(
            ManifestSource(
                cast("SourceFamily", family),
                cast("DetectedFormat", detected_format),
            ),
            ManifestResult("markdown_with_assets", 1, 1, 0),
        )
    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize(
    "result",
    [
        ManifestResult("markdown_with_assets", 0, 1, 0),
        ManifestResult("markdown_with_assets", 1, 0, 0),
        ManifestResult("markdown_with_unavailable_assets", 1, 1, 1),
        ManifestResult("markdown_with_unavailable_assets", 0, 0, 0),
        ManifestResult(cast("ResultMode", "other"), 0, 0, 0),
        ManifestResult("markdown_with_assets", -1, 1, 0),
    ],
)
def test_result_mode_invariants_fail_closed(result: ManifestResult) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        canonical_manifest_bytes(ManifestSource("pdf", "pdf"), result)
    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR
