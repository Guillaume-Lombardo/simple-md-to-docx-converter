"""Bounded validation and in-memory preparation of untrusted document archives."""

from __future__ import annotations

import io
import math
import stat
import unicodedata
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from markweave.conversion.errors import ConversionError, validation_error
from markweave.conversion.images import (
    SUPPORTED_IMAGE_SUFFIXES,
    ImageLimits,
    normalize_image,
)

_READ_CHUNK_BYTES = 64 * 1024
_ZIP_SYSTEM_UNIX = 3
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


@dataclass(frozen=True)
class ArchiveLimits:
    """Explicit archive bounds whose production values remain owned by T18."""

    max_archive_bytes: int
    max_entries: int
    max_member_uncompressed_bytes: int
    max_total_uncompressed_bytes: int
    max_compression_ratio: float
    max_markdown_bytes: int
    max_images: int
    max_files: int | None = None

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_archive_bytes,
            self.max_entries,
            self.max_member_uncompressed_bytes,
            self.max_total_uncompressed_bytes,
            self.max_markdown_bytes,
            self.max_images,
        )
        if any(type(value) is not int or value <= 0 for value in integer_limits):
            raise ValueError("Archive integer limits must be positive integers")
        if self.max_files is not None and (
            type(self.max_files) is not int or self.max_files <= 0
        ):
            raise ValueError("Archive file limit must be a positive integer")
        if type(self.max_compression_ratio) not in {int, float}:
            raise ValueError(
                "Archive compression ratio must be finite and at least one"
            )
        try:
            valid_ratio = (
                math.isfinite(self.max_compression_ratio)
                and self.max_compression_ratio >= 1.0
            )
        except OverflowError, TypeError, ValueError:
            valid_ratio = False
        if not valid_ratio:
            raise ValueError(
                "Archive compression ratio must be finite and at least one"
            )


@dataclass(frozen=True)
class ApprovedResource:
    """One image normalized to safe PNG bytes at its logical package path."""

    path: PurePosixPath
    content: bytes
    media_type: str = "image/png"


@dataclass(frozen=True)
class ApprovedDocument:
    """Selected UTF-8 Markdown and normalized resources from one safe package."""

    markdown: str
    entrypoint: PurePosixPath
    resources: tuple[ApprovedResource, ...]
    image_limits: ImageLimits | None = None


ImageNormalizer = Callable[[PurePosixPath, bytes, ImageLimits], bytes]


@dataclass(frozen=True)
class _Member:
    info: zipfile.ZipInfo
    path: PurePosixPath
    key: str


def _invalid_archive() -> None:
    raise validation_error("Document archive is invalid.")


def _archive_limit() -> None:
    raise validation_error("Document archive exceeds configured limits.")


def _path_key(path: PurePosixPath) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _safe_member_path(name: str, *, directory: bool) -> PurePosixPath:
    inspected = name.removesuffix("/") if directory else name
    if (
        not inspected
        or "\0" in inspected
        or "\\" in inspected
        or inspected.startswith(("/", "//"))
    ):
        _invalid_archive()
    path = PurePosixPath(inspected)
    if (
        path.parts in {(), (".",)}
        or ".." in path.parts
        or ":" in path.parts[0]
        or path.as_posix() != inspected
    ):
        _invalid_archive()
    return path


def _is_regular_member(info: zipfile.ZipInfo) -> bool:
    if info.create_system != _ZIP_SYSTEM_UNIX:
        return True
    file_type = stat.S_IFMT(info.external_attr >> 16)
    if info.is_dir():
        return file_type in {0, stat.S_IFDIR}
    return file_type in {0, stat.S_IFREG}


def _inspect_member(info: zipfile.ZipInfo, limits: ArchiveLimits) -> _Member:
    path = _safe_member_path(info.filename, directory=info.is_dir())
    if (
        info.flag_bits & 1
        or not _is_regular_member(info)
        or info.compress_type not in _SUPPORTED_COMPRESSION
        or info.file_size < 0
        or info.compress_size < 0
    ):
        _invalid_archive()
    if (
        info.file_size > limits.max_member_uncompressed_bytes
        or info.file_size / max(info.compress_size, 1) > limits.max_compression_ratio
    ):
        _archive_limit()
    if not info.is_dir():
        suffix = path.suffix.casefold()
        if suffix != ".md" and suffix not in SUPPORTED_IMAGE_SUFFIXES:
            _invalid_archive()
    return _Member(info, path, _path_key(path))


def _reject_file_prefix_collisions(members: tuple[_Member, ...]) -> None:
    file_keys = {member.key for member in members if not member.info.is_dir()}
    for member in members:
        parent = member.path.parent
        while parent.parts not in {(), (".",)}:
            if _path_key(parent) in file_keys:
                _invalid_archive()
            parent = parent.parent


