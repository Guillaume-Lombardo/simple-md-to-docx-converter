"""AnyDoc block, list, table, note and anchor rendering; upstream MIT notice: ../ANYDOC_COMPAT_LICENSE.txt."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from markweave.reversions._anydoc_compat.escaping import (
    _backtick_fence,
    _escape_marker_label,
    _escape_math_dollars,
    _render_code_span,
    _render_math_span,
)
from markweave.reversions._anydoc_compat.inlines import _render_inlines, _trim_paragraph
from markweave.reversions._anydoc_compat.render_context import _RenderContext
from markweave.reversions._anydoc_compat.traversal import _walk_blocks, _walk_inlines

_MAX_ROMAN = 3999


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
