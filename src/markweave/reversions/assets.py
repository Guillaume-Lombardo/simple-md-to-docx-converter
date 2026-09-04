"""Bounded normalization of untrusted reverse-conversion image assets."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from markweave.conversion.errors import ConversionError
from markweave.conversion.images import ImageLimits, SvgRenderer, normalize_image
from markweave.reversions.errors import ReverseErrorCategory, reject

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_ALLOWED_CHUNKS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"})
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_JPEG_MARKER_PREFIX = 0xFF
_JPEG_STUFFED_BYTE = 0x00
_JPEG_TEMPORARY_MARKER = 0x01
_JPEG_RESTART_FIRST = 0xD0
_JPEG_START_OF_IMAGE = 0xD8
_JPEG_END_OF_IMAGE = 0xD9
_JPEG_START_OF_SCAN = 0xDA
_JPEG_MIN_SEGMENT_LENGTH = 2
_GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
_GIF_LOGICAL_SCREEN_END = 13
_GIF_EXTENSION = 0x21
_GIF_IMAGE = 0x2C
_GIF_TRAILER = 0x3B
_SVG_START = re.compile(rb"^\s*(?:<\?xml\b[^>]*>\s*)?<svg(?:\s|>)", re.IGNORECASE)
_WEBP_HEADER_BYTES = 12
_SECONDARY_CONTAINER_SIGNATURES = (
    b"%PDF-",
    b"startxref",
    b"%%EOF",
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x7fELF",
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"Rar!\x1a\x07",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b\x08",
)
_MEDIA_SUFFIXES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class ReverseAssetLimits:
    """Caller-supplied limits whose production values are owned by T71."""

    image: ImageLimits
    max_asset_count: int
    max_total_source_bytes: int
    max_total_output_bytes: int

    def __post_init__(self) -> None:
        for value in (
            self.max_asset_count,
            self.max_total_source_bytes,
            self.max_total_output_bytes,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("Reverse asset limits must be positive integers")


@dataclass(frozen=True)
class AssetSource:
    """One image occurrence in source-position traversal order."""

    asset_id: str
    source: bytes | None
    declared_media_type: str | None


@dataclass(frozen=True)
class NormalizedAsset:
    """One unique normalized PNG ready for deterministic packaging."""

    path: PurePosixPath
    content: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class NormalizedAssetReference:
    """The normalized local path, or ``None`` for an unavailable occurrence."""

    asset_id: str
    path: PurePosixPath | None


@dataclass(frozen=True)
class AssetNormalizationResult:
    """Normalized unique files plus every occurrence's source-position mapping."""

    references: tuple[NormalizedAssetReference, ...]
    assets: tuple[NormalizedAsset, ...]
    unavailable_asset_count: int


def _has_exact_png_container(source: bytes) -> bool:
    if not source.startswith(_PNG_SIGNATURE):
        return False
    offset = len(_PNG_SIGNATURE)
    chunks: list[bytes] = []
    while offset + 12 <= len(source):
        length = int.from_bytes(source[offset : offset + 4], "big")
        end = offset + 12 + length
        if end > len(source):
            return False
        chunk_type = source[offset + 4 : offset + 8]
        content = source[offset + 8 : offset + 8 + length]
        expected_crc = int.from_bytes(source[end - 4 : end], "big")
        if (
            chunk_type not in _PNG_ALLOWED_CHUNKS
            or zlib.crc32(chunk_type + content) != expected_crc
        ):
            return False
        chunks.append(chunk_type)
        offset = end
        if chunk_type == b"IEND":
            first_idat = chunks.index(b"IDAT") if b"IDAT" in chunks else -1
            last_idat = (
                len(chunks) - 1 - chunks[::-1].index(b"IDAT") if first_idat >= 0 else -1
            )
            return (
                length == 0
                and offset == len(source)
                and chunks[0] == b"IHDR"
                and source[len(_PNG_SIGNATURE) : len(_PNG_SIGNATURE) + 4]
                == (13).to_bytes(4, "big")
                and chunks.count(b"IHDR") == 1
                and chunks.count(b"IEND") == 1
                and first_idat >= 1
                and all(
                    chunk == b"IDAT" for chunk in chunks[first_idat : last_idat + 1]
                )
                and chunks.count(b"PLTE") <= 1
                and chunks.count(b"tRNS") <= 1
                and all(
                    chunks.index(chunk) < first_idat
                    for chunk in (b"PLTE", b"tRNS")
                    if chunk in chunks
                )
            )
    return False


