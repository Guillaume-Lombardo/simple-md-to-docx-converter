"""Fail-closed compatibility boundary for firecrawl-anydoc 0.2.4.

This module is the only Markweave code allowed to depend on anydoc's concrete
``Document`` shape or to mirror its private Markdown renderer. It consumes one
already parsed document and injects normalized local image paths at the image
nodes' source positions. It must be removed when anydoc exposes a supported
asset-aware renderer hook.

The renderer behavior is derived from firecrawl/anydoc commit
42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c under the MIT license retained in
``ANYDOC_COMPAT_LICENSE.txt``. The exact mirrored surfaces are inventoried in
``UPSTREAM_RENDERER_SURFACES`` below.
"""

from __future__ import annotations

import importlib.metadata
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, Never, cast
from urllib.parse import unquote, urlsplit

import anydoc

from markweave.reversions.assets import AssetSource
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.formats import FormatAdmission, FormatFamily, admit_format

PINNED_ANYDOC_VERSION = "0.2.4"
UPSTREAM_ANYDOC_COMMIT = "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
_ALLOWED_HYPERLINK_SCHEMES = frozenset({"http", "https"})
_MAX_URL_DECODE_PASSES = 2
_MAX_URL_PORT = 65_535
UPSTREAM_RENDERER_SURFACES = (
    "src/render/markdown/mod.rs:document_to_markdown",
    "src/render/markdown/mod.rs:number_notes",
    "src/render/markdown/mod.rs:collect_note_refs",
    "src/render/markdown/mod.rs:render_block",
    "src/render/markdown/mod.rs:render_list",
    "src/render/markdown/mod.rs:trim_paragraph",
    "src/render/markdown/anchors.rs:resolve_anchors",
    "src/render/markdown/anchors.rs:gfm_slug",
    "src/render/markdown/anchors.rs:sanitize_id",
    "src/render/markdown/escape.rs:escape_text",
    "src/render/markdown/escape.rs:format_url",
    "src/render/markdown/escape.rs:backtick_fence",
    "src/render/markdown/inline.rs:normalize",
    "src/render/markdown/inline.rs:render_inlines",
    "src/render/markdown/inline.rs:render_link",
    "src/render/markdown/inline.rs:render_image",
    "src/render/markdown/inline.rs:render_text_run",
    "src/render/markdown/inline.rs:push_math_span",
    "src/render/markdown/inline.rs:push_code_span",
    "src/render/markdown/table.rs:render_table",
    "src/render/markdown/table.rs:render_cell",
)

_BLOCK_FIELDS = {
    "anchor",
    "blocks",
    "content",
    "kind",
    "lang",
    "level",
    "list",
    "table",
    "text",
}
_INLINE_FIELDS = {
    "alt",
    "anchor",
    "checked",
    "content",
    "kind",
    "note_id",
    "source",
    "style",
    "target",
    "text",
}
_MODEL_FIELDS: tuple[tuple[type[Any], frozenset[str]], ...] = (
    (anydoc.Document, frozenset({"assets", "blocks", "notes"})),
    (anydoc.Block, frozenset(_BLOCK_FIELDS)),
    (anydoc.Inline, frozenset(_INLINE_FIELDS)),
    (anydoc.Style, frozenset({"bold", "code", "italic", "strike"})),
    (anydoc.LinkTarget, frozenset({"kind", "value"})),
    (anydoc.ImageSource, frozenset({"asset_id", "kind", "url"})),
    (anydoc.List, frozenset({"items", "marker", "start"})),
    (anydoc.ListItem, frozenset({"blocks", "marker_label"})),
    (anydoc.Table, frozenset({"grid", "header_rows", "kind"})),
    (
        anydoc.CellSlot,
        frozenset({"cell", "kind", "origin_col", "origin_row"}),
    ),
    (anydoc.Cell, frozenset({"blocks", "col_span", "row_span"})),
    (anydoc.Note, frozenset({"blocks", "id", "kind"})),
    (anydoc.Asset, frozenset({"data", "id", "media_type", "origin_part"})),
)
_BLOCK_KINDS = {
    "block_quote",
    "code_block",
    "heading",
    "list",
    "math",
    "paragraph",
    "rule",
    "table",
}
_INLINE_KINDS = {
    "anchor",
    "checkbox",
    "image",
    "line_break",
    "link",
    "math",
    "note_ref",
    "text",
}
_MARKERS = {
    "bullet",
    "decimal",
    "lower_alpha",
    "lower_roman",
    "upper_alpha",
    "upper_roman",
}
_SAFE_ASSET_PATH = re.compile(r"assets/image-[0-9]{4,}\.png")
_MAX_U8 = 255
_MAX_ROMAN = 3999
_MIN_BRIDGE_RUNS = 2


@dataclass(frozen=True)
class _TextRun:
    text: str
    style: tuple[bool, bool, bool, bool]


@dataclass(frozen=True)
class _NodeRun:
    node: Any


_Run = _TextRun | _NodeRun


@dataclass(frozen=True)
class _RenderContext:
    note_numbers: dict[str, int]
    fragments: dict[str, str]
    html_ids: dict[str, str]
    image_paths: tuple[PurePosixPath | None, ...]
    image_index: list[int]


@dataclass(frozen=True)
class _EscapeOptions:
    at_line_start: bool = False
    styled: bool = False
    trailing_active: bool = False
    trailing_nonspace: bool = False
    trailing_delims: frozenset[str] = frozenset()
    in_label: bool = False


@dataclass(frozen=True, slots=True)
class ParsedSource:
    """One child-local admission and its single native conversion result."""

    admission: FormatAdmission
    document: anydoc.Document | None
    markdown: str | None

    def __post_init__(self) -> None:
        is_pdf = self.admission.family is FormatFamily.PDF
        if is_pdf != (self.markdown is not None) or is_pdf == (
            self.document is not None
        ):
            _compatibility_error()


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """Rendered Markdown plus source occurrences retained in the output."""

    markdown: str
    retained_occurrences: tuple[int, ...]


def _compatibility_error() -> None:
    reject(ReverseErrorCategory.MALFORMED)


def _require_type(value: Any, expected: type[Any]) -> None:
    if type(value) is not expected:
        _compatibility_error()


