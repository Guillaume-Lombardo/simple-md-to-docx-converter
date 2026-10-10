"""Client-side packaging of Markdown with its referenced local images (T104, D2).

The remote ``convert`` command uses this standard-library-only module to place a
Markdown file and the local images it references into the approved archive layout
before upload. The service still applies every archive, path, image, scanning and
limit rule to the uploaded package; this module only collects files and gives early
feedback with the service's stable validation messages.
"""

from __future__ import annotations

import errno
import html
import io
import os
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

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

# Stable messages shared with the service's archive and Markdown validation.
INVALID_IMAGE_PATH = "Markdown image path is invalid."
ESCAPING_IMAGE_PATH = "Markdown image path escapes the archive."
MISSING_IMAGE = "Markdown image is missing or unapproved."
INVALID_ARCHIVE = "Document archive is invalid."
VALIDATION_CODE = "validation"

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_REGULAR_FILE_ATTRIBUTES = (stat.S_IFREG | 0o644) << 16
_ZIP_SYSTEM_UNIX = 3
_REMOTE_RESOURCE = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*(?::|%3a)|//|%2f%2f)")
_AMBIGUOUS_ESCAPE = re.compile(r"(?i)%(?:2e|2f|5c)")
_MAX_URL_DECODE_PASSES = 2
_FENCE = re.compile(r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_CONTAINER_PREFIX = re.compile(r"^(?:\s{0,3}(?:>|[-+*]|\d{1,9}[.)])(?:\s+|$))+")
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-+*]|\d{1,9}[.)])(?:\s|$)")
_DEFINITION = re.compile(
    r"^ {0,3}\[(?P<label>(?:[^\]\\]|\\.){1,999})\]:[ \t]*(?P<rest>\S.*)?$"
)
_ASCII_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
_INDENTED_CODE_COLUMNS = 4
_ASCII_CONTROL_END = 32


class PackageError(ValueError):
    """A stable validation failure detected before upload."""

    code = VALIDATION_CODE

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class MarkdownPackage:
    """A deterministic approved-layout archive and its packaged image count."""

    content: bytes
    image_count: int


def image_destinations(markdown: str) -> tuple[str, ...]:
    """Return image destinations in source order, excluding code."""

    text = _strip_inline_code(_without_code_blocks(markdown))
    definitions = _reference_definitions(text)
    destinations: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if not text.startswith("![", index):
            index += 1
            continue
        label_end = _matching_bracket(text, index + 1)
        if label_end is None:
            index += 2
            continue
        destination, next_index = _image_destination(
            text, label_end, text[index + 2 : label_end], definitions
        )
        if destination is not None:
            destinations.append(destination)
            index = next_index
        else:
            index += 2
    return tuple(destinations)


def resolve_image_path(destination: str) -> PurePosixPath | None:
    """Resolve one destination from the archive-root entrypoint.

    Remote destinations return ``None`` because the service rejects them; the
    client never downloads or packages them.
    """

    if _is_remote(destination):
        return None
    # Without a scheme or "//" authority (rejected above), splitting cannot fail.
    parsed = urlsplit(destination)
    if parsed.query or parsed.fragment or _AMBIGUOUS_ESCAPE.search(destination):
        raise PackageError(INVALID_IMAGE_PATH)
    try:
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise PackageError(INVALID_IMAGE_PATH) from None
    if (
        unquote(decoded, encoding="utf-8", errors="replace") != decoded
        or not decoded
        or "\0" in decoded
        or "\\" in decoded
        or decoded.startswith("/")
        or "?" in decoded
        or "#" in decoded
    ):
        raise PackageError(INVALID_IMAGE_PATH)
    relative = PurePosixPath(decoded)
    if relative.parts and ":" in relative.parts[0]:
        raise PackageError(INVALID_IMAGE_PATH)
    resolved: list[str] = []
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise PackageError(ESCAPING_IMAGE_PATH)
            resolved.pop()
            continue
        resolved.append(part)
    if not resolved:
        raise PackageError(MISSING_IMAGE)
    return PurePosixPath(*resolved)


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
    paths: list[PurePosixPath] = []
    for destination in image_destinations(text):
        path = resolve_image_path(destination)
        if path is not None and path not in paths:
            paths.append(path)
    if not paths:
        return None
    keys: set[str] = set()
    for path in paths:
        key = unicodedata.normalize("NFC", path.as_posix()).casefold()
        if key in keys:
            raise PackageError(INVALID_ARCHIVE)
        keys.add(key)
    base = Path(os.path.realpath(markdown_path.parent))
    images = {path: _read_image(base, path) for path in paths}
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


