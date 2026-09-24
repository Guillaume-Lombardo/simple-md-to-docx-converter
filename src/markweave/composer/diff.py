"""Bounded semantic line changes for immutable Composer text artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

_MAXIMUM_TEXT_BYTES = 131_072
_MAXIMUM_LINES = 1_000
_MAXIMUM_CHANGES = 200


@dataclass(frozen=True, slots=True)
class RevisionTextChange:
    kind: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    before_text: str
    after_text: str


def markdown_changes(
    before: bytes, after: bytes
) -> tuple[tuple[RevisionTextChange, ...], str | None]:
    """Return complete line changes, or a reason when a safe bound is exceeded."""

    if len(before) > _MAXIMUM_TEXT_BYTES or len(after) > _MAXIMUM_TEXT_BYTES:
        return (), "text_too_large"
    try:
        old_lines = before.decode("utf-8").splitlines(keepends=True)
        new_lines = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return (), "invalid_text"
    if len(old_lines) > _MAXIMUM_LINES or len(new_lines) > _MAXIMUM_LINES:
        return (), "too_many_lines"
    changes = []
    for kind, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if kind == "equal":
            continue
        if len(changes) >= _MAXIMUM_CHANGES:
            return (), "too_many_changes"
        changes.append(
            RevisionTextChange(
                kind=kind,
                before_start=old_start + 1,
                before_end=old_end,
                after_start=new_start + 1,
                after_end=new_end,
                before_text="".join(old_lines[old_start:old_end]),
                after_text="".join(new_lines[new_start:new_end]),
            )
        )
    return tuple(changes), None