def _require_optional_type(value: Any, expected: type[Any]) -> None:
    if value is not None and type(value) is not expected:
        _compatibility_error()


def _check_version_and_surface() -> None:
    try:
        version = importlib.metadata.version("firecrawl-anydoc")
    except importlib.metadata.PackageNotFoundError:
        _compatibility_error()
    if version != PINNED_ANYDOC_VERSION:
        _compatibility_error()
    for model_type, expected_fields in _MODEL_FIELDS:
        if model_type.__module__ != "anydoc":
            _compatibility_error()
        actual_fields = frozenset(
            name for name in vars(model_type) if not name.startswith("__")
        )
        if actual_fields != expected_fields:
            _compatibility_error()


def _validate_style(style: Any) -> None:
    _require_type(style, anydoc.Style)
    for name in ("bold", "italic", "strike", "code"):
        _require_type(getattr(style, name), bool)


def _decoded_destination_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    for _ in range(_MAX_URL_DECODE_PASSES):
        decoded = unquote(variants[-1], encoding="utf-8", errors="replace")
        if decoded == variants[-1]:
            break
        variants.append(decoded)
    return tuple(variants)


def _is_safe_hyperlink(value: str) -> bool:
    variants = _decoded_destination_variants(value)
    if any(
        character.isspace() or unicodedata.category(character) == "Cc"
        for variant in variants
        for character in variant
    ):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() in _ALLOWED_HYPERLINK_SCHEMES
        and bool(parsed.netloc)
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and (port is None or 0 <= port <= _MAX_URL_PORT)
        and "%" not in hostname
        and "\\" not in value
    )


def _validate_inlines(inlines: Any, asset_count: int) -> None:  # noqa: PLR0912, PLR0915
    _require_type(inlines, list)
    for inline in inlines:
        _require_type(inline, anydoc.Inline)
        _require_type(inline.kind, str)
        if inline.kind not in _INLINE_KINDS:
            _compatibility_error()
        if inline.kind == "text":
            _require_type(inline.text, str)
            _validate_style(inline.style)
            expected = {"text", "style"}
        elif inline.kind == "link":
            _validate_inlines(inline.content, asset_count)
            _require_type(inline.target, anydoc.LinkTarget)
            _require_type(inline.target.kind, str)
            _require_type(inline.target.value, str)
            if inline.target.kind not in {"anchor", "external", "relative"}:
                _compatibility_error()
            if inline.target.kind != "anchor" and not _is_safe_hyperlink(
                inline.target.value
            ):
                reject(ReverseErrorCategory.MALFORMED)
            expected = {"content", "target"}
        elif inline.kind == "image":
            _require_type(inline.alt, str)
            _require_type(inline.source, anydoc.ImageSource)
            _require_type(inline.source.kind, str)
            if inline.source.kind == "asset":
                _require_type(inline.source.asset_id, int)
                if not 0 <= inline.source.asset_id < asset_count:
                    _compatibility_error()
                expected_source = {"asset_id", "kind"}
            elif inline.source.kind == "external":
                _require_type(inline.source.url, str)
                expected_source = {"kind", "url"}
            elif inline.source.kind == "unavailable":
                expected_source = {"kind"}
            else:
                _compatibility_error()
            _require_empty_payload(
                inline.source, {"asset_id", "kind", "url"}, expected_source
            )
            expected = {"alt", "source"}
        elif inline.kind == "anchor":
            _require_type(inline.anchor, str)
            expected = {"anchor"}
        elif inline.kind == "note_ref":
            _require_type(inline.note_id, str)
            expected = {"note_id"}
        elif inline.kind == "math":
            _require_type(inline.text, str)
            expected = {"text"}
        elif inline.kind == "checkbox":
            _require_type(inline.checked, bool)
            expected = {"checked"}
        else:
            expected = set()
        _require_empty_payload(inline, _INLINE_FIELDS, expected | {"kind"})


def _require_empty_payload(
    value: Any, all_fields: Iterable[str], populated_fields: set[str]
) -> None:
    for field in set(all_fields) - populated_fields:
        if getattr(value, field) is not None:
            _compatibility_error()


def _validate_blocks(blocks: Any, asset_count: int) -> None:
    _require_type(blocks, list)
    for block in blocks:
        _require_type(block, anydoc.Block)
        _require_type(block.kind, str)
        if block.kind not in _BLOCK_KINDS:
            _compatibility_error()
        expected: set[str] = {"kind"}
        if block.kind == "heading":
            _require_type(block.level, int)
            if not 0 <= block.level <= _MAX_U8:
                _compatibility_error()
            _require_optional_type(block.anchor, str)
            _validate_inlines(block.content, asset_count)
            expected |= {"anchor", "content", "level"}
        elif block.kind == "paragraph":
            _validate_inlines(block.content, asset_count)
            expected.add("content")
        elif block.kind == "list":
            _validate_list(block.list, asset_count)
            expected.add("list")
        elif block.kind == "table":
            _validate_table(block.table, asset_count)
            expected.add("table")
        elif block.kind == "block_quote":
            _validate_blocks(block.blocks, asset_count)
            expected.add("blocks")
        elif block.kind == "code_block":
            _require_optional_type(block.lang, str)
            _require_type(block.text, str)
            expected |= {"lang", "text"}
        elif block.kind == "math":
            _require_type(block.text, str)
            expected.add("text")
        _require_empty_payload(block, _BLOCK_FIELDS, expected)


def _validate_list(value: Any, asset_count: int) -> None:
    _require_type(value, anydoc.List)
    _require_type(value.marker, str)
    _require_type(value.start, int)
    _require_type(value.items, list)
    if value.marker not in _MARKERS or value.start < 0:
        _compatibility_error()
    for item in value.items:
        _require_type(item, anydoc.ListItem)
        _require_optional_type(item.marker_label, str)
        _validate_blocks(item.blocks, asset_count)


