"""Unit tests for reverse-conversion asset normalization."""

from __future__ import annotations

import io
import struct
import zipfile
import zlib

import pytest
from PIL import Image

from markweave.conversion.images import ImageLimits
from markweave.reversions.assets import (
    AssetSource,
    ReverseAssetLimits,
    _has_exact_gif_container,
    _has_exact_jpeg_container,
    _has_exact_png_container,
    normalize_assets,
)
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory

pytestmark = pytest.mark.unit

IMAGE_LIMITS = ImageLimits(20_000, 100, 100, 10_000, 100, 16)
LIMITS = ReverseAssetLimits(IMAGE_LIMITS, 8, 40_000, 40_000)


def _image(format_name: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (4, 3), "#123456").save(output, format=format_name)
    return output.getvalue()


def _svg_renderer(_source: bytes, width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(output, format="PNG")
    return output.getvalue()


def _png_with_embedded_payload(payload: bytes) -> bytes:
    png = _image("PNG")
    chunk_type = b"tEXt"
    chunk = (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload))
    )
    return png[:-12] + chunk + png[-12:]


def _png_zip_polyglot() -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr("payload.txt", b"payload")
    return _png_with_embedded_payload(archive.getvalue())


def test_assets_are_png_normalized_deduplicated_and_source_ordered() -> None:
    png = _image()
    result = normalize_assets(
        (
            AssetSource("missing", None, None),
            AssetSource("first", png, "image/png"),
            AssetSource("same-bytes", png, "IMAGE/PNG"),
            AssetSource("first", png, "image/png"),
        ),
        LIMITS,
    )

    assert result.unavailable_asset_count == 1
    assert [str(asset.path) for asset in result.assets] == ["assets/image-0002.png"]
    assert [
        str(reference.path) if reference.path else None
        for reference in result.references
    ] == [
        None,
        "assets/image-0002.png",
        "assets/image-0002.png",
        "assets/image-0002.png",
    ]
    with Image.open(io.BytesIO(result.assets[0].content)) as image:
        assert image.format == "PNG"
        assert image.size == (4, 3)


def test_safe_svg_is_rasterized_through_the_t08_boundary() -> None:
    result = normalize_assets(
        (
            AssetSource(
                "diagram",
                b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="3"/>',
                "image/svg+xml",
            ),
        ),
        LIMITS,
        svg_renderer=_svg_renderer,
    )
    assert result.assets[0].media_type == "image/png"


