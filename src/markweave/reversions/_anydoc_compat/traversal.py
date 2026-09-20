"""Traverse the single parsed anydoc model; upstream MIT notice: ../ANYDOC_COMPAT_LICENSE.txt."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any


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