def _validate_table(value: Any, asset_count: int) -> None:
    _require_type(value, anydoc.Table)
    _require_type(value.grid, list)
    _require_type(value.header_rows, int)
    _require_type(value.kind, str)
    if value.header_rows < 0 or value.kind not in {"data", "layout"}:
        _compatibility_error()
    for row_index, row in enumerate(value.grid):
        _require_type(row, list)
        for column_index, slot in enumerate(row):
            _require_type(slot, anydoc.CellSlot)
            _require_type(slot.kind, str)
            if slot.kind == "origin":
                _require_type(slot.cell, anydoc.Cell)
                _require_type(slot.cell.col_span, int)
                _require_type(slot.cell.row_span, int)
                if slot.cell.col_span < 1 or slot.cell.row_span < 1:
                    _compatibility_error()
                _validate_blocks(slot.cell.blocks, asset_count)
                if slot.origin_row is not None or slot.origin_col is not None:
                    _compatibility_error()
            elif slot.kind == "covered":
                _require_type(slot.origin_row, int)
                _require_type(slot.origin_col, int)
                if (
                    slot.cell is not None
                    or slot.origin_row < 0
                    or slot.origin_col < 0
                    or slot.origin_row > row_index
                    or (
                        slot.origin_row == row_index and slot.origin_col >= column_index
                    )
                ):
                    _compatibility_error()
            else:
                _compatibility_error()


def validate_document(document: Any) -> None:
    """Reject an unpinned library surface or an unknown document variant."""

    _check_version_and_surface()
    _require_type(document, anydoc.Document)
    _require_type(document.assets, list)
    for index, asset in enumerate(document.assets):
        _require_type(asset, anydoc.Asset)
        _require_type(asset.id, int)
        _require_type(asset.media_type, str)
        _require_type(asset.origin_part, str)
        _require_type(asset.data, bytes)
        if asset.id != index:
            _compatibility_error()
    _validate_blocks(document.blocks, len(document.assets))
    _require_type(document.notes, list)
    for note in document.notes:
        _require_type(note, anydoc.Note)
        _require_type(note.id, str)
        _require_type(note.kind, str)
        if note.kind not in {"footnote", "endnote"}:
            _compatibility_error()
        _validate_blocks(note.blocks, len(document.assets))


def parse_document(
    data: bytes, format_hint: anydoc.Format | None = None
) -> anydoc.Document:
    """Parse exactly once locally with the pinned native binding and validate its model."""

    _check_version_and_surface()
    _require_type(data, bytes)
    if format_hint not in {
        None,
        "csv",
        "doc",
        "docx",
        "epub",
        "odp",
        "ods",
        "odt",
        "ppt",
        "pptx",
        "rtf",
        "xlsx",
    }:
        reject(ReverseErrorCategory.UNSUPPORTED)
    try:
        document = anydoc.to_document(data, format_hint)
    except anydoc.ConvertError as error:
        _raise_mapped(error)
    validate_document(document)
    return document


def _raise_mapped(error: anydoc.ConvertError) -> Never:
    if isinstance(error, anydoc.UnsupportedError):
        reject(ReverseErrorCategory.UNSUPPORTED)
    if isinstance(error, (anydoc.MalformedError, anydoc.MissingPartError)):
        reject(ReverseErrorCategory.MALFORMED)
    if isinstance(error, anydoc.EncryptedError):
        reject(ReverseErrorCategory.ENCRYPTED)
    if isinstance(error, anydoc.ResourceLimitError):
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    if isinstance(error, anydoc.NeedsOcrError):
        reject(ReverseErrorCategory.NEEDS_OCR)
    reject(ReverseErrorCategory.MALFORMED)


def _is_bounded_csv_text(data: bytes) -> bool:
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return False
    return "\x00" not in text


def parse_source(data: bytes, extension: str) -> ParsedSource:
    """Detect and admit child-local input, then parse or render it exactly once.

    The extension is only an admission hint. Content detection and CSV text validation
    happen inside the credentialless attempt child. PDF uses the pinned local renderer
    with literal OCR rejection; every other admitted format returns one parsed model.
    """

    _check_version_and_surface()
    _require_type(data, bytes)
    try:
        detected = anydoc.format_from_bytes(data)
    except anydoc.ConvertError as error:
        _raise_mapped(error)
    admission = admit_format(
        extension,
        detected,
        csv_text_validated=detected is None and _is_bounded_csv_text(data),
    )
    if admission.family is not FormatFamily.PDF:
        return ParsedSource(
            admission=admission,
            document=parse_document(
                data, cast("anydoc.Format", admission.parser_format)
            ),
            markdown=None,
        )
    try:
        markdown = anydoc.to_markdown_bytes(data, "pdf", ocr="reject")
    except anydoc.ConvertError as error:
        _raise_mapped(error)
    _require_type(markdown, str)
    return ParsedSource(admission=admission, document=None, markdown=markdown)


def _walk_blocks(blocks: Sequence[Any]) -> Iterable[Any]:
    stack = list(reversed(blocks))
    while stack:
        block = stack.pop()
        yield block
        if block.kind == "list":
            for item in reversed(block.list.items):
                stack.extend(reversed(item.blocks))
        elif block.kind == "table":
            for row in reversed(block.table.grid):
                for slot in reversed(row):
                    if slot.kind == "origin":
                        stack.extend(reversed(slot.cell.blocks))
        elif block.kind == "block_quote":
            stack.extend(reversed(block.blocks))


def _walk_inlines(inlines: Sequence[Any]) -> Iterable[Any]:
    for inline in inlines:
        yield inline
        if inline.kind == "link":
            yield from _walk_inlines(inline.content)


def _image_nodes(document: Any) -> Iterable[Any]:
    note_numbers = _number_notes(document)
    ordered_notes = sorted(
        (
            (note_numbers[note.id], note)
            for note in document.notes
            if note.id in note_numbers
        ),
        key=lambda item: item[0],
    )
    rendered_blocks: list[Sequence[Any]] = [document.blocks]
    rendered_blocks.extend(note.blocks for _, note in ordered_notes)
    for blocks in rendered_blocks:
        for block in _walk_blocks(blocks):
            if block.kind in {"heading", "paragraph"}:
                for inline in _walk_inlines(block.content):
                    if inline.kind == "image":
                        yield inline


def extract_asset_sources(document: Any) -> tuple[AssetSource, ...]:
    """Return image occurrences and embedded bytes in source-position order."""

    validate_document(document)
    assets = {asset.id: asset for asset in document.assets}
    result: list[AssetSource] = []
    for inline in _image_nodes(document):
        source = inline.source
        if source.kind == "external":
            reject(ReverseErrorCategory.ASSET_INVALID)
        if source.kind == "asset":
            asset = assets[source.asset_id]
            result.append(
                AssetSource(
                    asset_id=f"anydoc:{asset.id}",
                    source=asset.data,
                    declared_media_type=asset.media_type,
                )
            )
        else:
            result.append(
                AssetSource(
                    asset_id=f"anydoc:unavailable:{len(result)}",
                    source=None,
                    declared_media_type=None,
                )
            )
    return tuple(result)


