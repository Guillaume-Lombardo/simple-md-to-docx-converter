"""Bounded normalization of untrusted local document images."""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Never
from xml.etree import ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from PIL import Image, ImageOps, UnidentifiedImageError

from md_converter.conversion.errors import ConversionError, validation_error

_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
_FORBIDDEN_XML = re.compile(rb"(?is)<!\s*(?:doctype|entity)\b")
_LENGTH = re.compile(r"^(?:\d+(?:\.\d+)?|\.\d+)(?:px)?$")
_URL = re.compile(r"(?is)url\(\s*(['\"]?)(.*?)\1\s*\)")
_VIEW_BOX_VALUES = 4
_RASTER_FORMATS = {
    ".gif": frozenset({"GIF"}),
    ".jpeg": frozenset({"JPEG"}),
    ".jpg": frozenset({"JPEG"}),
    ".png": frozenset({"PNG"}),
    ".webp": frozenset({"WEBP"}),
}
SUPPORTED_IMAGE_SUFFIXES = frozenset((*_RASTER_FORMATS, ".svg"))
SvgRenderer = Callable[[bytes, int, int], bytes]


@dataclass(frozen=True)
class ImageLimits:
    """Explicit configurable bounds; T18 owns their production values."""

    max_source_bytes: int
    max_width_pixels: int
    max_height_pixels: int
    max_pixels: int

    def __post_init__(self) -> None:
        for value in (
            self.max_source_bytes,
            self.max_width_pixels,
            self.max_height_pixels,
            self.max_pixels,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Image limits must be positive integers")


def _reject_image(message: str = "Document contains an invalid image.") -> Never:
    raise validation_error(message)


def _check_dimensions(width: int, height: int, limits: ImageLimits) -> None:
    if (
        width <= 0
        or height <= 0
        or width > limits.max_width_pixels
        or height > limits.max_height_pixels
        or width * height > limits.max_pixels
    ):
        _reject_image("Document image exceeds configured limits.")


def _normalized_png(image: Image.Image, limits: ImageLimits) -> bytes:
    _check_dimensions(*image.size, limits)
    if getattr(image, "n_frames", 1) != 1:
        _reject_image("Animated images are not supported.")
    image.load()
    transposed = ImageOps.exif_transpose(image)
    mode = "RGBA" if "A" in transposed.getbands() else "RGB"
    normalized = transposed.convert(mode)
    output = io.BytesIO()
    normalized.save(output, format="PNG", compress_level=9, optimize=False)
    return output.getvalue()


def _normalize_raster(source: bytes, suffix: str, limits: ImageLimits) -> bytes:
    try:
        with Image.open(io.BytesIO(source)) as probe:
            image_format = probe.format
            if image_format not in _RASTER_FORMATS[suffix]:
                _reject_image()
            probe.verify()
        with Image.open(io.BytesIO(source)) as image:
            return _normalized_png(image, limits)
    except ConversionError:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        TypeError,
        ValueError,
    ):
        _reject_image()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _has_external_css_reference(value: str) -> bool:
    return any(
        not match.group(2).strip().startswith("#") for match in _URL.finditer(value)
    )


def _sanitize_svg_tree(root: ElementTree.Element) -> bytes:
    if root.tag != f"{{{_SVG_NAMESPACE}}}svg":
        _reject_image()
    forbidden_elements = {
        "foreignObject",
        "script",
        "style",
    }
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in list(parent):
            if (
                not isinstance(child.tag, str)
                or not child.tag.startswith(f"{{{_SVG_NAMESPACE}}}")
                or _local_name(child.tag) in forbidden_elements
            ):
                parent.remove(child)
                continue
            href = child.attrib.get("href") or child.attrib.get(_XLINK_HREF)
            if href is not None and not href.strip().startswith("#"):
                parent.remove(child)
                continue
            pending.append(child)
        for name, value in tuple(parent.attrib.items()):
            local = _local_name(name).casefold()
            if (
                local.startswith("on")
                or name == _XML_BASE
                or (local in {"href", "src"} and not value.strip().startswith("#"))
                or _has_external_css_reference(value)
            ):
                del parent.attrib[name]
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _svg_dimension(root: ElementTree.Element, name: str, fallback: float) -> float:
    value = root.attrib.get(name)
    if value is None:
        return fallback
    stripped = value.strip()
    if not _LENGTH.fullmatch(stripped):
        _reject_image()
    return float(stripped.removesuffix("px"))


def _svg_dimensions(root: ElementTree.Element) -> tuple[int, int]:
    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    fallback_width = fallback_height = math.nan
    if view_box:
        if len(view_box) != _VIEW_BOX_VALUES:
            _reject_image()
        try:
            values = tuple(float(value) for value in view_box)
        except ValueError:
            _reject_image()
        if not all(math.isfinite(value) for value in values):
            _reject_image()
        fallback_width, fallback_height = values[2:]
    width = _svg_dimension(root, "width", fallback_width)
    height = _svg_dimension(root, "height", fallback_height)
    if not math.isfinite(width) or not math.isfinite(height):
        _reject_image()
    return math.ceil(width), math.ceil(height)


def render_svg_with_cairo(source: bytes, width: int, height: int) -> bytes:
    """Rasterize sanitized SVG through the locally installed Cairo engine."""

    try:
        from cairosvg import svg2png  # noqa: PLC0415 - optional native Cairo boundary

        return svg2png(
            bytestring=source,
            output_width=width,
            output_height=height,
            unsafe=False,
        )
    except ImportError, OSError, TypeError, ValueError:
        _reject_image("Document image could not be rasterized.")


def _normalize_svg(source: bytes, limits: ImageLimits, renderer: SvgRenderer) -> bytes:
    if _FORBIDDEN_XML.search(source):
        _reject_image()
    try:
        root = DefusedElementTree.fromstring(source)
    except DefusedXmlException, ElementTree.ParseError, ValueError:
        _reject_image()
    width, height = _svg_dimensions(root)
    _check_dimensions(width, height, limits)
    sanitized = _sanitize_svg_tree(root)
    try:
        rendered = renderer(sanitized, width, height)
        with Image.open(io.BytesIO(rendered)) as image:
            return _normalized_png(image, limits)
    except ConversionError:
        raise
    except (
        Image.DecompressionBombError,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        TypeError,
        ValueError,
    ):
        _reject_image()


def normalize_image(
    path: PurePosixPath,
    source: bytes,
    limits: ImageLimits,
    svg_renderer: SvgRenderer = render_svg_with_cairo,
) -> bytes:
    """Validate and normalize one supported local image to metadata-free PNG."""

    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        _reject_image("Document contains an unsupported image type.")
    if type(source) is not bytes or not source or len(source) > limits.max_source_bytes:
        _reject_image("Document image exceeds configured limits.")
    if suffix == ".svg":
        return _normalize_svg(source, limits, svg_renderer)
    return _normalize_raster(source, suffix, limits)
