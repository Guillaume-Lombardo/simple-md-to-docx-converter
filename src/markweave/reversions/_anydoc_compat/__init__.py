"""Fail-closed compatibility boundary for firecrawl-anydoc 0.2.4.

This private package is the only Markweave code allowed to depend on anydoc's concrete
``Document`` shape or to mirror its private Markdown renderer. It consumes one
already parsed document and injects normalized local image paths at the image
nodes' source positions. It must be removed when anydoc exposes a supported
asset-aware renderer hook.

The renderer behavior is derived from firecrawl/anydoc commit
42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c under the MIT license retained in
``../ANYDOC_COMPAT_LICENSE.txt``. The exact mirrored surfaces are inventoried in
``UPSTREAM_RENDERER_SURFACES`` below."""

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Never, cast

import anydoc

import markweave.reversions._anydoc_compat.blocks as _blocks
import markweave.reversions._anydoc_compat.escaping as _escaping
import markweave.reversions._anydoc_compat.inlines as _inlines
import markweave.reversions._anydoc_compat.render_context as _render_context
import markweave.reversions._anydoc_compat.traversal as _traversal
from markweave.reversions.assets import AssetSource
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.formats import FormatAdmission, FormatFamily, admit_format

_is_safe_hyperlink = _inlines._is_safe_hyperlink
_MAX_ROMAN = _blocks._MAX_ROMAN
_MIN_BRIDGE_RUNS = _inlines._MIN_BRIDGE_RUNS
_TextRun = _render_context._TextRun
_NodeRun = _render_context._NodeRun
_Run = _render_context._Run
_RenderContext = _render_context._RenderContext
_EscapeOptions = _render_context._EscapeOptions
RenderedDocument = _render_context.RenderedDocument
_walk_blocks = _traversal._walk_blocks
_walk_inlines = _traversal._walk_inlines
_image_nodes = _traversal._image_nodes
_inlines_are_empty = _traversal._inlines_are_empty
_number_notes = _traversal._number_notes
_plain_text = _blocks._plain_text
_gfm_slug = _blocks._gfm_slug
_sanitize_id = _blocks._sanitize_id
_resolve_anchors = _blocks._resolve_anchors
_render_blocks = _blocks._render_blocks
_render_block = _blocks._render_block
_render_list = _blocks._render_list
_marker_label = _blocks._marker_label
_roman = _blocks._roman
_trim_paragraph = _inlines._trim_paragraph
_ends_with_hard_break = _inlines._ends_with_hard_break
_normalize = _inlines._normalize
_render_inlines = _inlines._render_inlines
_is_active_run = _inlines._is_active_run
_is_nonspace_run = _inlines._is_nonspace_run
_starts_with_space = _inlines._starts_with_space
_render_link = _inlines._render_link
_render_text_run = _inlines._render_text_run
_delimiters_of = _inlines._delimiters_of
_emits_backtick = _inlines._emits_backtick
_closing_delimiters = _escaping._closing_delimiters
_can_close_math = _escaping._can_close_math
_can_close = _escaping._can_close
_escape_text = _escaping._escape_text
_line_is_only = _escaping._line_is_only
_entity_ahead = _escaping._entity_ahead
_format_url = _escaping._format_url
_escape_url_as_text = _escaping._escape_url_as_text
_escape_marker_label = _escaping._escape_marker_label
_backtick_fence = _escaping._backtick_fence
_escape_cell_code_span = _escaping._escape_cell_code_span
_render_code_span = _escaping._render_code_span
_escape_math_dollars = _escaping._escape_math_dollars
_render_math_span = _escaping._render_math_span
_RenderedCell = _blocks._RenderedCell
_render_table = _blocks._render_table
_format_row = _blocks._format_row
_render_cell = _blocks._render_cell
_cell_block_text = _blocks._cell_block_text

PINNED_ANYDOC_VERSION = "0.2.4"

UPSTREAM_ANYDOC_COMMIT = "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"

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


def detect_source(data: bytes, extension: str) -> FormatAdmission:
    """Use the pinned native detector without flattening a structured presentation."""

    _check_version_and_surface()
    _require_type(data, bytes)
    try:
        detected = anydoc.format_from_bytes(data)
    except anydoc.ConvertError as error:
        _raise_mapped(error)
    return admit_format(
        extension,
        detected,
        csv_text_validated=detected is None and _is_bounded_csv_text(data),
    )


def parse_source(data: bytes, extension: str) -> ParsedSource:
    """Detect and admit child-local input, then parse or render it exactly once.

    The extension is only an admission hint. Content detection and CSV text validation
    happen inside the credentialless attempt child. PDF uses the pinned local renderer
    with literal OCR rejection; every other admitted format returns one parsed model.
    """

    admission = detect_source(data, extension)
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
        first_retained = len(context.retained_occurrences)
        body = _render_blocks(note.blocks, context)
        if not body or number in rendered_notes:
            del context.retained_occurrences[first_retained:]
            continue
        rendered_notes.add(number)
        lines = body.splitlines()
        definition = f"[^{number}]: {lines[0]}"
        definition += "".join(f"\n{'    ' if line else ''}{line}" for line in lines[1:])
        parts.append(definition)
    if context.image_index[0] != len(paths):
        _compatibility_error()
    output = "\n\n".join(parts)
    markdown = output + "\n" if output else ""
    return RenderedDocument(markdown, tuple(context.retained_occurrences))


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
