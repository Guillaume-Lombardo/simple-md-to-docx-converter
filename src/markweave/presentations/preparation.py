"""Prepare a documented Markdown/Marp subset without executing input directives."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

from markweave.conversion.archive import (
    ApprovedDocument,
    ArchiveLimits,
    prepare_archive,
)
from markweave.conversion.errors import validation_error
from markweave.conversion.images import ImageLimits
from markweave.conversion.validation import validate_document
from markweave.presentations.models import PresentationDialect, PresentationOptions

_PARSER = MarkdownIt("commonmark", {"html": True}).use(front_matter_plugin)
_CROWDED_SLIDE_WORDS = 150
_METADATA = frozenset(
    {"title", "author", "date", "subtitle", "description", "keywords"}
)
_DIRECTIVE = re.compile(r"^_?([A-Za-z][\w-]*)\s*:")
_FENCE = re.compile(r"^\s*(:{3,})\s*(.*?)\s*$")
_ALLOWED_DIV = re.compile(
    r"^(?:notes|columns|column|\{\.(?:notes|columns|column)(?:\s+width=\"?(?:100|[1-9][0-9]?)%\"?)?\})$"
)


@dataclass(frozen=True, slots=True)
class PresentationPlan:
    document: ApprovedDocument
    options: PresentationOptions
    titles: tuple[str, ...]
    warnings: tuple[str, ...]
    explicit_breaks: bool


def prepare_source(
    source: bytes,
    *,
    archive: bool,
    archive_limits: ArchiveLimits,
    image_limits: ImageLimits,
) -> ApprovedDocument:
    if archive:
        return prepare_archive(source, archive_limits, image_limits)
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        raise validation_error("Markdown input is not valid UTF-8.") from None
    return ApprovedDocument(text, PurePosixPath("document.md"), ())


def plan_presentation(
    document: ApprovedDocument, options: PresentationOptions
) -> PresentationPlan:
    """Normalize only known syntax, preserving code and warning about ignored styling."""
    lines = document.markdown.splitlines(keepends=True)
    tokens = _PARSER.parse(document.markdown)
    warnings: list[str] = []
    edits: list[tuple[int, int, str]] = []
    dialect = options.dialect
    dialect, edits = _metadata_edits(tokens, dialect, warnings)
    if dialect is PresentationDialect.AUTO:
        dialect = PresentationDialect.MARKDOWN
    for token in tokens:
        if token.type != "html_block" or token.map is None:
            continue
        comment = token.content.strip()
        if dialect is not PresentationDialect.MARP or not (
            comment.startswith("<!--") and comment.endswith("-->")
        ):
            continue  # Existing raw-HTML validation rejects everything else.
        body = comment[4:-3].strip()
        directive = _DIRECTIVE.match(body)
        if directive and directive.group(1) != "notes":
            warnings.append(
                "Unsupported Marp directive is not applied; the PowerPoint template controls appearance."
            )
            replacement = ""
        else:
            note = body[directive.end() :].strip() if directive else body
            replacement = "::: notes\n" + note + "\n:::\n"
        edits.append((token.map[0], token.map[1], replacement))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    normalized = "".join(lines)
    _validate_divs(normalized)
    prepared = replace(document, markdown=normalized)
    validate_document(prepared)
    outline_tokens = _visible_tokens(normalized)
    explicit = any(t.type == "hr" and t.level == 0 for t in outline_tokens)
    titles, crowded = _outline(
        outline_tokens, normalized, options.slide_level, explicit
    )
    if crowded:
        warnings.append(
            "Some slides contain substantial text. Check their layout in PowerPoint and split them if necessary; no text was removed."
        )
    warnings.append(
        "Layout overflow is not measured automatically. Review the generated slides before presenting."
    )
    return PresentationPlan(
        prepared,
        replace(options, dialect=dialect),
        titles,
        tuple(dict.fromkeys(warnings)),
        explicit,
    )


def _metadata_edits(
    tokens: list, dialect: PresentationDialect, warnings: list[str]
) -> tuple[PresentationDialect, list[tuple[int, int, str]]]:
    edits: list[tuple[int, int, str]] = []
    for token in tokens:
        if token.type != "front_matter" or token.map is None:
            continue
        try:
            metadata = yaml.safe_load(token.content) or {}
        except yaml.YAMLError, RecursionError:
            raise validation_error("Presentation metadata is invalid.") from None
        if not isinstance(metadata, dict) or any(
            not isinstance(k, str) for k in metadata
        ):
            raise validation_error("Presentation metadata must be a mapping.")
        if dialect is PresentationDialect.AUTO:
            dialect = (
                PresentationDialect.MARP
                if metadata.get("marp") is True
                else PresentationDialect.MARKDOWN
            )
        accepted = {}
        for key, value in metadata.items():
            if key == "marp" and dialect is PresentationDialect.MARP:
                if value is not True:
                    raise validation_error("Marp metadata must declare marp: true.")
            elif key in _METADATA and isinstance(value, (str, int, float)):
                accepted[key] = str(value)
            else:
                warnings.append(
                    "Unsupported metadata or styling is not applied; the PowerPoint template controls appearance."
                )
        replacement = (
            "---\n"
            + yaml.safe_dump(accepted, allow_unicode=True, sort_keys=True)
            + "---\n"
            if accepted
            else ""
        )
        edits.append((token.map[0], token.map[1], replacement))
    return dialect, edits


def _validate_divs(text: str) -> None:
    excluded: set[int] = set()
    for token in _PARSER.parse(text):
        if token.type in {"fence", "code_block", "front_matter"} and token.map:
            excluded.update(range(*token.map))
    for index, line in enumerate(text.splitlines()):
        if index in excluded:
            continue
        match = _FENCE.match(line)
        if match and match[2] and not _ALLOWED_DIV.fullmatch(match[2]):
            raise validation_error(
                "Only notes, columns, and column presentation blocks are supported."
            )


def _outline(
    tokens: list, text: str, slide_level: int, explicit: bool
) -> tuple[tuple[str, ...], bool]:
    boundaries: list[tuple[int, str]] = [(0, "Untitled slide")]
    for index, token in enumerate(tokens):
        if token.map is None or token.level != 0:
            continue
        if explicit and token.type == "hr":
            boundaries.append((token.map[1], "Untitled slide"))
        elif token.type == "heading_open":
            title = tokens[index + 1].content[:200]
            if not explicit and int(token.tag[1:]) <= slide_level:
                if boundaries[-1][0] == 0 and boundaries[-1][1] == "Untitled slide":
                    boundaries[-1] = (token.map[0], title)
                else:
                    boundaries.append((token.map[0], title))
            elif boundaries[-1][1] == "Untitled slide":
                boundaries[-1] = (boundaries[-1][0], title)
    lines = text.splitlines()
    crowded = any(
        len(" ".join(lines[start:end]).split()) > _CROWDED_SLIDE_WORDS
        for (start, _), (end, _) in zip(
            boundaries, [*boundaries[1:], (len(lines), "")], strict=True
        )
    )
    titles = tuple(title for _, title in boundaries)
    metadata = next((t for t in tokens if t.type == "front_matter"), None)
    if metadata is not None:
        title = (yaml.safe_load(metadata.content) or {}).get("title")
        if title:
            titles = (str(title), *titles)
    return titles, crowded


def _visible_tokens(text: str) -> list[Token]:
    """Do not mistake headings, rules or div syntax inside notes/code for slides."""
    tokens = _PARSER.parse(text)
    code_lines: set[int] = set()
    for token in tokens:
        if token.type in {"fence", "code_block", "front_matter"} and token.map:
            code_lines.update(range(*token.map))
    notes: set[int] = set()
    in_notes = False
    for index, line in enumerate(text.splitlines()):
        match = None if index in code_lines else _FENCE.match(line)
        if match and match[2] in {"notes", "{.notes}"}:
            in_notes = True
        elif match and not match[2] and in_notes:
            in_notes = False
        elif in_notes:
            notes.add(index)
    return [token for token in tokens if token.map is None or token.map[0] not in notes]
