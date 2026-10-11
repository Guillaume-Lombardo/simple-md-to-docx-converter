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
from collections.abc import Callable
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
_FRONT_MATTER = "---"
_YAML_ESCAPE = "\\"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
UPLOAD_LIMIT_CODE = "conversion_request_invalid"
UPLOAD_LIMIT = "The conversion request is invalid."


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


def package_markdown(
    markdown_path: Path,
    markdown: bytes,
    *,
    upload_limit: Callable[[], int] | None = None,
) -> MarkdownPackage | None:
    """Package Markdown and referenced local images, or return ``None``.

    ``None`` means the Markdown has no local image reference (or is not UTF-8)
    and must be uploaded unchanged so the service keeps deciding its validity.
    Images are read only from the Markdown file's directory tree, without
    following any symbolic link. ``upload_limit`` returns the service's upload
    limit; it is called once, only when images must be read.
    """

    try:
        text = markdown.decode("utf-8")
    except UnicodeDecodeError:
        return None
    references = load_image_references()
    if references is None:
        if _may_reference_images(text):
            raise PackageError(PACKAGING_UNAVAILABLE, PACKAGING_UNAVAILABLE_CODE)
        return None
    paths = _referenced_paths(references, text)
    if not paths:
        return None
    limit = upload_limit() if upload_limit is not None else None
    images = _read_images(markdown_path.parent, paths, references, len(markdown), limit)
    content = build_package(markdown, images)
    if limit is not None and len(content) > limit:
        raise PackageError(UPLOAD_LIMIT, UPLOAD_LIMIT_CODE)
    return MarkdownPackage(content, len(images))


def _may_reference_images(text: str) -> bool:
    """Conservatively detect image references without the shared parser.

    Markdown image syntax is always the literal ``![``. Inside YAML front
    matter, only double-quoted escapes (``\\x21``, ``\\u0021``, an escaped line
    break, and so on) can decode into image syntax absent from the raw text, and
    every such escape needs a backslash.
    """

    return _IMAGE_SYNTAX in text or (
        text.startswith(_FRONT_MATTER) and _YAML_ESCAPE in text
    )


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


def _read_images(
    directory: Path,
    paths: list[PurePosixPath],
    references: ModuleType,
    markdown_size: int,
    limit: int | None,
) -> dict[PurePosixPath, bytes]:
    try:
        base = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise PackageError(references.MISSING_IMAGE) from None
    try:
        images: dict[PurePosixPath, bytes] = {}
        total = markdown_size
        for path in paths:
            content = _read_image(base, path, references, limit, total)
            total += len(content)
            images[path] = content
        return images
    finally:
        os.close(base)


def _read_image(
    base: int,
    path: PurePosixPath,
    references: ModuleType,
    limit: int | None,
    total: int,
) -> bytes:
    """Read one image by walking anchored directory descriptors.

    Every component is opened relative to its parent's descriptor with
    ``O_NOFOLLOW``, so a symbolic link anywhere in the path, or a directory
    swapped for one after resolution, is refused instead of followed.
    """

    missing: str = references.MISSING_IMAGE
    escaping: str = references.ESCAPING_IMAGE_PATH
    if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PackageError(missing)
    descriptors: list[int] = []
    parent = base
    try:
        for component in path.parts[:-1]:
            try:
                parent = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            except OSError as error:
                # Linux reports a symlink opened with O_NOFOLLOW | O_DIRECTORY
                # as ENOTDIR; tell it apart from a regular file.
                if error.errno == errno.ENOTDIR and _is_symlink(component, parent):
                    raise PackageError(escaping) from None
                raise
            descriptors.append(parent)
        descriptor = os.open(path.parts[-1], _FILE_FLAGS, dir_fd=parent)
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PackageError(missing)
        if limit is not None and total + metadata.st_size > limit:
            raise PackageError(UPLOAD_LIMIT, UPLOAD_LIMIT_CODE)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            content = stream.read(metadata.st_size + 1)
        if len(content) != metadata.st_size:
            raise PackageError(missing)
        return content
    except PackageError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise PackageError(escaping) from None
        raise PackageError(missing) from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _is_symlink(name: str, directory: int) -> bool:
    try:
        return stat.S_ISLNK(
            os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
        )
    except OSError:
        return False