def render_document(
    document: Any, image_paths: Sequence[PurePosixPath | None] = ()
) -> str:
    """Render one validated parsed document, injecting one path per image occurrence."""

    return render_document_result(document, image_paths).markdown


def render_document_result(
    document: Any, image_paths: Sequence[PurePosixPath | None] = ()
) -> RenderedDocument:
    """Render and identify the image occurrences retained in emitted Markdown."""

    validate_document(document)
    paths = tuple(image_paths)
    occurrences = tuple(_image_nodes(document))
    if len(paths) != len(occurrences):
        _compatibility_error()
    for path in paths:
        if path is not None and (
            type(path) is not PurePosixPath
            or _SAFE_ASSET_PATH.fullmatch(path.as_posix()) is None
            or path.is_absolute()
            or ".." in path.parts
        ):
            _compatibility_error()
    if any(inline.source.kind == "external" for inline in occurrences):
        reject(ReverseErrorCategory.ASSET_INVALID)

    note_numbers = _number_notes(document)
    fragments, html_ids = _resolve_anchors(document)
    context = _RenderContext(note_numbers, fragments, html_ids, paths, [0])
    parts = [
        rendered
        for block in document.blocks
        if (rendered := _render_block(block, context)) is not None
    ]
    retained_occurrences = list(range(context.image_index[0]))
    rendered_notes: set[int] = set()
    ordered_notes = sorted(
        (
            (note_numbers[note.id], note)
            for note in document.notes
            if note.id in note_numbers
        ),
        key=lambda item: item[0],
    )
    for number, note in ordered_notes:
        first_occurrence = context.image_index[0]
        body = _render_blocks(note.blocks, context)
        if not body or number in rendered_notes:
            continue
        rendered_notes.add(number)
        retained_occurrences.extend(range(first_occurrence, context.image_index[0]))
        lines = body.splitlines()
        definition = f"[^{number}]: {lines[0]}"
        definition += "".join(f"\n{'    ' if line else ''}{line}" for line in lines[1:])
        parts.append(definition)
    if context.image_index[0] != len(paths):
        _compatibility_error()
    output = "\n\n".join(parts)
    markdown = output + "\n" if output else ""
    return RenderedDocument(markdown, tuple(retained_occurrences))


def _inlines_are_empty(inlines: Sequence[Any]) -> bool:
    for inline in inlines:
        if inline.kind == "text" and inline.text.strip():
            return False
        if inline.kind == "link" and (
            inline.target.value or not _inlines_are_empty(inline.content)
        ):
            return False
        if inline.kind in {"image", "note_ref", "checkbox"}:
            return False
        if inline.kind == "math" and inline.text.strip():
            return False
    return True


def _number_notes(document: Any) -> dict[str, int]:
    valid: dict[str, Any] = {}
    for note in document.notes:
        if not all(
            block.kind == "paragraph" and _inlines_are_empty(block.content)
            for block in note.blocks
        ):
            valid.setdefault(note.id, note)
    order: list[str] = []
    seen: set[str] = set()

    def collect(blocks: Sequence[Any]) -> None:
        for block in blocks:
            if block.kind in {"paragraph", "heading"}:
                for inline in _walk_inlines(block.content):
                    if (
                        inline.kind == "note_ref"
                        and inline.note_id in valid
                        and inline.note_id not in seen
                    ):
                        seen.add(inline.note_id)
                        order.append(inline.note_id)
                        collect(valid[inline.note_id].blocks)
            elif block.kind == "list":
                for item in block.list.items:
                    collect(item.blocks)
            elif block.kind == "table":
                for row in block.table.grid:
                    for slot in row:
                        if slot.kind == "origin":
                            collect(slot.cell.blocks)
            elif block.kind == "block_quote":
                collect(block.blocks)

    collect(document.blocks)
    for note in document.notes:
        if note.id in valid and note.id not in seen:
            seen.add(note.id)
            order.append(note.id)
    return {note_id: index for index, note_id in enumerate(order, start=1)}


def _plain_text(inlines: Sequence[Any]) -> str:
    output: list[str] = []
    for inline in inlines:
        if inline.kind == "text":
            output.append(inline.text)
        elif inline.kind == "link":
            output.append(_plain_text(inline.content))
        elif inline.kind == "image":
            output.append(inline.alt)
        elif inline.kind == "math":
            output.append(inline.text)
        elif inline.kind == "checkbox":
            output.append("[x]" if inline.checked else "[ ]")
        elif inline.kind == "line_break":
            output.append("\n")
    return "".join(output)


def _gfm_slug(text: str) -> str:
    output: list[str] = []
    for char in text.strip().lower():
        if char == " ":
            output.append("-")
        elif (
            char == "-"
            or char.isalnum()
            or unicodedata.category(char) in {"Mn", "Mc", "Pc"}
        ):
            output.append(char)
    return "".join(output) or "section"


def _sanitize_id(value: str) -> str:
    output: list[str] = []
    previous_dash = False
    for char in value:
        lowered = char.lower() if char.isascii() else "-"
        mapped = (
            lowered
            if lowered.isascii() and (lowered.isalnum() or lowered in "_-")
            else "-"
        )
        if mapped == "-" and previous_dash:
            continue
        previous_dash = mapped == "-"
        output.append(mapped)
    return "".join(output).strip("-") or "anchor"


