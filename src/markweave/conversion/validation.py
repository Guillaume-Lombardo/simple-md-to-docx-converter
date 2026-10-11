"""Pre-engine validation for the fixed Markdown dialect."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from markdown_it.token import Token

from markweave.conversion import image_references as _references
from markweave.conversion.archive import ApprovedDocument, ApprovedResource
from markweave.conversion.errors import ConversionError, validation_error
from markweave.conversion.images import (
    SUPPORTED_IMAGE_SUFFIXES,
    ImageLimits,
    validate_normalized_png,
)

PANDOC_READER = (
    "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
)
# The image-reference parser and path rules are shared with the CLI packager.
_MARKDOWN = _references.MARKDOWN
_METADATA_MARKDOWN = _references.METADATA_MARKDOWN
_REMOTE_RESOURCE = _references.REMOTE_RESOURCE
_decoded_destination_variants = _references.decoded_destination_variants
_is_remote_destination = _references.is_remote_destination
_metadata_scalars = _references.metadata_scalars
_walk = _references.walk
_RAW_ATTRIBUTE = re.compile(r"^\{\s*=([^\s{}]+)\s*\}")
_ALLOWED_EXTERNAL_LINK_SCHEMES = frozenset({"http", "https"})
_ASCII_CONTROL_END = 32
_ASCII_DELETE = 127
_MAX_URL_PORT = 65_535


@dataclass(frozen=True)
class ApprovedMarkdown:
    """Markdown that passed all T07 pre-Pandoc checks."""

    text: str
    entrypoint: PurePosixPath = field(default_factory=lambda: PurePosixPath("input.md"))
    resources: tuple[ApprovedResource, ...] = ()
    image_limits: ImageLimits | None = None


def _is_raw_attribute(value: str, *, complete: bool) -> bool:
    match = _RAW_ATTRIBUTE.fullmatch(value) if complete else _RAW_ATTRIBUTE.match(value)
    if match is None:
        return False
    format_name = unicodedata.normalize("NFC", match.group(1))
    return bool(format_name) and all(
        character.isalnum() or unicodedata.category(character).startswith("M")
        for character in format_name
    )


def _resolve_local_image(
    resource: str,
    entrypoint: PurePosixPath,
    approved_paths: frozenset[PurePosixPath],
) -> None:
    path = _references.resolve_local_image_path(resource, entrypoint)
    if path not in approved_paths:
        raise validation_error(_references.MISSING_IMAGE)


def _is_safe_external_link(resource: str) -> bool:
    variants = _decoded_destination_variants(resource)
    if not _REMOTE_RESOURCE.search(resource) or any(
        ord(character) < _ASCII_CONTROL_END or ord(character) == _ASCII_DELETE
        for value in variants
        for character in value
    ):
        return False
    try:
        parsed = urlsplit(resource)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in _ALLOWED_EXTERNAL_LINK_SCHEMES
        and bool(parsed.netloc)
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or 0 <= port <= _MAX_URL_PORT)
        and "%" not in hostname
        and "\\" not in resource
    )


def _is_safe_package_path(path: PurePosixPath) -> bool:
    return (
        isinstance(path, PurePosixPath)
        and bool(path.parts)
        and not (
            path.is_absolute()
            or path.parts in {(), (".",)}
            or ".." in path.parts
            or "\0" in path.as_posix()
            or "\\" in path.as_posix()
            or ":" in path.parts[0]
        )
    )


def _package_paths_are_distinct(paths: tuple[PurePosixPath, ...]) -> bool:
    keys = {unicodedata.normalize("NFC", path.as_posix()).casefold() for path in paths}
    if len(keys) != len(paths):
        return False
    for path in paths:
        parent = path.parent
        while parent.parts not in {(), (".",)}:
            key = unicodedata.normalize("NFC", parent.as_posix()).casefold()
            if key in keys:
                return False
            parent = parent.parent
    return True


def _validate_tokens(
    tokens: tuple[Token, ...],
    *,
    entrypoint: PurePosixPath,
    approved_paths: frozenset[PurePosixPath],
) -> None:
    for token in tokens:
        if token.type == "fence" and _is_raw_attribute(
            token.info.strip(), complete=True
        ):
            raise validation_error("Markdown input contains a raw attribute.")
        if token.type != "inline" or not token.children:
            continue
        for current, following in zip(token.children, token.children[1:], strict=False):
            if (
                current.type == "code_inline"
                and following.type == "text"
                and following.info != "escape"
                and _is_raw_attribute(following.content, complete=False)
            ):
                raise validation_error("Markdown input contains a raw attribute.")
    if any(token.type in {"html_block", "html_inline"} for token in tokens):
        raise validation_error("Markdown input contains raw HTML.")
    for token in tokens:
        if token.type in {"code_block", "code_inline", "fence"}:
            continue
        resource = token.attrGet("src") or token.attrGet("href")
        if token.type == "image":
            if isinstance(resource, str) and _is_remote_destination(resource):
                raise validation_error("Markdown input contains a remote resource.")
            if not isinstance(resource, str) or not approved_paths:
                raise validation_error("Markdown input contains an unapproved image.")
            _resolve_local_image(resource, entrypoint, approved_paths)
        elif (
            isinstance(resource, str)
            and _is_remote_destination(resource)
            and not _is_safe_external_link(resource)
        ):
            raise validation_error("Markdown input contains a remote resource.")


def validate_markdown(markdown: str) -> ApprovedMarkdown:
    """Reject forbidden constructs before any document engine can be invoked."""

    if not isinstance(markdown, str) or not markdown.strip():
        raise validation_error("Markdown input must not be empty.")
    tokens = tuple(_walk(_MARKDOWN.parse(markdown)))
    _validate_tokens(
        tokens,
        entrypoint=PurePosixPath("input.md"),
        approved_paths=frozenset(),
    )
    for metadata in (token.content for token in tokens if token.type == "front_matter"):
        for scalar in _metadata_scalars(metadata):
            _validate_tokens(
                tuple(_walk(_METADATA_MARKDOWN.parse(scalar))),
                entrypoint=PurePosixPath("input.md"),
                approved_paths=frozenset(),
            )
    return ApprovedMarkdown(markdown)


def validate_document(document: ApprovedDocument) -> ApprovedMarkdown:
    """Bind every local image reference to one normalized archive resource."""

    if not isinstance(document, ApprovedDocument):
        raise validation_error("Document package is invalid.")
    if (
        type(document.markdown) is not str
        or not _is_safe_package_path(document.entrypoint)
        or document.entrypoint.suffix.casefold() != ".md"
        or not isinstance(document.resources, tuple)
    ):
        raise validation_error("Document package is invalid.")
    if any(
        not isinstance(resource, ApprovedResource)
        or not _is_safe_package_path(resource.path)
        or resource.path.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES
        or resource.media_type != "image/png"
        or type(resource.content) is not bytes
        for resource in document.resources
    ):
        raise validation_error("Document package is invalid.")
    if document.image_limits is not None and not isinstance(
        document.image_limits, ImageLimits
    ):
        raise validation_error("Document package is invalid.")
    if document.resources and document.image_limits is None:
        raise validation_error("Document package is invalid.")
    if document.image_limits is not None:
        try:
            for resource in document.resources:
                validate_normalized_png(resource.content, document.image_limits)
        except ConversionError:
            raise validation_error("Document package is invalid.") from None
    approved_paths = frozenset(resource.path for resource in document.resources)
    package_paths = (document.entrypoint, *approved_paths)
    if len(approved_paths) != len(
        document.resources
    ) or not _package_paths_are_distinct(package_paths):
        raise validation_error("Document package is invalid.")
    if not document.markdown.strip():
        raise validation_error("Markdown input must not be empty.")
    tokens = tuple(_walk(_MARKDOWN.parse(document.markdown)))
    _validate_tokens(
        tokens,
        entrypoint=document.entrypoint,
        approved_paths=approved_paths,
    )
    for metadata in (token.content for token in tokens if token.type == "front_matter"):
        for scalar in _metadata_scalars(metadata):
            _validate_tokens(
                tuple(_walk(_METADATA_MARKDOWN.parse(scalar))),
                entrypoint=document.entrypoint,
                approved_paths=approved_paths,
            )
    return ApprovedMarkdown(
        document.markdown,
        entrypoint=document.entrypoint,
        resources=document.resources,
        image_limits=document.image_limits,
    )
