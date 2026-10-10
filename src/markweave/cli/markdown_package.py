"""Client-side packaging of Markdown with its referenced local images (T104, D2).

The remote ``convert`` command places a Markdown file and the local images it
references into the approved archive layout before upload. Image references are
found and resolved by ``markweave.conversion.image_references``, the same code the
service uses, which needs the optional ``markweave[packaging]`` extra. The base
CLI keeps importing only the standard library. The service still applies every
archive, path, image, scanning, and limit rule to the uploaded package.
"""

from __future__ import annotations

import errno
import io
import os
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType

ENTRYPOINT = "document.md"
SUPPORTED_IMAGE_SUFFIXES = frozenset(
    {
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".webp",
    }
)
INVALID_ARCHIVE = "Document archive is invalid."
VALIDATION_CODE = "validation"
PACKAGING_UNAVAILABLE_CODE = "packaging_unavailable"
PACKAGING_UNAVAILABLE = (
    "Markdown image references need the optional packaging support. Install "
    "'markweave[packaging]' to upload the Markdown file with its local images, "
    "or pass --no-package to upload the Markdown file unchanged."
)

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_ATTRIBUTES = (stat.S_IFREG | 0o644) << 16
_ZIP_SYSTEM_UNIX = 3
_IMAGE_SYNTAX = "!["


class PackageError(ValueError):
    """A stable failure detected before upload."""

    def __init__(self, message: str, code: str = VALIDATION_CODE) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class MarkdownPackage:
    """A deterministic approved-layout archive and its packaged image count."""

    content: bytes
    image_count: int


def load_image_references() -> ModuleType | None:
    """Import the shared service parser, or return ``None`` without the extra."""

    try:
        from markweave.conversion import image_references  # noqa: PLC0415
    except ImportError:
        return None
    return image_references


def package_markdown(markdown_path: Path, markdown: bytes) -> MarkdownPackage | None:
    """Package Markdown and referenced local images, or return ``None``.

    ``None`` means the Markdown has no local image reference (or is not UTF-8)
    and must be uploaded unchanged so the service keeps deciding its validity.
    Images are read only from the Markdown file's directory tree.
    """

    try:
        text = markdown.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if _IMAGE_SYNTAX not in text:
        return None
    references = load_image_references()
    if references is None:
        raise PackageError(PACKAGING_UNAVAILABLE, PACKAGING_UNAVAILABLE_CODE)
    paths = _referenced_paths(references, text)
    if not paths:
        return None
    base = Path(os.path.realpath(markdown_path.parent))
    images = {path: _read_image(base, path, references) for path in paths}
    return MarkdownPackage(build_package(markdown, images), len(images))


def build_package(markdown: bytes, images: dict[PurePosixPath, bytes]) -> bytes:
    """Build a deterministic stored ZIP with root ``document.md`` and images."""

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in (
            (ENTRYPOINT, markdown),
            *sorted((path.as_posix(), data) for path, data in images.items()),
        ):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = _ZIP_SYSTEM_UNIX
            info.external_attr = _REGULAR_FILE_ATTRIBUTES
            archive.writestr(info, content)
    return output.getvalue()


def _referenced_paths(references: ModuleType, text: str) -> list[PurePosixPath]:
    from markweave.conversion.errors import ConversionError  # noqa: PLC0415

    entrypoint = PurePosixPath(ENTRYPOINT)
    paths: list[PurePosixPath] = []
    try:
        for destination in references.image_destinations(text):
            if references.is_remote_destination(destination):
                continue
            path = references.resolve_local_image_path(destination, entrypoint)
            if not path.parts:
                raise PackageError(references.MISSING_IMAGE)
            if path not in paths:
                paths.append(path)
    except ConversionError as error:
        # The shared parser raises the service's stable validation messages.
        raise PackageError(str(error)) from None
    keys: set[str] = set()
    for path in paths:
        key = unicodedata.normalize("NFC", path.as_posix()).casefold()
        if key in keys:
            raise PackageError(INVALID_ARCHIVE)
        keys.add(key)
    return paths


def _read_image(base: Path, path: PurePosixPath, references: ModuleType) -> bytes:
    missing: str = references.MISSING_IMAGE
    escaping: str = references.ESCAPING_IMAGE_PATH
    if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PackageError(missing)
    candidate = base.joinpath(*path.parts)
    resolved = Path(os.path.realpath(candidate))
    if resolved != base and base not in resolved.parents:
        raise PackageError(escaping)
    descriptor = -1
    try:
        descriptor = os.open(
            resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise PackageError(missing)
            return stream.read()
    except PackageError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise PackageError(escaping) from None
        raise PackageError(missing) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