def _has_exact_jpeg_container(  # noqa: PLR0911 - direct JPEG marker grammar
    source: bytes,
) -> bool:
    if not source.startswith(_JPEG_SIGNATURE):
        return False
    offset = 2
    in_scan = False
    while offset < len(source):
        marker_start = source.find(b"\xff", offset)
        if marker_start < 0 or (not in_scan and marker_start != offset):
            return False
        offset = marker_start + 1
        while offset < len(source) and source[offset] == _JPEG_MARKER_PREFIX:
            offset += 1
        if offset >= len(source):
            return False
        marker = source[offset]
        offset += 1
        if in_scan and (
            marker == _JPEG_STUFFED_BYTE
            or _JPEG_RESTART_FIRST <= marker < _JPEG_START_OF_IMAGE
        ):
            continue
        in_scan = False
        if marker == _JPEG_END_OF_IMAGE:
            return offset == len(source)
        if marker == _JPEG_TEMPORARY_MARKER or (
            _JPEG_RESTART_FIRST <= marker <= _JPEG_START_OF_IMAGE
        ):
            continue
        if marker == _JPEG_STUFFED_BYTE or offset + _JPEG_MIN_SEGMENT_LENGTH > len(
            source
        ):
            return False
        segment_length = int.from_bytes(
            source[offset : offset + _JPEG_MIN_SEGMENT_LENGTH], "big"
        )
        if segment_length < _JPEG_MIN_SEGMENT_LENGTH or offset + segment_length > len(
            source
        ):
            return False
        offset += segment_length
        in_scan = marker == _JPEG_START_OF_SCAN
    return False


def _skip_gif_sub_blocks(source: bytes, offset: int) -> int | None:
    while offset < len(source):
        block_length = source[offset]
        offset += 1
        if block_length == 0:
            return offset
        offset += block_length
        if offset > len(source):
            return None
    return None


def _has_exact_gif_container(source: bytes) -> bool:
    if not source.startswith(_GIF_SIGNATURES) or len(source) < _GIF_LOGICAL_SCREEN_END:
        return False
    packed = source[10]
    offset = _GIF_LOGICAL_SCREEN_END
    if packed & 0x80:
        offset += 3 * (2 ** ((packed & 0x07) + 1))
    while offset < len(source):
        marker = source[offset]
        if marker == _GIF_TRAILER:
            return offset + 1 == len(source)
        if marker == _GIF_EXTENSION:
            offset = _skip_gif_sub_blocks(source, offset + 2) or len(source)
            continue
        if marker != _GIF_IMAGE or offset + 10 > len(source):
            return False
        packed = source[offset + 9]
        offset += 10
        if packed & 0x80:
            offset += 3 * (2 ** ((packed & 0x07) + 1))
        if offset >= len(source):
            return False
        offset = _skip_gif_sub_blocks(source, offset + 1) or len(source)
    return False


def _detected_suffix(source: bytes) -> str:
    if _has_exact_png_container(source):
        return ".png"
    if _has_exact_jpeg_container(source):
        return ".jpg"
    if _has_exact_gif_container(source):
        return ".gif"
    if (
        len(source) >= _WEBP_HEADER_BYTES
        and source.startswith(b"RIFF")
        and source[8:12] == b"WEBP"
        and int.from_bytes(source[4:8], "little") + 8 == len(source)
    ):
        return ".webp"
    if _SVG_START.match(source):
        return ".svg"
    return reject(ReverseErrorCategory.ASSET_INVALID)


def _normalized_declared_media_type(value: str | None) -> str:
    if value is None or type(value) is not str:
        reject(ReverseErrorCategory.ASSET_INVALID)
    normalized = value.strip().casefold()
    if normalized not in _MEDIA_SUFFIXES:
        reject(ReverseErrorCategory.ASSET_INVALID)
    return normalized


