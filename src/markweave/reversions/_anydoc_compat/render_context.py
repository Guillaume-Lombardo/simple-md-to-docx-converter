"""Private anydoc renderer state; upstream MIT notice: ../ANYDOC_COMPAT_LICENSE.txt."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import PurePosixPath
from typing import Any


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
    retained_occurrences: list[int] = dataclass_field(default_factory=list)


@dataclass(frozen=True)
class _EscapeOptions:
    at_line_start: bool = False
    styled: bool = False
    trailing_active: bool = False
    trailing_nonspace: bool = False
    trailing_delims: frozenset[str] = frozenset()
    in_label: bool = False


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """Rendered Markdown plus source occurrences retained in the output."""

    markdown: str
    retained_occurrences: tuple[int, ...]
