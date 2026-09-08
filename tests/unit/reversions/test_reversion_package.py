"""Unit tests for deterministic reverse-conversion result packages."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import PurePosixPath

import pytest

from markweave.reversions.assets import NormalizedAsset
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.manifest import ManifestSource
from markweave.reversions.package import PackageLimits, build_reverse_package

pytestmark = pytest.mark.unit

LIMITS = PackageLimits(10_000, 20_000)


def test_asset_free_result_is_plain_utf8_markdown() -> None:
    result = build_reverse_package(
        "# Café\n",
        (),
        (),
        unavailable_asset_count=0,
        source=ManifestSource("word", "docx"),
        limits=LIMITS,
    )
    assert result.content == "# Café\n".encode()
    assert result.media_type == "text/markdown; charset=utf-8"
    assert result.extension == ".md"


def test_zip_is_byte_deterministic_closed_and_canonically_ordered() -> None:
    assets = (
        NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"first"),
        NormalizedAsset(PurePosixPath("assets/image-0003.png"), b"third"),
    )
    arguments = {
        "markdown": "![](assets/image-0001.png)\n![](assets/image-0003.png)\n",
        "assets": assets,
        "asset_references": (
            assets[0].path,
            assets[1].path,
            None,
        ),
        "unavailable_asset_count": 1,
        "source": ManifestSource("opendocument", "odt"),
        "limits": LIMITS,
    }
    first = build_reverse_package(**arguments)
    second = build_reverse_package(**arguments)
    assert first == second
    assert first.media_type == "application/zip"
    assert first.extension == ".zip"

    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert archive.namelist() == [
            "document.md",
            "assets/image-0001.png",
            "assets/image-0003.png",
            "manifest.json",
        ]
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )
        assert all(
            info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()
        )
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["result"] == {
            "mode": "markdown_with_assets",
            "asset_count": 2,
            "asset_bytes": 10,
            "unavailable_asset_count": 1,
        }


def test_all_unavailable_assets_still_emit_the_closed_zip() -> None:
    result = build_reverse_package(
        "Unavailable image\n",
        (),
        (None, None),
        unavailable_asset_count=2,
        source=ManifestSource("pdf", "pdf"),
        limits=LIMITS,
    )
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert archive.namelist() == ["document.md", "manifest.json"]
        assert json.loads(archive.read("manifest.json"))["result"] == {
            "mode": "markdown_with_unavailable_assets",
            "asset_count": 0,
            "asset_bytes": 0,
            "unavailable_asset_count": 2,
        }


def test_escaped_alt_text_does_not_hide_a_valid_asset_link() -> None:
    result = build_reverse_package(
        r"![a \] bracket](assets/image-0001.png)" "\n",
        (NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"image"),),
        (PurePosixPath("assets/image-0001.png"),),
        unavailable_asset_count=0,
        source=ManifestSource("epub", "epub"),
        limits=LIMITS,
    )
    assert result.extension == ".zip"


def test_asset_like_markdown_inside_code_is_preserved_without_assets() -> None:
    result = build_reverse_package(
        "```markdown\n![](assets/example.png)\n```\n",
        (),
        (),
        unavailable_asset_count=0,
        source=ManifestSource("word", "docx"),
        limits=LIMITS,
    )

    assert result.extension == ".md"
    assert b"assets/example.png" in result.content


def test_structured_asset_reference_without_archive_entry_fails_closed() -> None:
    with pytest.raises(ReverseConversionError) as captured:
        build_reverse_package(
            "![](assets/image-0001.png)\n",
            (),
            (PurePosixPath("assets/image-0001.png"),),
            unavailable_asset_count=0,
            source=ManifestSource("word", "docx"),
            limits=LIMITS,
        )

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


def test_accepts_asset_ordinals_beyond_four_digits() -> None:
    result = build_reverse_package(
        "![](assets/image-9999.png)\n![](assets/image-10000.png)\n",
        (
            NormalizedAsset(PurePosixPath("assets/image-9999.png"), b"x"),
            NormalizedAsset(PurePosixPath("assets/image-10000.png"), b"y"),
        ),
        (
            PurePosixPath("assets/image-9999.png"),
            PurePosixPath("assets/image-10000.png"),
        ),
        unavailable_asset_count=0,
        source=ManifestSource("word", "docx"),
        limits=LIMITS,
    )

    assert result.extension == ".zip"


@pytest.mark.parametrize(
    ("markdown", "assets"),
    [
        (
            "No reference\n",
            (NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"x"),),
        ),
        (
            "![](assets/../escape.png)\n",
            (NormalizedAsset(PurePosixPath("assets/../escape.png"), b"x"),),
        ),
        (
            "![](assets/image-0002.png)\n![](assets/image-0001.png)\n",
            (
                NormalizedAsset(PurePosixPath("assets/image-0002.png"), b"x"),
                NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"y"),
            ),
        ),
        (
            "![](assets/image-0001.png)\n",
            (
                NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"x"),
                NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"y"),
            ),
        ),
        (
            "![](assets/image-0001.png)\n",
            (
                NormalizedAsset(
                    PurePosixPath("assets/image-0001.png"), b"x", "image/jpeg"
                ),
            ),
        ),
    ],
)
def test_orphan_escaping_unsorted_and_invalid_asset_paths_fail_closed(
    markdown: str, assets: tuple[NormalizedAsset, ...]
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        build_reverse_package(
            markdown,
            assets,
            tuple(asset.path for asset in assets),
            unavailable_asset_count=0,
            source=ManifestSource("word", "docx"),
            limits=LIMITS,
        )
    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize(
    "limits",
    [PackageLimits(1, 20_000), PackageLimits(10_000, 1)],
)
def test_markdown_and_final_package_limits_are_enforced(limits: PackageLimits) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        build_reverse_package(
            "long markdown",
            (),
            (None,),
            unavailable_asset_count=1,
            source=ManifestSource("csv", "csv"),
            limits=limits,
        )
    assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT


def test_oversized_zip_is_rejected_before_output_allocation(mocker) -> None:
    allocate = mocker.patch("markweave.reversions.package.io.BytesIO")

    with pytest.raises(ReverseConversionError) as captured:
        build_reverse_package(
            "![](assets/image-0001.png)\n",
            (NormalizedAsset(PurePosixPath("assets/image-0001.png"), b"image"),),
            (PurePosixPath("assets/image-0001.png"),),
            unavailable_asset_count=0,
            source=ManifestSource("word", "docx"),
            limits=PackageLimits(10_000, 1),
        )

    assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT
    allocate.assert_not_called()


def test_package_limits_must_be_positive_integers() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        PackageLimits(True, 1)


@pytest.mark.parametrize(
    ("markdown", "unavailable_count"),
    [("bad\x00markdown", 0), ("bad\ud800markdown", 0), ("ok", -1), ("ok", True)],
)
def test_invalid_markdown_and_unavailable_count_fail_closed(
    markdown: str, unavailable_count: int
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        build_reverse_package(
            markdown,
            (),
            (),
            unavailable_asset_count=unavailable_count,
            source=ManifestSource("word", "docx"),
            limits=LIMITS,
        )
    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR
