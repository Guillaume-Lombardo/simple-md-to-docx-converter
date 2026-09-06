"""Unit coverage for fail-closed reverse-result validation."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID

import pytest
from PIL import Image

from markweave.reversion_jobs.models import (
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
)
from markweave.reversion_jobs.result_validation import validate_reverse_result
from markweave.reversions.assets import AssetSource, NormalizedAsset, normalize_assets
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.reversions.manifest import ManifestSource
from markweave.reversions.models import ReverseContentLimits, ReverseOutputMode
from markweave.reversions.package import build_reverse_package

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 6, tzinfo=UTC)
LIMITS = ReverseContentLimits(
    max_input_bytes=10_000,
    max_output_bytes=30_000,
    max_image_source_bytes=10_000,
    max_image_width_pixels=100,
    max_image_height_pixels=100,
    max_image_pixels=10_000,
    max_svg_elements=100,
    max_svg_depth=16,
    max_asset_count=8,
    max_total_asset_source_bytes=20_000,
    max_total_asset_output_bytes=20_000,
    max_markdown_bytes=10_000,
    max_package_bytes=30_000,
)
WORD_SOURCE = ManifestSource("word", "docx")


def _job(admission: FormatAdmission | None = None) -> ReversionJob:
    return ReversionJob(
        id=UUID("10000000-0000-4000-8000-000000000001"),
        owner_id=UUID("20000000-0000-4000-8000-000000000001"),
        source_object_id=UUID("30000000-0000-4000-8000-000000000001"),
        source_stem="source",
        admission=admission
        or FormatAdmission(FormatFamily.WORD, ".docx", "docx", "docx"),
        source_sha256="a" * 64,
        source_size=100,
        component_versions=(("firecrawl-anydoc", "0.2.4"),),
        request_digest="b" * 64,
        idempotency_digest=None,
        correlation_id="12345678-1234-4234-8234-123456789abc",
        state=ReversionJobState.QUEUED,
        step=ReversionJobStep.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), "#123456").save(output, format="PNG")
    normalized = normalize_assets(
        (AssetSource("image", output.getvalue(), "image/png"),),
        LIMITS.asset_limits,
    )
    return normalized.assets[0].content


def _package(
    *,
    assets: tuple[NormalizedAsset, ...] = (),
    unavailable: int = 0,
    source: ManifestSource = WORD_SOURCE,
) -> bytes:
    references = tuple(asset.path for asset in assets) + (None,) * unavailable
    markdown = "".join(f"![]({asset.path.as_posix()})\n" for asset in assets)
    if unavailable:
        markdown += "Unavailable image\n"
    return build_reverse_package(
        markdown,
        assets,
        references,
        unavailable_asset_count=unavailable,
        source=source,
        limits=LIMITS.package_limits,
    ).content


def _rewrite_zip(  # noqa: PLR0913 - adversarial ZIP fixture controls
    content: bytes,
    *,
    compression: int = zipfile.ZIP_STORED,
    mutate_manifest: object | None = None,
    manifest_bytes: bytes | None = None,
    timestamp: tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0),
    archive_comment: bytes = b"",
    names: tuple[str, ...] | None = None,
) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        entries = [
            (info.filename, source.read(info.filename)) for info in source.infolist()
        ]
    if names is not None:
        entries = [(name, entries[index][1]) for index, name in enumerate(names)]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        archive.comment = archive_comment
        for name, value in entries:
            payload = value
            if name == "manifest.json":
                if mutate_manifest is not None:
                    payload = (
                        json.dumps(mutate_manifest, separators=(",", ":")) + "\n"
                    ).encode()
                elif manifest_bytes is not None:
                    payload = manifest_bytes
            info = zipfile.ZipInfo(name, date_time=timestamp)
            info.compress_type = compression
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _category(callable_result: Callable[[], object]) -> ReverseErrorCategory:
    with pytest.raises(ReverseConversionError) as caught:
        callable_result()
    return caught.value.category


def test_validates_plain_markdown_and_publication_metadata() -> None:
    content = "# Café\n".encode()

    result = validate_reverse_result(
        _job(), ReverseOutputMode.MARKDOWN, content, LIMITS
    )

    assert result.content == content
    assert result.media_type == "text/markdown; charset=utf-8"
    assert result.extension == ".md"
    assert result.size == len(content)
    assert result.trace.detected_format == "docx"
    assert result.trace.result_mode is ReverseOutputMode.MARKDOWN


def test_validates_asset_package_and_manifest_counters() -> None:
    asset = NormalizedAsset(PurePosixPath("assets/image-0001.png"), _png())
    content = _package(assets=(asset,), unavailable=1)

    result = validate_reverse_result(
        _job(), ReverseOutputMode.MARKDOWN_WITH_ASSETS, content, LIMITS
    )

    assert result.media_type == "application/zip"
    assert result.extension == ".zip"
    assert result.trace.asset_count == 1
    assert result.trace.asset_bytes == len(asset.content)
    assert result.trace.unavailable_asset_count == 1


def test_validates_unavailable_only_package() -> None:
    result = validate_reverse_result(
        _job(),
        ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
        _package(unavailable=2),
        LIMITS,
    )

    assert result.trace.asset_count == 0
    assert result.trace.unavailable_asset_count == 2


def test_csv_trace_records_selected_parser() -> None:
    job = _job(FormatAdmission(FormatFamily.CSV, ".csv", None, "csv"))

    result = validate_reverse_result(
        job, ReverseOutputMode.MARKDOWN, b"a | b\n", LIMITS
    )

    assert result.trace.source_family is FormatFamily.CSV
    assert result.trace.detected_format == "csv"


@pytest.mark.parametrize(
    ("mode", "content"),
    [
        (ReverseOutputMode.MARKDOWN_WITH_ASSETS, b"plain"),
        (ReverseOutputMode.MARKDOWN, b"\xff"),
        (ReverseOutputMode.MARKDOWN, b"nul\x00byte"),
    ],
)
def test_rejects_mode_or_markdown_protocol_mismatch(mode, content) -> None:
    assert (
        _category(lambda: validate_reverse_result(_job(), mode, content, LIMITS))
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_manifest_source_that_differs_from_admission() -> None:
    content = _package(unavailable=1, source=ManifestSource("pdf", "pdf"))

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                content,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_noncanonical_zip_compression() -> None:
    content = _rewrite_zip(_package(unavailable=1), compression=zipfile.ZIP_DEFLATED)

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                content,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


@pytest.mark.parametrize(
    "content",
    [
        b"not a zip",
        _rewrite_zip(_package(unavailable=1), archive_comment=b"comment"),
        _rewrite_zip(_package(unavailable=1), timestamp=(1981, 1, 1, 0, 0, 0)),
        _rewrite_zip(_package(unavailable=1), names=("renamed.md", "manifest.json")),
        _rewrite_zip(_package(unavailable=1), names=("document.md",)),
    ],
)
def test_rejects_malformed_or_noncanonical_zip(content: bytes) -> None:
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                content,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_duplicate_zip_entries() -> None:
    content = _package(unavailable=1)
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicated = _rewrite_zip(
            content,
            names=("document.md", "document.md"),
        )

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                duplicated,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_false_png_asset() -> None:
    content = _package(
        assets=(NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"not a png"),)
    )

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN_WITH_ASSETS, content, LIMITS
            )
        )
        is ReverseErrorCategory.ASSET_INVALID
    )


def test_rejects_extended_or_inconsistent_manifest() -> None:
    content = _package(unavailable=1)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["unexpected"] = True
    extended = _rewrite_zip(content, mutate_manifest=manifest)

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                extended,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_noncanonical_manifest_bytes_and_asset_counters() -> None:
    asset = NormalizedAsset(PurePosixPath("assets/image-0001.png"), _png())
    content = _package(assets=(asset,))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    pretty = _rewrite_zip(
        content, manifest_bytes=json.dumps(manifest, indent=2).encode()
    )
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN_WITH_ASSETS, pretty, LIMITS
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )

    manifest["result"]["asset_count"] = 2
    wrong_count = _rewrite_zip(content, mutate_manifest=manifest)
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN_WITH_ASSETS, wrong_count, LIMITS
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_zip_bytes_outside_the_canonical_archive() -> None:
    content = _package(unavailable=1) + b"trailing"

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                content,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_output_and_markdown_limits() -> None:
    constrained = replace(
        LIMITS,
        max_input_bytes=4,
        max_output_bytes=4,
        max_markdown_bytes=4,
        max_package_bytes=4,
        max_total_asset_output_bytes=4,
    )

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN, b"12345", constrained
            )
        )
        is ReverseErrorCategory.RESOURCE_LIMIT
    )

    packaged = _package(unavailable=1)
    package_constrained = replace(
        LIMITS,
        max_package_bytes=len(packaged) - 1,
        max_markdown_bytes=100,
        max_total_asset_output_bytes=100,
    )
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                packaged,
                package_constrained,
            )
        )
        is ReverseErrorCategory.RESOURCE_LIMIT
    )


def test_rejects_archive_entry_limits() -> None:
    markdown = "12345"
    unavailable = build_reverse_package(
        markdown,
        (),
        (None,),
        unavailable_asset_count=1,
        source=WORD_SOURCE,
        limits=LIMITS.package_limits,
    ).content
    markdown_constrained = replace(LIMITS, max_markdown_bytes=4)
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                unavailable,
                markdown_constrained,
            )
        )
        is ReverseErrorCategory.RESOURCE_LIMIT
    )

    asset = NormalizedAsset(PurePosixPath("assets/image-0001.png"), _png())
    packaged = _package(assets=(asset,))
    asset_constrained = replace(
        LIMITS, max_total_asset_output_bytes=len(asset.content) - 1
    )
    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_ASSETS,
                packaged,
                asset_constrained,
            )
        )
        is ReverseErrorCategory.RESOURCE_LIMIT
    )


def test_rejects_duplicate_manifest_keys() -> None:
    content = _package(unavailable=1)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = archive.read("manifest.json")
    duplicated = manifest.replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1'
    )
    malformed = _rewrite_zip(content, manifest_bytes=duplicated)

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(),
                ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS,
                malformed,
                LIMITS,
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


def test_rejects_markdown_larger_than_its_specific_limit() -> None:
    constrained = replace(LIMITS, max_markdown_bytes=4)

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN, b"12345", constrained
            )
        )
        is ReverseErrorCategory.RESOURCE_LIMIT
    )


def test_rejects_noncanonical_asset_path_before_archive_layout() -> None:
    asset = NormalizedAsset(PurePosixPath("assets/image-0001.png"), _png())
    malformed = _rewrite_zip(
        _package(assets=(asset,)),
        names=("document.md", "other/image.png", "manifest.json"),
    )

    assert (
        _category(
            lambda: validate_reverse_result(
                _job(), ReverseOutputMode.MARKDOWN_WITH_ASSETS, malformed, LIMITS
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )


@pytest.mark.parametrize(
    ("job", "mode", "content", "limits"),
    [
        (None, ReverseOutputMode.MARKDOWN, b"content", LIMITS),
        (_job(), "markdown", b"content", LIMITS),
        (_job(), ReverseOutputMode.MARKDOWN, bytearray(b"content"), LIMITS),
        (_job(), ReverseOutputMode.MARKDOWN, b"", LIMITS),
        (_job(), ReverseOutputMode.MARKDOWN, b"content", None),
    ],
)
def test_rejects_invalid_validation_boundary_arguments(
    job: object, mode: object, content: object, limits: object
) -> None:
    assert (
        _category(
            lambda: validate_reverse_result(
                cast(Any, job),
                cast(Any, mode),
                cast(Any, content),
                cast(Any, limits),
            )
        )
        is ReverseErrorCategory.PROTOCOL_ERROR
    )
