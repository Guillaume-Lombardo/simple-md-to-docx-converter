"""Markdown image-reference discovery and path resolution shared by the service
and the CLI packaging client.

This module is the single source of truth for which image destinations a Markdown
document contains and how a local destination resolves inside a package. It
depends only on the parser libraries of the ``packaging`` extra and never imports
image engines, so the remote CLI can reuse it without the server installation.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from markweave.conversion.errors import validation_error

INVALID_IMAGE_PATH = "Markdown image path is invalid."
ESCAPING_IMAGE_PATH = "Markdown image path escapes the archive."
MISSING_IMAGE = "Markdown image is missing or unapproved."
INVALID_METADATA = "Markdown input contains invalid YAML metadata."

REMOTE_RESOURCE = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*(?::|%3a)|//|%2f%2f)")
_MAX_URL_DECODE_PASSES = 2


class _ResourceParser(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        """Tokenize every destination so validation, not the parser, decides policy."""

        return True


MARKDOWN = (
    _ResourceParser("commonmark", {"html": True})
    .use(front_matter_plugin)
    .use(footnote_plugin)
)
METADATA_MARKDOWN = _ResourceParser("commonmark", {"html": True}).use(footnote_plugin)


def walk(tokens: list[Token]) -> Iterator[Token]:
    """Yield every token, including inline children, in document order."""

    for token in tokens:
        yield token
        if token.children:
            yield from walk(token.children)


def metadata_scalars(metadata: str) -> Iterator[str]:
    """Yield every YAML scalar value of one front-matter block."""

    try:
        root = yaml.compose(metadata, Loader=yaml.SafeLoader)
    except yaml.YAMLError, RecursionError:
        raise validation_error(INVALID_METADATA) from None
    if root is None:
        return
    pending: list[Node] = [root]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        identity = id(node)
        if identity in seen:
            continue
        seen.add(identity)
        if isinstance(node, ScalarNode):
            yield node.value
        elif isinstance(node, SequenceNode):
            pending.extend(node.value)
        elif isinstance(node, MappingNode):
            for key, value in node.value:
                pending.extend((key, value))


def image_destinations(markdown: str) -> tuple[str, ...]:
    """Return every image ``src`` the service validates, including metadata images.

    Destinations are returned as the parser normalizes them, before any local or
    remote policy decision.
    """

    tokens = tuple(walk(MARKDOWN.parse(markdown)))
    destinations = [_image_source(token) for token in tokens if token.type == "image"]
    for metadata in (token.content for token in tokens if token.type == "front_matter"):
        for scalar in metadata_scalars(metadata):
            destinations.extend(
                _image_source(token)
                for token in walk(METADATA_MARKDOWN.parse(scalar))
                if token.type == "image"
            )
    return tuple(destinations)


def decoded_destination_variants(resource: str) -> tuple[str, ...]:
    """Return a destination and up to two percent-decoded variants."""

    variants = [resource]
    for _ in range(_MAX_URL_DECODE_PASSES):
        decoded = unquote(variants[-1], encoding="utf-8", errors="replace")
        if decoded == variants[-1]:
            break
        variants.append(decoded)
    return tuple(variants)


def is_remote_destination(resource: str) -> bool:
    """Whether a destination names a scheme or network location."""

    return any(
        REMOTE_RESOURCE.search(value)
        for value in decoded_destination_variants(resource)
    )


def decoded_local_path(resource: str) -> PurePosixPath:
    """Decode one local image destination or raise the stable invalid-path error."""

    try:
        parsed = urlsplit(resource)
    except ValueError:
        raise validation_error(INVALID_IMAGE_PATH) from None
    if parsed.query or parsed.fragment:
        raise validation_error(INVALID_IMAGE_PATH)
    if re.search(r"(?i)%(?:2e|2f|5c)", resource):
        raise validation_error(INVALID_IMAGE_PATH)
    try:
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise validation_error(INVALID_IMAGE_PATH) from None
    if unquote(decoded, encoding="utf-8", errors="replace") != decoded:
        raise validation_error(INVALID_IMAGE_PATH)
    if (
        not decoded
        or "\0" in decoded
        or "\\" in decoded
        or decoded.startswith("/")
        or "?" in decoded
        or "#" in decoded
    ):
        raise validation_error(INVALID_IMAGE_PATH)
    relative = PurePosixPath(decoded)
    if relative.parts and ":" in relative.parts[0]:
        raise validation_error(INVALID_IMAGE_PATH)
    return relative


def resolve_local_image_path(resource: str, entrypoint: PurePosixPath) -> PurePosixPath:
    """Resolve a local destination relative to the package entrypoint.

    Raises the stable invalid or escaping error. The result may be empty when the
    destination names the entrypoint's own directory.
    """

    relative = decoded_local_path(resource)
    resolved: list[str] = list(entrypoint.parent.parts)
    if resolved == ["."]:
        resolved.clear()
    for part in relative.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise validation_error(ESCAPING_IMAGE_PATH)
            resolved.pop()
            continue
        resolved.append(part)
    return PurePosixPath(*resolved)


def _image_source(token: Token) -> str:
    source = token.attrGet("src")
    return source if isinstance(source, str) else ""