def _inspect_source(reference: AssetSource) -> tuple[bytes, str, str]:
    source = reference.source
    if type(source) is not bytes or not source:
        reject(ReverseErrorCategory.ASSET_INVALID)
    if zipfile.is_zipfile(io.BytesIO(source)) or any(
        source.find(signature, 1) >= 0 for signature in _SECONDARY_CONTAINER_SIGNATURES
    ):
        reject(ReverseErrorCategory.ASSET_INVALID)
    media_type = _normalized_declared_media_type(reference.declared_media_type)
    suffix = _detected_suffix(source)
    if _MEDIA_SUFFIXES[media_type] != suffix:
        reject(ReverseErrorCategory.ASSET_INVALID)
    return hashlib.sha256(source).digest(), media_type, suffix


def _normalize_source(
    source: bytes,
    suffix: str,
    limits: ReverseAssetLimits,
    svg_renderer: SvgRenderer | None,
) -> bytes:
    try:
        path = PurePosixPath(f"source{suffix}")
        content = (
            normalize_image(path, source, limits.image)
            if svg_renderer is None
            else normalize_image(path, source, limits.image, svg_renderer)
        )
    except ConversionError as error:
        category = (
            ReverseErrorCategory.RESOURCE_LIMIT
            if "configured limits" in str(error)
            else ReverseErrorCategory.ASSET_INVALID
        )
        reject(category)
    return content


def normalize_assets(
    references: tuple[AssetSource, ...],
    limits: ReverseAssetLimits,
    *,
    svg_renderer: SvgRenderer | None = None,
) -> AssetNormalizationResult:
    """Normalize referenced assets once, preserving first-reference ordering.

    Missing bytes are an explicit unavailable occurrence. Every supplied byte stream is
    signature-identified independently of its declared type, rejected when its container has
    trailing polyglot data, and normalized through the T08 image boundary.
    """

    if len(references) > limits.max_asset_count:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)

    source_total = 0
    output_total = 0
    unavailable_count = 0
    by_id: dict[str, tuple[bytes, str, PurePosixPath]] = {}
    by_content: dict[bytes, PurePosixPath] = {}
    normalized_assets: list[NormalizedAsset] = []
    normalized_references: list[NormalizedAssetReference] = []

    for ordinal, reference in enumerate(references, start=1):
        if type(reference.asset_id) is not str or not reference.asset_id:
            reject(ReverseErrorCategory.ASSET_INVALID)
        if reference.source is None:
            if reference.declared_media_type is not None:
                reject(ReverseErrorCategory.ASSET_INVALID)
            unavailable_count += 1
            normalized_references.append(
                NormalizedAssetReference(reference.asset_id, None)
            )
            continue
        if type(reference.source) is not bytes or not reference.source:
            reject(ReverseErrorCategory.ASSET_INVALID)
        source_total += len(reference.source)
        if source_total > limits.max_total_source_bytes:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        source_fingerprint, media_type, suffix = _inspect_source(reference)
        prior_id = by_id.get(reference.asset_id)
        if prior_id is not None:
            if prior_id[:2] != (source_fingerprint, media_type):
                reject(ReverseErrorCategory.ASSET_INVALID)
            normalized_references.append(
                NormalizedAssetReference(reference.asset_id, prior_id[2])
            )
            continue

        path = by_content.get(source_fingerprint)
        if path is None:
            content = _normalize_source(reference.source, suffix, limits, svg_renderer)
            output_total += len(content)
            if output_total > limits.max_total_output_bytes:
                reject(ReverseErrorCategory.RESOURCE_LIMIT)
            path = PurePosixPath(f"assets/image-{ordinal:04d}.png")
            by_content[source_fingerprint] = path
            normalized_assets.append(NormalizedAsset(path, content))

        by_id[reference.asset_id] = (source_fingerprint, media_type, path)
        normalized_references.append(NormalizedAssetReference(reference.asset_id, path))

    return AssetNormalizationResult(
        references=tuple(normalized_references),
        assets=tuple(normalized_assets),
        unavailable_asset_count=unavailable_count,
    )