def _select_entrypoint(file_members: tuple[_Member, ...]) -> PurePosixPath:
    root_document = next(
        (
            member.path
            for member in file_members
            if member.path.as_posix() == "document.md"
        ),
        None,
    )
    markdown_candidates = tuple(
        member.path for member in file_members if member.path.suffix.casefold() == ".md"
    )
    if root_document is not None:
        entrypoint = root_document
    elif len(markdown_candidates) == 1:
        entrypoint = markdown_candidates[0]
    else:
        raise validation_error(
            "Document archive has no unambiguous Markdown entrypoint."
        )
    return entrypoint


def _preflight(
    archive: zipfile.ZipFile, limits: ArchiveLimits
) -> tuple[tuple[_Member, ...], PurePosixPath]:
    infos = archive.infolist()
    if not infos:
        _invalid_archive()
    if len(infos) > limits.max_entries:
        _archive_limit()
    members: list[_Member] = []
    keys: set[str] = set()
    total_uncompressed = 0
    file_count = 0
    image_count = 0
    for info in infos:
        member = _inspect_member(info, limits)
        if member.key in keys:
            _invalid_archive()
        keys.add(member.key)
        members.append(member)
        if not info.is_dir():
            file_count += 1
            if limits.max_files is not None and file_count > limits.max_files:
                _archive_limit()
        total_uncompressed += info.file_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            _archive_limit()
        if (
            not info.is_dir()
            and member.path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES
        ):
            image_count += 1
            if image_count > limits.max_images:
                _archive_limit()
    file_members = tuple(member for member in members if not member.info.is_dir())
    _reject_file_prefix_collisions(tuple(members))
    return tuple(members), _select_entrypoint(file_members)


def _read_member_bounded(
    archive: zipfile.ZipFile,
    member: _Member,
    limits: ArchiveLimits,
    total_read: int,
) -> tuple[bytes, int]:
    payload = bytearray()
    actual_size = 0
    try:
        with archive.open(member.info, "r") as source:
            while True:
                remaining_member = limits.max_member_uncompressed_bytes - actual_size
                remaining_total = (
                    limits.max_total_uncompressed_bytes - total_read - actual_size
                )
                remaining_declared = member.info.file_size - actual_size
                read_size = min(
                    _READ_CHUNK_BYTES,
                    remaining_member + 1,
                    remaining_total + 1,
                    remaining_declared + 1,
                )
                chunk = source.read(max(read_size, 1))
                if not chunk:
                    break
                actual_size += len(chunk)
                if (
                    actual_size > member.info.file_size
                    or actual_size > limits.max_member_uncompressed_bytes
                    or total_read + actual_size > limits.max_total_uncompressed_bytes
                ):
                    _archive_limit()
                payload.extend(chunk)
    except ConversionError:
        raise
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        zlib.error,
    ):
        _invalid_archive()
    if actual_size != member.info.file_size:
        _invalid_archive()
    return bytes(payload), total_read + actual_size


def prepare_archive(
    data: bytes,
    limits: ArchiveLimits,
    image_limits: ImageLimits,
    *,
    image_normalizer: ImageNormalizer = normalize_image,
) -> ApprovedDocument:
    """Validate a ZIP entirely before returning selected Markdown and safe images."""

    if type(data) is not bytes or not data:
        _invalid_archive()
    if len(data) > limits.max_archive_bytes:
        _archive_limit()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except OSError, zipfile.BadZipFile:
        _invalid_archive()
    with archive:
        members, entrypoint = _preflight(archive, limits)
        payloads: dict[PurePosixPath, bytes] = {}
        total_read = 0
        for member in members:
            if member.info.is_dir():
                continue
            payload, total_read = _read_member_bounded(
                archive, member, limits, total_read
            )
            payloads[member.path] = payload

    markdown_source = payloads[entrypoint]
    if len(markdown_source) > limits.max_markdown_bytes:
        _archive_limit()
    try:
        markdown = markdown_source.decode("utf-8")
    except UnicodeDecodeError:
        raise validation_error("Document Markdown is not valid UTF-8.") from None
    resources: list[ApprovedResource] = []
    for path in sorted(payloads, key=str):
        if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        normalized = image_normalizer(path, payloads[path], image_limits)
        if type(normalized) is not bytes or not normalized:
            raise validation_error("Document contains an invalid image.")
        resources.append(ApprovedResource(path, normalized))
    return ApprovedDocument(markdown, entrypoint, tuple(resources), image_limits)
