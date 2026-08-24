"""Unit tests for bounded local-image normalization."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import cast

import pytest
from PIL import Image

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.images import (
    ImageLimits,
    normalize_image,
    render_svg_with_cairo,
)

pytestmark = pytest.mark.unit


class _FakeCairoSvg(ModuleType):
    svg2png: Callable[..., bytes]


LIMITS = ImageLimits(
    max_source_bytes=100_000,
    max_width_pixels=256,
    max_height_pixels=256,
    max_pixels=65_536,
)


def _image_bytes(format_name: str, **save_options: object) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "#336699").save(output, format=format_name, **save_options)
    return output.getvalue()


def _assert_png(data: bytes, size: tuple[int, int] = (8, 6)) -> None:
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size == size
        assert getattr(image, "n_frames", 1) == 1
        assert "exif" not in image.info


def _blank_svg_renderer(source: bytes, width: int, height: int) -> bytes:
    assert b"svg" in source
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    ("suffix", "format_name"),
    [(".png", "PNG"), (".jpg", "JPEG"), (".gif", "GIF"), (".webp", "WEBP")],
)
def test_supported_static_images_are_normalized_to_png(
    suffix: str, format_name: str
) -> None:
    normalized = normalize_image(
        PurePosixPath(f"assets/image{suffix}"),
        _image_bytes(format_name),
        LIMITS,
    )
    _assert_png(normalized)
    assert normalized == normalize_image(
        PurePosixPath(f"assets/image{suffix}"),
        _image_bytes(format_name),
        LIMITS,
    )


def test_jpeg_orientation_is_applied_and_metadata_is_removed() -> None:
    output = io.BytesIO()
    image = Image.new("RGB", (8, 6), "white")
    exif = image.getexif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)
    normalized = normalize_image(
        PurePosixPath("rotated.jpeg"), output.getvalue(), LIMITS
    )
    _assert_png(normalized, (6, 8))


def test_animated_image_is_rejected() -> None:
    output = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    with pytest.raises(ConversionError) as captured:
        normalize_image(PurePosixPath("animated.gif"), output.getvalue(), LIMITS)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Animated images are not supported."


@pytest.mark.parametrize(
    ("path", "source", "message"),
    [
        ("image.bmp", b"bitmap", "Document contains an unsupported image type."),
        ("image.png", b"not an image", "Document contains an invalid image."),
        ("image.png", b"x" * 100_001, "Document image exceeds configured limits."),
        ("image.jpg", _image_bytes("PNG"), "Document contains an invalid image."),
    ],
)
def test_invalid_types_content_and_source_limits_are_rejected(
    path: str, source: bytes, message: str
) -> None:
    with pytest.raises(ConversionError) as captured:
        normalize_image(PurePosixPath(path), source, LIMITS)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == message


def test_raster_dimension_limits_are_checked_before_decode() -> None:
    with pytest.raises(ConversionError) as captured:
        normalize_image(
            PurePosixPath("wide.png"),
            _image_bytes("PNG"),
            ImageLimits(100_000, 7, 256, 65_536),
        )
    assert str(captured.value) == "Document image exceeds configured limits."


def test_safe_svg_is_rasterized_deterministically() -> None:
    source = Path("tests/corpus/local-images/assets/safe-local.svg").read_bytes()
    first = normalize_image(
        PurePosixPath("assets/diagram.svg"), source, LIMITS, _blank_svg_renderer
    )
    second = normalize_image(
        PurePosixPath("assets/diagram.svg"), source, LIMITS, _blank_svg_renderer
    )
    assert first == second
    _assert_png(first, (120, 80))


def test_svg_xxe_is_rejected_without_entity_resolution() -> None:
    source = Path("tests/corpus/security/xxe.svg").read_bytes()
    with pytest.raises(ConversionError) as captured:
        normalize_image(PurePosixPath("xxe.svg"), source, LIMITS, _blank_svg_renderer)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Document contains an invalid image."


@pytest.mark.parametrize("fixture", ["script.svg", "remote-xlink.svg"])
def test_hostile_svg_active_content_is_removed_before_rasterization(
    fixture: str,
) -> None:
    source = Path("tests/corpus/security", fixture).read_bytes()
    inspected: list[bytes] = []

    def inspect_renderer(svg: bytes, width: int, height: int) -> bytes:
        inspected.append(svg)
        return _blank_svg_renderer(svg, width, height)

    normalized = normalize_image(
        PurePosixPath(fixture), source, LIMITS, inspect_renderer
    )
    _assert_png(normalized, (10, 10))
    sanitized = inspected[0].lower()
    assert b"script" not in sanitized
    assert b"onclick" not in sanitized
    assert b"example.invalid" not in sanitized
    assert b"xlink:href" not in sanitized


def test_svg_external_css_references_and_foreign_content_are_removed() -> None:
    source = b"""<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">
      <style>@import url(https://example.invalid/style.css);</style>
      <foreignObject><body xmlns="http://www.w3.org/1999/xhtml">bad</body></foreignObject>
      <rect width="10" height="10" style="fill:url(file:///etc/passwd)"/>
    </svg>"""
    inspected: list[bytes] = []

    def inspect_renderer(svg: bytes, width: int, height: int) -> bytes:
        inspected.append(svg)
        return _blank_svg_renderer(svg, width, height)

    normalize_image(PurePosixPath("hostile.svg"), source, LIMITS, inspect_renderer)
    sanitized = inspected[0].lower()
    assert b"example.invalid" not in sanitized
    assert b"file:///" not in sanitized
    assert b"foreignobject" not in sanitized


@pytest.mark.parametrize(
    "source",
    [
        b"<not-svg/>",
        b"<svg xmlns='http://www.w3.org/2000/svg'>",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"<svg xmlns='http://www.w3.org/2000/svg' width='10em' height='10'/>",
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10'/>",
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 nope 10'/>",
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 inf 10'/>",
    ],
)
def test_svg_structure_and_dimensions_fail_closed(source: bytes) -> None:
    with pytest.raises(ConversionError, match="invalid image"):
        normalize_image(
            PurePosixPath("invalid.svg"), source, LIMITS, _blank_svg_renderer
        )


def test_svg_dimensions_and_renderer_output_are_bounded() -> None:
    source = b"<svg xmlns='http://www.w3.org/2000/svg' width='300' height='1'/>"
    with pytest.raises(ConversionError, match="exceeds configured limits"):
        normalize_image(PurePosixPath("large.svg"), source, LIMITS, _blank_svg_renderer)
    safe = b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>"
    with pytest.raises(ConversionError, match="invalid image"):
        normalize_image(PurePosixPath("bad.svg"), safe, LIMITS, lambda *_: b"bad")


def test_svg_renderer_preserves_stable_conversion_error() -> None:
    source = b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/>"

    def reject(_source: bytes, _width: int, _height: int) -> bytes:
        raise ConversionError(ConversionErrorCode.VALIDATION, "safe failure")

    with pytest.raises(ConversionError, match="safe failure"):
        normalize_image(PurePosixPath("bad.svg"), source, LIMITS, reject)


def test_cairo_boundary_passes_only_sanitized_bytes_and_fixed_options(mocker) -> None:
    fake = _FakeCairoSvg("cairosvg")
    rendered = _image_bytes("PNG")

    def svg2png(**options: object) -> bytes:
        assert options == {
            "bytestring": b"<svg/>",
            "output_width": 8,
            "output_height": 6,
            "unsafe": False,
        }
        return rendered

    fake.svg2png = svg2png
    mocker.patch.dict(sys.modules, {"cairosvg": fake})
    assert render_svg_with_cairo(b"<svg/>", 8, 6) == rendered


def test_cairo_boundary_maps_engine_failure_to_content_free_error(mocker) -> None:
    fake = _FakeCairoSvg("cairosvg")

    def svg2png(**_options: object) -> bytes:
        raise OSError("sensitive native engine detail")

    fake.svg2png = svg2png
    mocker.patch.dict(sys.modules, {"cairosvg": fake})
    with pytest.raises(ConversionError) as captured:
        render_svg_with_cairo(b"sensitive svg", 8, 6)
    assert str(captured.value) == "Document image could not be rasterized."
    assert "sensitive" not in str(captured.value)


@pytest.mark.parametrize(
    "limits",
    [
        (0, 1, 1, 1),
        (1, True, 1, 1),
        (1, cast("int", 1.0), 1, 1),
        (1, 1, -1, 1),
    ],
)
def test_image_limits_require_positive_integers(
    limits: tuple[int, int, int, int],
) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        ImageLimits(*limits)
