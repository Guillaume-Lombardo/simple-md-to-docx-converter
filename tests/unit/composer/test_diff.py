"""Bounded exact-revision Markdown change summaries."""

import pytest

from markweave.composer.diff import markdown_changes


@pytest.mark.unit
def test_markdown_changes_preserve_human_text_and_line_positions() -> None:
    changes, reason = markdown_changes(
        b"# Heading\nOld fact\n", b"# Heading\nCorrected fact\n"
    )
    assert reason is None
    assert len(changes) == 1
    assert changes[0].kind == "replace"
    assert changes[0].before_start == 2
    assert changes[0].after_start == 2
    assert changes[0].before_text == "Old fact\n"
    assert changes[0].after_text == "Corrected fact\n"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("old", "new", "reason"),
    (
        (b"a" * 131_073, b"new", "text_too_large"),
        (b"\xff", b"new", "invalid_text"),
        (b"line\n" * 1_001, b"new", "too_many_lines"),
    ),
)
def test_markdown_changes_do_not_return_partial_or_unbounded_results(
    old: bytes, new: bytes, reason: str
) -> None:
    assert markdown_changes(old, new) == ((), reason)


@pytest.mark.unit
def test_markdown_changes_refuse_more_than_bounded_hunks() -> None:
    old = "".join(f"shared {index}\nold {index}\n" for index in range(201))
    new = "".join(f"shared {index}\nnew {index}\n" for index in range(201))
    assert markdown_changes(old.encode(), new.encode()) == ((), "too_many_changes")
