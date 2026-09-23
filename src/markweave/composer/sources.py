"""Apply existing bounded archive admission to Composer source packages."""

from __future__ import annotations

import io
import zipfile
import zlib

from markweave.config import Settings
from markweave.conversion.archive import ArchiveLimits, prepare_archive
from markweave.conversion.errors import ConversionError
from markweave.conversion.images import ImageLimits
from markweave.http.composer_errors import ComposerRequestError

_MINIMUM_REVERSION_PACKAGE_ENTRIES = 2
_FROZEN_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_MODE = 0o100644


def _write_frozen_member(package: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_FROZEN_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = _REGULAR_FILE_MODE << 16
    package.writestr(info, content)


def markdown_from_archive(data: bytes, settings: Settings) -> str:
    """Select the unambiguous Markdown entrypoint after full safe ZIP validation."""

    maximum = settings.composer_upload_max_bytes
    if maximum is None:
        raise ComposerRequestError("Composer upload limit is unavailable")
    limits = ArchiveLimits(
        max_archive_bytes=maximum,
        max_entries=settings.conversion_max_files,
        max_member_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_total_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_compression_ratio=settings.conversion_max_compression_ratio,
        max_markdown_bytes=maximum,
        max_images=settings.conversion_max_images,
        max_files=settings.conversion_max_files,
    )
    image_limits = ImageLimits(
        settings.conversion_image_max_source_bytes,
        settings.conversion_image_max_width_pixels,
        settings.conversion_image_max_height_pixels,
        settings.conversion_image_max_pixels,
        settings.conversion_image_max_svg_elements,
        settings.conversion_image_max_svg_depth,
    )
    try:
        markdown = prepare_archive(data, limits, image_limits).markdown
    except ConversionError:
        raise ComposerRequestError("Composer source archive is invalid") from None
    if not markdown:
        raise ComposerRequestError("Composer source Markdown is empty")
    return markdown


def repack_approved_markdown(
    markdown: bytes,
    source_archive: bytes,
    settings: Settings,
    *,
    reversion_result: bool = False,
) -> bytes:
    """Freeze edited Markdown with only validated original image assets."""

    maximum = settings.conversion_upload_max_bytes
    if not markdown or len(markdown) > maximum or len(source_archive) > maximum:
        raise ComposerRequestError("Approved Markdown exceeds the conversion limit")
    limits = ArchiveLimits(
        max_archive_bytes=maximum,
        max_entries=settings.conversion_max_files,
        max_member_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_total_uncompressed_bytes=settings.conversion_max_decompressed_bytes,
        max_compression_ratio=settings.conversion_max_compression_ratio,
        max_markdown_bytes=maximum,
        max_images=settings.conversion_max_images,
        max_files=settings.conversion_max_files,
    )
    image_limits = ImageLimits(
        settings.conversion_image_max_source_bytes,
        settings.conversion_image_max_width_pixels,
        settings.conversion_image_max_height_pixels,
        settings.conversion_image_max_pixels,
        settings.conversion_image_max_svg_elements,
        settings.conversion_image_max_svg_depth,
    )
    try:
        if reversion_result:
            with zipfile.ZipFile(io.BytesIO(source_archive)) as reverse:
                infos = reverse.infolist()
                if (
                    len(infos) < _MINIMUM_REVERSION_PACKAGE_ENTRIES
                    or len(infos) > limits.max_entries + 1
                    or infos[0].filename != "document.md"
                    or infos[-1].filename != "manifest.json"
                    or len({info.filename for info in infos}) != len(infos)
                ):
                    raise ComposerRequestError("Reverse result package is invalid")
                total = sum(info.file_size for info in infos)
                if total > limits.max_total_uncompressed_bytes or any(
                    info.file_size > limits.max_member_uncompressed_bytes
                    or info.compress_size < 0
                    or info.file_size / max(info.compress_size, 1)
                    > limits.max_compression_ratio
                    for info in infos
                ):
                    raise ComposerRequestError(
                        "Reverse result package exceeds the limit"
                    )
                stripped = io.BytesIO()
                with zipfile.ZipFile(
                    stripped, "w", compression=zipfile.ZIP_STORED
                ) as clean:
                    for info in infos[:-1]:
                        with reverse.open(info) as member:
                            content = member.read(info.file_size + 1)
                        if len(content) != info.file_size:
                            raise ComposerRequestError(
                                "Reverse result package is invalid"
                            )
                        _write_frozen_member(clean, info.filename, content)
            source_archive = stripped.getvalue()
        approved = prepare_archive(source_archive, limits, image_limits)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as package:
            _write_frozen_member(package, approved.entrypoint.as_posix(), markdown)
            for resource in approved.resources:
                _write_frozen_member(
                    package,
                    resource.path.as_posix(),
                    resource.content,
                )
        frozen = output.getvalue()
        if len(frozen) > maximum:
            raise ComposerRequestError("Approved asset package exceeds the limit")
        checked = prepare_archive(frozen, limits, image_limits)
        if checked.markdown.encode("utf-8") != markdown:
            raise ComposerRequestError("Approved asset package changed")
        return frozen
    except ComposerRequestError:
        raise
    except ConversionError, KeyError, zipfile.BadZipFile, OSError:
        raise ComposerRequestError("Approved asset package is invalid") from None


def markdown_from_reversion_result(data: bytes, settings: Settings) -> str:
    """Read only the bounded entrypoint of a verified immutable reverse result."""

    maximum = settings.composer_upload_max_bytes
    if maximum is None or len(data) > maximum:
        raise ComposerRequestError("Composer source archive exceeds the limit")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if (
                len(names) < _MINIMUM_REVERSION_PACKAGE_ENTRIES
                or names[0] != "document.md"
                or names[-1] != "manifest.json"
            ):
                raise ComposerRequestError("Reverse result package is invalid")
            info = archive.getinfo("document.md")
            if info.file_size > maximum:
                raise ComposerRequestError("Reverse result Markdown exceeds the limit")
            with archive.open(info) as markdown_file:
                content = markdown_file.read(maximum + 1)
        if len(content) > maximum:
            raise ComposerRequestError("Reverse result Markdown exceeds the limit")
        markdown = content.decode("utf-8")
    except ComposerRequestError:
        raise
    except (
        KeyError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        OSError,
        RuntimeError,
        zlib.error,
    ):
        raise ComposerRequestError("Reverse result package is invalid") from None
    if not markdown or "\x00" in markdown:
        raise ComposerRequestError("Reverse result Markdown is invalid")
    return markdown
