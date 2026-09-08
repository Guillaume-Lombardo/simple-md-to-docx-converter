"""Deterministic Markdown and asset-package construction."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, cast

from markweave.reversions.assets import NormalizedAsset
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.manifest import (
    ManifestResult,
    ManifestSource,
    canonical_manifest_bytes,
)

_ASSET_PATH = re.compile(r"^assets/image-(\d{4,})\.png$")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_ZIP_MEDIA_TYPE = "application/zip"


@dataclass(frozen=True)
class PackageLimits:
    """Caller-supplied result bounds whose production values are owned by T71."""

    max_markdown_bytes: int
    max_package_bytes: int

    def __post_init__(self) -> None:
        for value in (self.max_markdown_bytes, self.max_package_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError("Reverse package limits must be positive integers")


@dataclass(frozen=True)
class ReversePackage:
    """A bounded result ready for supervisor validation, never direct publication."""

    content: bytes
    media_type: str
    extension: str


class _CountingWriter:
    """Seekable byte sink used to size a ZIP without allocating its output."""

    def __init__(self) -> None:
        self.position = 0
        self.size = 0

    def write(self, content: bytes) -> int:
        self.position += len(content)
        self.size = max(self.size, self.position)
        return len(content)

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self.position + offset
        elif whence == io.SEEK_END:
            position = self.size + offset
        else:
            raise ValueError("Unsupported seek mode")
        if position < 0:
            raise ValueError("Negative seek position")
        self.position = position
        return position

    def flush(self) -> None:
        pass


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=_ZIP_TIMESTAMP)
    # Stored entries avoid zlib-version-dependent bytes in the canonical package.
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits = 0x800
    return info


def _validate_assets(
    markdown: str,
    assets: tuple[NormalizedAsset, ...],
    references: tuple[PurePosixPath | None, ...],
) -> None:
    paths = tuple(asset.path.as_posix() for asset in assets)
    if len(paths) != len(set(paths)):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    ordinals: list[int] = []
    for asset, path in zip(assets, paths, strict=True):
        match = _ASSET_PATH.fullmatch(path)
        if (
            match is None
            or type(asset.content) is not bytes
            or not asset.content
            or asset.media_type != "image/png"
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        ordinals.append(int(match.group(1)))
    if ordinals != sorted(ordinals):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    referenced_paths: list[str] = []
    for reference in references:
        if reference is None:
            continue
        if type(reference) is not PurePosixPath:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        referenced_paths.append(reference.as_posix())
    if set(referenced_paths) != set(paths) or any(
        markdown.count(f"]({path})") < referenced_paths.count(path)
        for path in set(referenced_paths)
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)


def _write_zip(
    destination: BinaryIO | _CountingWriter,
    entries: tuple[tuple[str, bytes], ...],
) -> None:
    with zipfile.ZipFile(
        cast(BinaryIO, destination), mode="w", compression=zipfile.ZIP_STORED
    ) as archive:
        for path, content in entries:
            archive.writestr(_zip_info(path), content)


def build_reverse_package(  # noqa: PLR0913 - explicit bounded package contract
    markdown: str,
    assets: tuple[NormalizedAsset, ...],
    asset_references: tuple[PurePosixPath | None, ...],
    *,
    unavailable_asset_count: int,
    source: ManifestSource,
    limits: PackageLimits,
) -> ReversePackage:
    """Build plain Markdown or the closed deterministic ZIP layout."""

    if type(markdown) is not str or "\x00" in markdown:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    try:
        markdown_bytes = markdown.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if len(markdown_bytes) > limits.max_markdown_bytes:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    if type(unavailable_asset_count) is not int or unavailable_asset_count < 0:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if (
        sum(reference is None for reference in asset_references)
        != unavailable_asset_count
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    _validate_assets(markdown, assets, asset_references)

    if not assets and unavailable_asset_count == 0:
        return ReversePackage(markdown_bytes, _MARKDOWN_MEDIA_TYPE, ".md")

    asset_bytes = sum(len(asset.content) for asset in assets)
    mode = "markdown_with_assets" if assets else "markdown_with_unavailable_assets"
    manifest = canonical_manifest_bytes(
        source,
        ManifestResult(
            mode=mode,
            asset_count=len(assets),
            asset_bytes=asset_bytes,
            unavailable_asset_count=unavailable_asset_count,
        ),
    )
    entries = (
        ("document.md", markdown_bytes),
        *((asset.path.as_posix(), asset.content) for asset in assets),
        ("manifest.json", manifest),
    )
    counter = _CountingWriter()
    _write_zip(counter, entries)
    if counter.size > limits.max_package_bytes:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    output = io.BytesIO()
    _write_zip(output, entries)
    content = output.getvalue()
    if len(content) != counter.size:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return ReversePackage(content, _ZIP_MEDIA_TYPE, ".zip")
