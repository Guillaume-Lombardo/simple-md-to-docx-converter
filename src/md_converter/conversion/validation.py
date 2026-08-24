"""Pre-engine validation for the fixed Markdown dialect."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from md_converter.conversion.errors import validation_error

PANDOC_READER = (
    "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
)
_REMOTE_RESOURCE = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*(?::|%3a)|//|%2f%2f)")


class _ResourceParser(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        """Tokenize every destination so validation, not the parser, decides policy."""

        return True


_MARKDOWN = (
    _ResourceParser("commonmark", {"html": True})
    .use(front_matter_plugin)
    .use(footnote_plugin)
)
_METADATA_MARKDOWN = _ResourceParser("commonmark", {"html": True}).use(footnote_plugin)


@dataclass(frozen=True)
class ApprovedMarkdown:
    """Markdown that passed all T07 pre-Pandoc checks."""

    text: str


def _walk(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def _metadata_scalars(metadata: str) -> Iterator[str]:
    try:
        root = yaml.compose(metadata, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise validation_error(
            "Markdown input contains invalid YAML metadata."
        ) from None
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


def _validate_tokens(tokens: tuple[Token, ...]) -> None:
    if any(token.type in {"html_block", "html_inline"} for token in tokens):
        raise validation_error("Markdown input contains raw HTML.")
    for token in tokens:
        if token.type in {"code_block", "code_inline", "fence"}:
            continue
        resource = token.attrGet("src") or token.attrGet("href")
        if isinstance(resource, str) and _REMOTE_RESOURCE.search(resource):
            raise validation_error("Markdown input contains a remote resource.")


def validate_markdown(markdown: str) -> ApprovedMarkdown:
    """Reject forbidden constructs before any document engine can be invoked."""

    if not isinstance(markdown, str) or not markdown.strip():
        raise validation_error("Markdown input must not be empty.")
    tokens = tuple(_walk(_MARKDOWN.parse(markdown)))
    _validate_tokens(tokens)
    for metadata in (token.content for token in tokens if token.type == "front_matter"):
        for scalar in _metadata_scalars(metadata):
            _validate_tokens(tuple(_walk(_METADATA_MARKDOWN.parse(scalar))))
    return ApprovedMarkdown(markdown)