def _resolve_anchors(  # noqa: PLR0912
    document: Any,
) -> tuple[dict[str, str], dict[str, str]]:
    linked: set[str] = set()
    all_blocks = list(_walk_blocks(document.blocks))
    for note in document.notes:
        all_blocks.extend(_walk_blocks(note.blocks))
    for block in all_blocks:
        if block.kind in {"heading", "paragraph"}:
            for inline in _walk_inlines(block.content):
                if inline.kind == "link" and inline.target.kind == "anchor":
                    linked.add(inline.target.value)

    used: set[str] = set()
    next_suffix: dict[str, int] = {}

    def claim(base: str) -> str:
        if base not in used:
            used.add(base)
            next_suffix.setdefault(base, 1)
            return base
        number = next_suffix.get(base, 1)
        while f"{base}-{number}" in used:
            number += 1
        candidate = f"{base}-{number}"
        used.add(candidate)
        next_suffix[base] = number + 1
        next_suffix.setdefault(candidate, 1)
        return candidate

    fragments: dict[str, str] = {}
    html_ids: dict[str, str] = {}
    for block in _walk_blocks(document.blocks):
        if block.kind != "heading":
            continue
        slug = claim(_gfm_slug(_plain_text(block.content)))
        ids: list[str] = []
        if block.anchor is not None:
            ids.append(block.anchor)
        ids.extend(
            inline.anchor
            for inline in _walk_inlines(block.content)
            if inline.kind == "anchor"
        )
        for anchor_id in ids:
            fragments.setdefault(anchor_id, slug)

    for block in all_blocks:
        if block.kind not in {"heading", "paragraph"}:
            continue
        for inline in _walk_inlines(block.content):
            if (
                inline.kind == "anchor"
                and inline.anchor in linked
                and inline.anchor not in fragments
            ):
                resolved = claim(_sanitize_id(inline.anchor))
                fragments[inline.anchor] = resolved
                html_ids[inline.anchor] = resolved
    return fragments, html_ids


def _render_blocks(blocks: Sequence[Any], context: _RenderContext) -> str:
    return "\n\n".join(
        rendered
        for block in blocks
        if (rendered := _render_block(block, context)) is not None
    )


def _render_block(  # noqa: PLR0911
    block: Any, context: _RenderContext
) -> str | None:
    if block.kind == "heading":
        text = _render_inlines(block.content, "heading", context).strip()
        return f"{'#' * min(6, max(1, block.level))} {text}" if text else None
    if block.kind == "paragraph":
        text = _trim_paragraph(_render_inlines(block.content, "block", context))
        return text or None
    if block.kind == "list":
        return _render_list(block.list, context)
    if block.kind == "table":
        if (
            block.table.kind == "layout"
            and len(block.table.grid) == 1
            and len(block.table.grid[0]) == 1
            and block.table.grid[0][0].kind == "origin"
        ):
            return _render_blocks(block.table.grid[0][0].cell.blocks, context) or None
        return _render_table(block.table, context)
    if block.kind == "block_quote":
        inner = _render_blocks(block.blocks, context)
        return (
            "\n".join(">" if not line else f"> {line}" for line in inner.splitlines())
            if inner
            else None
        )
    if block.kind == "code_block":
        fence = _backtick_fence(block.text, 3)
        return f"{fence}{block.lang or ''}\n{block.text.rstrip(chr(10))}\n{fence}"
    if block.kind == "rule":
        return "---"
    source = _escape_math_dollars(block.text.strip())
    return f"$$\n{source}\n$$" if source else None


def _render_list(value: Any, context: _RenderContext) -> str | None:
    if not value.items:
        return None
    rendered_items: list[str] = []
    loose = False
    for index, item in enumerate(value.items):
        if item.marker_label is not None:
            marker = f"- {_escape_marker_label(item.marker_label, 'block')} "
        elif value.marker == "bullet":
            marker = "- "
        elif value.marker == "decimal":
            marker = f"{value.start + index}. "
        else:
            marker = f"- {_marker_label(value.marker, value.start + index)} "
        body = _render_blocks(item.blocks, context)
        loose |= len(item.blocks) > 1
        lines = body.splitlines()
        result = marker + (lines[0] if lines else "")
        indent = " " * len(marker)
        for line in lines[1:]:
            result += "\n"
            if line:
                result += indent + line
            else:
                loose = True
        rendered_items.append(result)
    return ("\n\n" if loose else "\n").join(rendered_items)


def _marker_label(marker: str, number: int) -> str:
    if marker == "decimal":
        value = str(number)
    elif marker in {"lower_alpha", "upper_alpha"}:
        if number == 0:
            value = "0"
        else:
            chars: list[str] = []
            remaining = number
            while remaining > 0:
                remaining -= 1
                chars.append(chr(ord("a") + remaining % 26))
                remaining //= 26
            value = "".join(reversed(chars))
        if marker == "upper_alpha":
            value = value.upper()
    else:
        value = _roman(number)
        if marker == "upper_roman":
            value = value.upper()
    return value + "."


def _roman(number: int) -> str:
    if number == 0 or number > _MAX_ROMAN:
        return str(number)
    output = ""
    for value, numeral in (
        (1000, "m"),
        (900, "cm"),
        (500, "d"),
        (400, "cd"),
        (100, "c"),
        (90, "xc"),
        (50, "l"),
        (40, "xl"),
        (10, "x"),
        (9, "ix"),
        (5, "v"),
        (4, "iv"),
        (1, "i"),
    ):
        while number >= value:
            output += numeral
            number -= value
    return output