def _read_image(base: Path, path: PurePosixPath) -> bytes:
    if path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
        raise PackageError(MISSING_IMAGE)
    candidate = base.joinpath(*path.parts)
    resolved = Path(os.path.realpath(candidate))
    if resolved != base and base not in resolved.parents:
        raise PackageError(ESCAPING_IMAGE_PATH)
    descriptor = -1
    try:
        descriptor = os.open(
            resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise PackageError(MISSING_IMAGE)
            return stream.read()
    except PackageError:
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise PackageError(ESCAPING_IMAGE_PATH) from None
        raise PackageError(MISSING_IMAGE) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _is_remote(destination: str) -> bool:
    value = destination
    for _ in range(_MAX_URL_DECODE_PASSES + 1):
        if _REMOTE_RESOURCE.search(value):
            return True
        decoded = unquote(value, encoding="utf-8", errors="replace")
        if decoded == value:
            return False
        value = decoded
    return False


def _without_code_blocks(markdown: str) -> str:
    """Blank fenced and indented code lines while keeping line structure."""

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    fence: tuple[str, int] | None = None
    previous_blank = True
    in_list = False
    for line in lines:
        content = _CONTAINER_PREFIX.sub("", line, count=1)
        if fence is not None:
            closing = _FENCE.match(content)
            if (
                closing is not None
                and closing.group("fence")[0] == fence[0]
                and len(closing.group("fence")) >= fence[1]
                and not closing.group("info").strip()
            ):
                fence = None
            kept.append("")
            previous_blank = True
            continue
        opening = _FENCE.match(content)
        if opening is not None and not (
            opening.group("fence")[0] == "`" and "`" in opening.group("info")
        ):
            fence = (opening.group("fence")[0], len(opening.group("fence")))
            kept.append("")
            previous_blank = True
            continue
        blank = not line.strip()
        if _LIST_ITEM.match(line):
            in_list = True
        elif not blank and previous_blank and not line[:1].isspace():
            in_list = False
        indented = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip(" "))
        if (
            not blank
            and not in_list
            and previous_blank
            and (indented >= _INDENTED_CODE_COLUMNS)
        ):
            kept.append("")
            continue
        kept.append(line)
        previous_blank = blank
    return "\n".join(kept)


def _strip_inline_code(text: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\\" and index + 1 < len(text):
            output.append(text[index : index + 2])
            index += 2
            continue
        if character != "`":
            output.append(character)
            index += 1
            continue
        run_end = index
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        run = text[index:run_end]
        closing = _closing_backticks(text, run_end, len(run))
        if closing is None:
            output.append(run)
            index = run_end
            continue
        output.append(" " * (closing + len(run) - index))
        index = closing + len(run)
    return "".join(output)


def _closing_backticks(text: str, start: int, length: int) -> int | None:
    paragraph_end = text.find("\n\n", start)
    limit = len(text) if paragraph_end < 0 else paragraph_end
    index = start
    while index < limit:
        if text[index] != "`":
            index += 1
            continue
        run_end = index
        while run_end < limit and text[run_end] == "`":
            run_end += 1
        if run_end - index == length:
            return index
        index = run_end
    return None


def _matching_bracket(text: str, opening: int) -> int | None:
    depth = 0
    index = opening
    while index < len(text):
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "\n" and text.startswith("\n", index + 1):
            return None
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _image_destination(
    text: str, label_end: int, label: str, definitions: dict[str, str]
) -> tuple[str | None, int]:
    following = label_end + 1
    if text.startswith("(", following):
        inline = _inline_destination(text, following + 1)
        if inline is not None:
            return inline
    if text.startswith("[", following):
        reference_end = _matching_bracket(text, following)
        if reference_end is not None:
            reference = text[following + 1 : reference_end]
            key = _label_key(reference if reference.strip() else label)
            return definitions.get(key), reference_end + 1
    return definitions.get(_label_key(label)), following


def _inline_destination(text: str, start: int) -> tuple[str, int] | None:
    index = _skip_space(text, start)
    parsed = (
        _angle_destination(text, index)
        if text.startswith("<", index)
        else _bare_destination(text, index)
    )
    if parsed is None:
        return None
    raw, after_destination = parsed
    index = _skip_space(text, after_destination)
    if index > after_destination and index < len(text) and text[index] in "\"'(":
        closing = {'"': '"', "'": "'", "(": ")"}[text[index]]
        index += 1
        while index < len(text) and text[index] != closing:
            index += 2 if text[index] == "\\" else 1
        if index >= len(text):
            return None
        index = _skip_space(text, index + 1)
    if not text.startswith(")", index):
        return None
    return _normalize_destination(raw), index + 1


def _angle_destination(text: str, index: int) -> tuple[str, int] | None:
    end = index + 1
    while end < len(text) and text[end] not in "<>\n":
        end += 2 if text[end] == "\\" else 1
    if end >= len(text) or text[end] != ">":
        return None
    return text[index + 1 : end], end + 1


def _bare_destination(text: str, index: int) -> tuple[str, int]:
    end = index
    depth = 0
    while end < len(text):
        character = text[end]
        if character == "\\" and end + 1 < len(text):
            end += 2
            continue
        if character.isspace() or ord(character) < _ASCII_CONTROL_END:
            break
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                break
            depth -= 1
        end += 1
    return text[index:end], end


def _skip_space(text: str, index: int) -> int:
    newlines = 0
    while index < len(text) and text[index] in " \t\n":
        if text[index] == "\n":
            newlines += 1
            if newlines > 1:
                break
        index += 1
    return index


def _reference_definitions(text: str) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for line in text.split("\n"):
        match = _DEFINITION.match(line)
        if match is None or match.group("rest") is None:
            continue
        rest = match.group("rest").strip()
        if rest.startswith("<"):
            end = rest.find(">")
            if end < 0:
                continue
            raw = rest[1:end]
        else:
            raw = rest.split()[0]
        key = _label_key(match.group("label"))
        if key and key not in definitions:
            definitions[key] = _normalize_destination(raw)
    return definitions


def _label_key(label: str) -> str:
    return " ".join(label.split()).casefold()


def _normalize_destination(raw: str) -> str:
    unescaped = re.sub(
        r"\\(.)",
        lambda match: (
            match.group(1) if match.group(1) in _ASCII_PUNCTUATION else match.group(0)
        ),
        raw,
    )
    return html.unescape(unescaped)