@pytest.mark.parametrize(
    ("format_name", "media_type"),
    [("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
)
def test_each_supported_raster_signature_is_identified_before_normalization(
    format_name: str, media_type: str
) -> None:
    result = normalize_assets(
        (AssetSource("image", _image(format_name), media_type),), LIMITS
    )
    assert len(result.assets) == 1


@pytest.mark.parametrize(
    "reference",
    [
        AssetSource("mismatch", _image("PNG"), "image/jpeg"),
        AssetSource("polyglot", _image("PNG") + b"PK\x03\x04", "image/png"),
        AssetSource("true-png-zip-polyglot", _png_zip_polyglot(), "image/png"),
        AssetSource(
            "png-pdf-polyglot",
            _png_with_embedded_payload(b"%PDF-1.4\n%%EOF\n"),
            "image/png",
        ),
        AssetSource(
            "png-headerless-pdf-polyglot",
            _png_with_embedded_payload(b"1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"),
            "image/png",
        ),
        AssetSource(
            "jpeg-polyglot", _image("JPEG") + b"PK\x03\x04\xff\xd9", "image/jpeg"
        ),
        AssetSource("gif-polyglot", _image("GIF") + b"PK\x03\x04\x3b", "image/gif"),
        AssetSource("empty", b"", "image/png"),
        AssetSource("missing-type", _image("PNG"), None),
        AssetSource("false-missing", None, "image/png"),
        AssetSource("unknown-type", _image("PNG"), "image/bmp"),
        AssetSource("not-image", b"plain text", "image/png"),
        AssetSource("truncated-png", b"\x89PNG\r\n\x1a\n\x00" * 2, "image/png"),
    ],
)
def test_mismatched_non_image_polyglot_and_invalid_metadata_fail_closed(
    reference: AssetSource,
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets((reference,), LIMITS)
    assert captured.value.category is ReverseErrorCategory.ASSET_INVALID


def test_reused_id_cannot_change_payload() -> None:
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets(
            (
                AssetSource("same", _image("PNG"), "image/png"),
                AssetSource("same", _image("JPEG"), "image/jpeg"),
            ),
            LIMITS,
        )
    assert captured.value.category is ReverseErrorCategory.ASSET_INVALID


def test_invalid_image_after_signature_detection_is_asset_invalid() -> None:
    damaged_jpeg = b"\xff\xd8\xff\xff\xd9"
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets((AssetSource("damaged", damaged_jpeg, "image/jpeg"),), LIMITS)
    assert captured.value.category is ReverseErrorCategory.ASSET_INVALID


def test_per_image_dimension_limit_maps_to_resource_limit() -> None:
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets(
            (AssetSource("wide", _image(), "image/png"),),
            ReverseAssetLimits(
                ImageLimits(20_000, 3, 100, 10_000, 100, 16), 1, 20_000, 20_000
            ),
        )
    assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT


def test_asset_identifier_must_be_a_nonempty_string() -> None:
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets((AssetSource("", _image(), "image/png"),), LIMITS)
    assert captured.value.category is ReverseErrorCategory.ASSET_INVALID


@pytest.mark.parametrize(
    "source",
    [
        b"not-png",
        b"\x89PNG\r\n\x1a\n\x00\x00\x01\x00DATA",
        b"\x89PNG\r\n\x1a\n",
    ],
)
def test_png_container_mutations_are_rejected(source: bytes) -> None:
    assert not _has_exact_png_container(source)


@pytest.mark.parametrize(
    "source",
    [
        b"not-jpeg",
        b"\xff\xd8\xff",
        b"\xff\xd8\xffpayload",
        b"\xff\xd8\xff\x01",
        b"\xff\xd8\xff\x00",
        b"\xff\xd8\xff\xe0\x00\x01",
        b"\xff\xd8\xff\xe0\x00\x10",
    ],
)
def test_jpeg_marker_mutations_are_rejected(source: bytes) -> None:
    assert not _has_exact_jpeg_container(source)


def test_jpeg_scan_accepts_stuffed_and_restart_markers_only_before_exact_eoi() -> None:
    source = b"\xff\xd8\xff\xda\x00\x02scan\xff\x00data\xff\xd0more\xff\xd9"
    assert _has_exact_jpeg_container(source)


def test_gif_extension_blocks_are_bounded_and_preserve_exact_trailer() -> None:
    source = _image("GIF")
    with_comment = source[:-1] + b"\x21\xfe\x01a\x00\x3b"
    assert _has_exact_gif_container(with_comment)
    assert not _has_exact_gif_container(with_comment + b"trailing")
    truncated_block = b"GIF89a\x01\x00\x01\x00\x00\x00\x00\x21\xfe\x10x"
    assert not _has_exact_gif_container(truncated_block)


@pytest.mark.parametrize("source", [b"GIF89a", b"GIF89a" + b"\x00" * 7 + b"?"])
def test_gif_structural_mutations_are_rejected(source: bytes) -> None:
    assert not _has_exact_gif_container(source)


@pytest.mark.parametrize(
    "limits",
    [
        ReverseAssetLimits(IMAGE_LIMITS, 1, 40_000, 40_000),
        ReverseAssetLimits(IMAGE_LIMITS, 8, 1, 40_000),
        ReverseAssetLimits(IMAGE_LIMITS, 8, 40_000, 1),
    ],
)
def test_all_configured_aggregate_limits_are_enforced(
    limits: ReverseAssetLimits,
) -> None:
    references = (
        AssetSource("one", _image(), "image/png"),
        AssetSource("two", _image("JPEG"), "image/jpeg"),
    )
    with pytest.raises(ReverseConversionError) as captured:
        normalize_assets(references, limits)
    assert captured.value.category is ReverseErrorCategory.RESOURCE_LIMIT


@pytest.mark.parametrize("field", [0, -1, True])
def test_limit_values_must_be_positive_integers(field: int) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        ReverseAssetLimits(IMAGE_LIMITS, field, 1, 1)