def _trim_paragraph(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        trimmed = line.lstrip()
        if not _ends_with_hard_break(trimmed):
            trimmed = trimmed.rstrip()
        if not trimmed.rstrip("\\").strip():
            trimmed = ""
        lines.append(trimmed)
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    output = "\n".join(lines)
    if _ends_with_hard_break(output):
        output = output[:-1].rstrip()
    return output


def _ends_with_hard_break(value: str) -> bool:
    return (len(value) - len(value.rstrip("\\"))) % 2 == 1


def _normalize(inlines: Sequence[Any], context: _RenderContext) -> list[_Run]:
    output: list[_Run] = []
    for inline in inlines:
        if inline.kind == "text":
            if not inline.text:
                continue
            style = (
                inline.style.bold,
                inline.style.italic,
                inline.style.strike,
                inline.style.code,
            )
            if not inline.text.strip():
                style = (False, False, False, False)
            if (
                output
                and isinstance(output[-1], _TextRun)
                and output[-1].style == style
            ):
                output[-1] = _TextRun(output[-1].text + inline.text, style)
            elif (
                style != (False, False, False, False)
                and not style[3]
                and len(output) >= _MIN_BRIDGE_RUNS
                and isinstance(output[-1], _TextRun)
                and output[-1].style == (False, False, False, False)
                and not output[-1].text.strip()
                and isinstance(output[-2], _TextRun)
                and output[-2].style == style
            ):
                whitespace = cast("_TextRun", output.pop()).text
                previous = output[-1]
                output[-1] = _TextRun(previous.text + whitespace + inline.text, style)
            else:
                output.append(_TextRun(inline.text, style))
        elif inline.kind == "link" and not inline.target.value:
            if not _inlines_are_empty(inline.content):
                output.extend(_normalize(inline.content, context))
        elif (inline.kind == "anchor" and inline.anchor not in context.html_ids) or (
            inline.kind == "math" and not inline.text.strip()
        ):
            continue
        else:
            output.append(_NodeRun(inline))
    return output


def _render_inlines(  # noqa: PLR0912
    inlines: Sequence[Any],
    inline_context: Literal["block", "heading", "table"],
    context: _RenderContext,
    *,
    in_label: bool = False,
) -> str:
    runs = _normalize(inlines, context)
    suffix: list[frozenset[str]] = [frozenset() for _ in range(len(runs) + 1)]
    for index in range(len(runs) - 1, -1, -1):
        suffix[index] = suffix[index + 1] | _delimiters_of(runs[index], context)
    output = ""
    for index, run in enumerate(runs):
        if isinstance(run, _TextRun):
            following = runs[index + 1] if index + 1 < len(runs) else None
            next_active = _is_active_run(following)
            next_nonspace = _is_nonspace_run(following, inline_context)
            output += _render_text_run(
                run,
                inline_context,
                _EscapeOptions(
                    trailing_active=next_active,
                    trailing_nonspace=next_nonspace,
                    trailing_delims=suffix[index + 1],
                    in_label=in_label,
                ),
                at_line_start=not output or output.endswith("\n"),
            )
            continue
        inline = run.node
        if inline.kind == "note_ref":
            if inline.note_id in context.note_numbers:
                output += f"[^{context.note_numbers[inline.note_id]}]"
        elif inline.kind == "link":
            output += _render_link(inline, inline_context, context)
        elif inline.kind == "image":
            occurrence = context.image_index[0]
            context.image_index[0] += 1
            path = context.image_paths[occurrence]
            if path is not None:
                alt = _escape_text(
                    inline.alt.strip(),
                    inline_context,
                    _EscapeOptions(in_label=True),
                )
                output += f"![{alt}]({path.as_posix()})"
            elif inline.alt.strip():
                output += _escape_text(
                    inline.alt.strip(),
                    inline_context,
                    _EscapeOptions(in_label=in_label),
                )
        elif inline.kind == "anchor":
            output += f'<a id="{context.html_ids[inline.anchor]}"></a>'
        elif inline.kind == "line_break":
            output += {"block": "\\\n", "heading": " ", "table": "\n"}[inline_context]
        elif inline.kind == "math":
            output += _render_math_span(inline.text, inline_context)
        elif inline.kind == "checkbox":
            output += "[x]" if inline.checked else "[ ]"
            if index + 1 < len(runs) and not _starts_with_space(runs[index + 1]):
                output += " "
    return output


def _is_active_run(run: _Run | None) -> bool:
    if isinstance(run, _TextRun):
        return run.style != (False, False, False, False)
    return isinstance(run, _NodeRun) and run.node.kind in {
        "link",
        "image",
        "note_ref",
        "math",
    }


def _is_nonspace_run(
    run: _Run | None, inline_context: Literal["block", "heading", "table"]
) -> bool:
    return isinstance(run, _NodeRun) and (
        run.node.kind in {"anchor", "checkbox"}
        or (run.node.kind == "line_break" and inline_context != "heading")
    )


def _starts_with_space(run: _Run) -> bool:
    return (isinstance(run, _TextRun) and bool(run.text) and run.text[0].isspace()) or (
        isinstance(run, _NodeRun) and run.node.kind == "line_break"
    )


def _render_link(
    inline: Any,
    inline_context: Literal["block", "heading", "table"],
    context: _RenderContext,
) -> str:
    target = inline.target
    if target.kind == "anchor":
        fragment = context.fragments.get(target.value)
        if fragment is None:
            return _render_inlines(inline.content, inline_context, context)
        url = f"#{fragment}"
    else:
        if not _is_safe_hyperlink(target.value):
            reject(ReverseErrorCategory.MALFORMED)
        url = target.value
    label = _render_inlines(inline.content, inline_context, context, in_label=True)
    if not label.strip():
        if target.kind == "anchor":
            return ""
        label = _escape_url_as_text(url, inline_context)
    return f"[{label}]({_format_url(url)})"


def _render_text_run(
    run: _TextRun,
    inline_context: Literal["block", "heading", "table"],
    options: _EscapeOptions,
    *,
    at_line_start: bool,
) -> str:
    plain = (False, False, False, False)
    if run.style == plain:
        return _escape_text(
            run.text,
            inline_context,
            _EscapeOptions(
                at_line_start=at_line_start,
                trailing_active=options.trailing_active,
                trailing_nonspace=options.trailing_nonspace,
                trailing_delims=options.trailing_delims,
                in_label=options.in_label,
            ),
        )
    lead_count = len(run.text) - len(run.text.lstrip())
    trail_index = len(run.text.rstrip())
    lead, core, trail = (
        run.text[:lead_count],
        run.text[lead_count:trail_index],
        run.text[trail_index:],
    )
    if not core:
        return lead + trail
    bold, italic, strike, code = run.style
    if code:
        rendered = _render_code_span(core, inline_context)
    else:
        opening = (
            ("~~" if strike else "") + ("**" if bold else "") + ("*" if italic else "")
        )
        rendered = (
            opening
            + _escape_text(
                core,
                inline_context,
                _EscapeOptions(styled=True, in_label=options.in_label),
            )
            + opening[::-1]
        )
    return lead + rendered + trail


def _delimiters_of(  # noqa: PLR0911, PLR0912
    run: _Run, context: _RenderContext
) -> frozenset[str]:
    if isinstance(run, _TextRun):
        bold, italic, strike, code = run.style
        if code:
            return frozenset("`")
        if run.style == (False, False, False, False):
            return _closing_delimiters(run.text)
        delimiters: set[str] = set()
        if bold or italic:
            delimiters.add("*")
        if strike:
            delimiters.add("~")
        if "`" in run.text:
            delimiters.add("`")
        if "]" in run.text:
            delimiters.add("]")
        delimiters |= _closing_delimiters(
            "".join(char if char == "$" or char.isspace() else "x" for char in run.text)
        )
        return frozenset(delimiters)
    inline = run.node
    if inline.kind == "link":
        if (
            inline.target.kind == "anchor"
            and inline.target.value not in context.fragments
        ):
            result: set[str] = set()
            for child in _normalize(inline.content, context):
                result |= _delimiters_of(child, context)
            return frozenset(result)
        if _emits_backtick(inline.content) or "`" in inline.target.value:
            return frozenset("`")
    elif inline.kind == "image":
        if inline.source.kind == "external":
            return frozenset("`" if "`" in inline.alt else "")
        return _closing_delimiters(inline.alt)
    return frozenset()


def _emits_backtick(inlines: Sequence[Any]) -> bool:
    for inline in inlines:
        if inline.kind == "text" and (
            "`" in inline.text or (inline.style.code and bool(inline.text.strip()))
        ):
            return True
        if inline.kind == "link" and (
            _emits_backtick(inline.content) or "`" in inline.target.value
        ):
            return True
        if inline.kind == "image" and "`" in inline.alt:
            return True
    return False


def _closing_delimiters(text: str) -> frozenset[str]:
    chars = list(text)
    output: set[str] = set()
    index = 0
    while index < len(chars):
        end = index + 1
        while end < len(chars) and chars[end] == chars[index]:
            end += 1
        char = chars[index]
        if (
            char in "`]"
            or (char == "$" and _can_close_math(chars, index, end))
            or (char in "*_~" and _can_close(chars, index, end))
        ):
            output.add(char)
        index = end
    return frozenset(output)


def _can_close_math(chars: Sequence[str], start: int, end: int) -> bool:
    previous = chars[start - 1] if start else None
    following = chars[end] if end < len(chars) else None
    return not (previous is not None and previous.isspace()) and not (
        following is not None and following.isascii() and following.isdigit()
    )


def _can_close(chars: Sequence[str], start: int, end: int) -> bool:
    previous = chars[start - 1] if start else None
    following = chars[end] if end < len(chars) else None
    if previous is not None and previous.isspace():
        return False
    if (
        previous is not None
        and previous.isascii()
        and not previous.isalnum()
        and following is not None
        and following.isalnum()
    ):
        return False
    return chars[start] != "_" or not (
        previous is not None
        and previous.isalnum()
        and following is not None
        and following.isalnum()
    )


def _escape_text(  # noqa: PLR0912, PLR0915
    text: str,
    inline_context: Literal["block", "heading", "table"],
    options: _EscapeOptions,
) -> str:
    chars = list(text)
    last: dict[str, int] = {}
    index = 0
    while index < len(chars):
        end = index + 1
        while end < len(chars) and chars[end] == chars[index]:
            end += 1
        char = chars[index]
        if (
            char in "`]"
            or (char == "$" and _can_close_math(chars, index, end))
            or (char in "*_~" and _can_close(chars, index, end))
        ):
            last[char] = end - 1
        index = end
    output: list[str] = []
    line_has_content = not (options.at_line_start and inline_context == "block")
    index = 0
    while index < len(chars):
        char = chars[index]
        if char == "\n":
            output.append(char)
            if inline_context == "block":
                line_has_content = False
            index += 1
            continue
        start_of_line = not line_has_content
        if not char.isspace():
            line_has_content = True
        following = chars[index + 1] if index + 1 < len(chars) else None
        next_nonspace = (
            options.trailing_active or options.trailing_nonspace
            if following is None
            else not following.isspace()
        )
        paired = (
            options.trailing_active
            or char in options.trailing_delims
            or last.get(char, -1) > index
        )
        escape = False
        if char == "\\":
            escape = True
        elif char == "$":
            escape = next_nonspace and paired
        elif char == "]" and options.in_label:
            escape = True
        elif char == "`":
            escape = options.styled or paired
        elif char == "*":
            escape = options.styled or start_of_line or (next_nonspace and paired)
        elif char == "_":
            previous_alnum = index > 0 and chars[index - 1].isalnum()
            following_alnum = following is not None and following.isalnum()
            escape = options.styled or (
                next_nonspace and not (previous_alnum and following_alnum) and paired
            )
        elif char == "~":
            escape = options.styled or (next_nonspace and paired)
        elif char == "[":
            escape = (
                options.in_label
                or "]" in options.trailing_delims
                or last.get("]", -1) > index
            )
        elif char == "<":
            escape = following is not None and (
                (following.isascii() and following.isalpha()) or following in "/!?"
            )
        elif char == "!":
            escape = following is None and options.trailing_active
        elif char == "|" and inline_context == "table":
            escape = True
        elif char == "&" and _entity_ahead(chars[index:]):
            output.append("&amp;")
            index += 1
            continue
        elif char == "#" and start_of_line:
            cursor = index
            while cursor < len(chars) and chars[cursor] == "#":
                cursor += 1
            escape = cursor == len(chars) or chars[cursor].isspace()
        elif char == "-" and start_of_line:
            escape = not next_nonspace or _line_is_only(chars[index:], "-")
        elif char == "+" and start_of_line:
            escape = not next_nonspace
        elif char == ">" and start_of_line:
            escape = True
        elif char == "=" and start_of_line:
            escape = _line_is_only(chars[index:], "=")
        elif char.isascii() and char.isdigit() and start_of_line:
            cursor = index
            while (
                cursor < len(chars)
                and chars[cursor].isascii()
                and chars[cursor].isdigit()
            ):
                cursor += 1
            if (
                cursor < len(chars)
                and chars[cursor] in ".)"
                and (cursor + 1 == len(chars) or chars[cursor + 1].isspace())
            ):
                output.extend(chars[index:cursor])
                output.extend(("\\", chars[cursor]))
                index = cursor + 1
                continue
        if escape:
            output.append("\\")
        output.append(char)
        index += 1
    return "".join(output)


def _line_is_only(chars: Sequence[str], char: str) -> bool:
    return all(
        value == char or value in " \t"
        for value in list(chars)[
            : next((i for i, value in enumerate(chars) if value == "\n"), len(chars))
        ]
    )


def _entity_ahead(chars: Sequence[str]) -> bool:
    if len(chars) > 1 and chars[1] == "#":
        return True
    index = 1
    while index < len(chars) and chars[index].isascii() and chars[index].isalnum():
        index += 1
    return index > 1 and index < len(chars) and chars[index] == ";"


def _format_url(url: str) -> str:
    output: list[str] = []
    for char in url:
        if char == "<":
            output.append("%3C")
        elif char == ">":
            output.append("%3E")
        elif char == "|":
            output.append("%7C")
        elif unicodedata.category(char) == "Cc":
            output.extend(f"%{byte:02X}" for byte in char.encode())
        else:
            output.append(char)
    escaped = "".join(output)
    return (
        f"<{escaped}>"
        if any(char.isspace() or char in "()" for char in escaped)
        else escaped
    )


def _escape_url_as_text(
    url: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in url
    )
    return _escape_text(
        cleaned,
        inline_context,
        _EscapeOptions(trailing_active=True, in_label=True),
    )


def _escape_marker_label(
    label: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    cleaned = "".join(
        " " if unicodedata.category(char).startswith("C") else char for char in label
    )
    return _escape_text(
        cleaned,
        inline_context,
        _EscapeOptions(at_line_start=inline_context == "block", trailing_active=True),
    )


def _backtick_fence(text: str, minimum: int) -> str:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(longest + 1, minimum)


def _escape_cell_code_span(text: str) -> str:
    output: list[str] = []
    backslashes = 0
    for char in text:
        if char == "|":
            output.extend("\\" for _ in range(backslashes + 1))
            backslashes = 0
        elif char == "\\":
            backslashes += 1
        else:
            backslashes = 0
        output.append(char)
    return "".join(output)


def _render_code_span(
    text: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    text = text.replace("\n", " ")
    fence = _backtick_fence(text, 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    if inline_context == "table":
        text = _escape_cell_code_span(text)
    return f"{fence}{padding}{text}{padding}{fence}"


def _escape_math_dollars(text: str) -> str:
    output: list[str] = []
    backslashes = 0
    for char in text:
        if char == "$" and backslashes % 2 == 0:
            output.append("\\")
        output.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    return "".join(output)


def _render_math_span(
    text: str, inline_context: Literal["block", "heading", "table"]
) -> str:
    source = _escape_math_dollars(text.strip()).replace("\n", " ")
    if inline_context == "table":
        output: list[str] = []
        backslashes = 0
        for char in source:
            if char == "|" and backslashes % 2 == 0:
                output.append("\\")
            output.append(char)
            backslashes = backslashes + 1 if char == "\\" else 0
        source = "".join(output)
    return f"${source}$"


@dataclass
class _RenderedCell:
    text: str
    covered_span: bool


def _render_table(table: Any, context: _RenderContext) -> str | None:
    if not table.grid:
        return None
    width = max((len(row) for row in table.grid), default=0)
    rendered: list[list[_RenderedCell]] = []
    for row in table.grid:
        cells = [
            _RenderedCell(_render_cell(slot.cell, context), False)
            if slot.kind == "origin"
            else _RenderedCell("", True)
            for slot in row
        ]
        cells.extend(_RenderedCell("", False) for _ in range(width - len(cells)))
        rendered.append(cells)
    while len(rendered) > 1 and all(
        not cell.text and not cell.covered_span for cell in rendered[-1]
    ):
        rendered.pop()
    width = max(
        (
            max(
                (
                    index + 1
                    for index, cell in enumerate(row)
                    if cell.text or cell.covered_span
                ),
                default=0,
            )
            for row in rendered
        ),
        default=0,
    )
    if width == 0:
        return None
    rendered = [row[:width] for row in rendered]
    if table.header_rows >= 1 and rendered:
        header = [cell.text for cell in rendered.pop(0)]
    else:
        header = [""] * width
    rows = [_format_row(header), _format_row(["---"] * width)]
    rows.extend(_format_row([cell.text for cell in row]) for row in rendered)
    return "\n".join(rows)


def _format_row(cells: Sequence[str]) -> str:
    return "|" + "".join(f" {cell} |" for cell in cells)


def _render_cell(cell: Any, context: _RenderContext) -> str:
    parts: list[str] = []
    for block in cell.blocks:
        _cell_block_text(block, context, parts)
    return "<br>".join(
        line.strip() for line in "<br>".join(parts).splitlines() if line.strip()
    )


def _cell_block_text(  # noqa: PLR0912
    block: Any, context: _RenderContext, parts: list[str]
) -> None:
    if block.kind == "heading":
        text = _render_inlines(block.content, "table", context)
        if text.strip():
            parts.append(f"**{text.strip()}**")
    elif block.kind == "paragraph":
        text = _render_inlines(block.content, "table", context)
        if text.strip():
            parts.append(text)
    elif block.kind == "list":
        for index, item in enumerate(block.list.items):
            inner: list[str] = []
            for nested in item.blocks:
                _cell_block_text(nested, context, inner)
            if item.marker_label is not None:
                marker = _escape_marker_label(item.marker_label, "table") + " "
            elif block.list.marker == "bullet":
                marker = "• "
            else:
                marker = (
                    _marker_label(block.list.marker, block.list.start + index) + " "
                )
            if inner:
                parts.append(marker + " ".join(inner))
    elif block.kind == "table":
        for row in block.table.grid:
            cells = [
                _render_cell(slot.cell, context) if slot.kind == "origin" else ""
                for slot in row
            ]
            if any(cells):
                parts.append(" / ".join(cells))
    elif block.kind == "block_quote":
        for nested in block.blocks:
            _cell_block_text(nested, context, parts)
    elif block.kind == "code_block" and block.text.strip():
        parts.append(_render_code_span(block.text.strip(), "table"))
    elif block.kind == "math" and block.text.strip():
        parts.append(_render_math_span(block.text, "table"))


__all__ = [
    "PINNED_ANYDOC_VERSION",
    "UPSTREAM_ANYDOC_COMMIT",
    "UPSTREAM_RENDERER_SURFACES",
    "ParsedSource",
    "RenderedDocument",
    "extract_asset_sources",
    "parse_document",
    "parse_source",
    "render_document",
    "render_document_result",
    "validate_document",
]
