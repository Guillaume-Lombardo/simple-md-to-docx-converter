from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any

import anydoc
import pytest

from markweave.reversions import _anydoc_compat as compat
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory

pytestmark = pytest.mark.integration


def _style(
    *,
    bold: bool = False,
    italic: bool = False,
    strike: bool = False,
    code: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(bold=bold, italic=italic, strike=strike, code=code)


def _inline(kind: str, **values: Any) -> SimpleNamespace:
    defaults = {
        "alt": None,
        "anchor": None,
        "checked": None,
        "content": None,
        "kind": kind,
        "note_id": None,
        "source": None,
        "style": None,
        "target": None,
        "text": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _text(value: str, **style: bool) -> SimpleNamespace:
    return _inline("text", text=value, style=_style(**style))


def _block(kind: str, **values: Any) -> SimpleNamespace:
    defaults = {
        "anchor": None,
        "blocks": None,
        "content": None,
        "kind": kind,
        "lang": None,
        "level": None,
        "list": None,
        "table": None,
        "text": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _context(*paths: PurePosixPath | None) -> compat._RenderContext:
    return compat._RenderContext({}, {}, {}, paths, [0])


def test_mirrors_all_standalone_block_variants() -> None:
    context = _context()
    paragraph = _block("paragraph", content=[_text("  # heading\nline  ")])
    quote = _block("block_quote", blocks=[paragraph, _block("rule")])

    assert (
        compat._render_block(
            _block("heading", level=9, content=[_text(" title ")]), context
        )
        == "###### title"
    )
    assert compat._render_block(paragraph, context) == "\\# heading\nline"
    assert compat._render_block(quote, context) == "> \\# heading\n> line\n>\n> ---"
    assert (
        compat._render_block(_block("code_block", lang="py", text="x```\n"), context)
        == "````py\nx```\n````"
    )
    assert compat._render_block(_block("rule"), context) == "---"
    assert (
        compat._render_block(_block("math", text=" a $ b "), context)
        == "$$\na \\$ b\n$$"
    )
    assert compat._render_block(_block("math", text="  "), context) is None


def test_mirrors_ordered_and_source_marker_lists() -> None:
    item = lambda value, label=None: SimpleNamespace(  # noqa: E731
        blocks=[_block("paragraph", content=[_text(value)])], marker_label=label
    )
    context = _context()

    decimal = SimpleNamespace(
        marker="decimal", start=4, items=[item("four"), item("five")]
    )
    alpha = SimpleNamespace(marker="lower_alpha", start=27, items=[item("alpha")])
    roman = SimpleNamespace(marker="upper_roman", start=4, items=[item("roman")])
    labelled = SimpleNamespace(marker="bullet", start=1, items=[item("body", "1-*)")])

    assert compat._render_list(decimal, context) == "4. four\n5. five"
    assert compat._render_list(alpha, context) == "- aa. alpha"
    assert compat._render_list(roman, context) == "- IV. roman"
    assert compat._render_list(labelled, context) == "- 1-\\*) body"
    assert compat._render_list(SimpleNamespace(items=[]), context) is None
    assert compat._marker_label("upper_alpha", 1) == "A."
    assert compat._marker_label("lower_roman", 4000) == "4000."


def test_mirrors_inline_styles_links_math_breaks_and_checkboxes() -> None:
    context = compat._RenderContext(
        {"note": 1}, {"known": "target"}, {"mark": "mark"}, (), [0]
    )
    content = [
        _text("bold", bold=True),
        _text(" "),
        _text("again", bold=True),
        _text(" and "),
        _text("code`", code=True),
        _inline(
            "link",
            content=[_text("site")],
            target=SimpleNamespace(kind="external", value="https://example.test/a-b"),
        ),
        _inline(
            "link",
            content=[_text("jump")],
            target=SimpleNamespace(kind="anchor", value="known"),
        ),
        _inline("anchor", anchor="mark"),
        _inline("note_ref", note_id="note"),
        _inline("line_break"),
        _inline("math", text="x$y\nz"),
        _inline("checkbox", checked=True),
        _text("caption"),
    ]

    rendered = compat._render_inlines(content, "block", context)

    assert "**bold again**" in rendered
    assert "`` code` ``" in rendered
    assert "[site](https://example.test/a-b)" in rendered
    assert "[jump](#target)" in rendered
    assert '<a id="mark"></a>[^1]\\\n$x\\$y z$[x] caption' in rendered


def test_mirrors_unresolved_empty_and_nested_links() -> None:
    context = _context()
    unresolved = _inline(
        "link",
        content=[_text("plain")],
        target=SimpleNamespace(kind="anchor", value="missing"),
    )
    empty_target = _inline(
        "link",
        content=[_text("kept")],
        target=SimpleNamespace(kind="relative", value=""),
    )
    empty_label = _inline(
        "link",
        content=[],
        target=SimpleNamespace(kind="external", value="https://example.test/x"),
    )

    assert compat._render_inlines(
        [unresolved, empty_target, empty_label], "block", context
    ) == ("plainkept[https://example.test/x](https://example.test/x)")


def test_mirrors_unavailable_and_linked_image_rendering() -> None:
    embedded = _inline(
        "image",
        alt=" dot] ",
        source=SimpleNamespace(kind="asset", asset_id=0, url=None),
    )
    unavailable = _inline(
        "image",
        alt=" *missing* ",
        source=SimpleNamespace(kind="unavailable", asset_id=None, url=None),
    )
    context = _context(PurePosixPath("assets/image-0001.png"), None)

    rendered = compat._render_inlines(
        [embedded, _text(" "), unavailable], "block", context
    )

    assert rendered == "![dot\\]](assets/image-0001.png) \\*missing*"


def test_mirrors_table_spans_layout_and_nested_content() -> None:
    def cell(*blocks: Any) -> SimpleNamespace:
        return SimpleNamespace(blocks=list(blocks), col_span=1, row_span=1)

    def origin(*blocks: Any) -> SimpleNamespace:
        return SimpleNamespace(
            kind="origin", cell=cell(*blocks), origin_row=None, origin_col=None
        )

    covered = SimpleNamespace(kind="covered", cell=None, origin_row=0, origin_col=0)
    nested = SimpleNamespace(
        kind="data",
        header_rows=0,
        grid=[[origin(_block("paragraph", content=[_text("nested")]))]],
    )
    list_value = SimpleNamespace(
        marker="bullet",
        start=1,
        items=[
            SimpleNamespace(
                blocks=[_block("paragraph", content=[_text("item")])], marker_label=None
            )
        ],
    )
    table = SimpleNamespace(
        kind="data",
        header_rows=1,
        grid=[
            [origin(_block("heading", content=[_text("Head")]))],
            [
                origin(
                    _block("list", list=list_value),
                    _block("table", table=nested),
                    _block("code_block", text="a|b", lang=None),
                    _block("math", text="x|y"),
                    _block("rule"),
                ),
                covered,
            ],
            [origin(_block("paragraph", content=[_text("  ")]))],
        ],
    )

    rendered = compat._render_table(table, _context())

    assert rendered == (
        "| **Head** |  |\n| --- | --- |\n| • item<br>nested<br>`a\\|b`<br>$x\\|y$ |  |"
    )
    layout = SimpleNamespace(
        kind="layout",
        header_rows=0,
        grid=[[origin(_block("paragraph", content=[_text("flat")]))]],
    )
    assert compat._render_block(_block("table", table=layout), _context()) == "flat"


def test_mirrors_anchor_slug_collision_and_sanitization() -> None:
    heading_one = _block(
        "heading",
        level=1,
        anchor="first",
        content=[_text("Déjà Vu"), _inline("anchor", anchor="coincident")],
    )
    heading_two = _block("heading", level=1, content=[_text("Déjà Vu")])
    target = _block(
        "paragraph",
        content=[
            _inline("anchor", anchor=" Bad ID! "),
            _inline(
                "link",
                content=[_text("go")],
                target=SimpleNamespace(kind="anchor", value=" Bad ID! "),
            ),
        ],
    )
    document = SimpleNamespace(blocks=[heading_one, heading_two, target], notes=[])

    fragments, html_ids = compat._resolve_anchors(document)

    assert fragments == {
        "first": "déjà-vu",
        "coincident": "déjà-vu",
        " Bad ID! ": "bad-id",
    }
    assert html_ids == {" Bad ID! ": "bad-id"}


@pytest.mark.parametrize(
    ("value", "context", "expected"),
    [
        ("# title", "block", "\\# title"),
        ("1. item", "block", "1\\. item"),
        ("---", "block", "\\---"),
        ("<tag &copy;", "heading", "\\<tag &amp;copy;"),
        ("a|b", "table", "a\\|b"),
        ("a\\b", "block", "a\\\\b"),
    ],
)
def test_mirrors_contextual_escaping(value: str, context: Any, expected: str) -> None:
    assert (
        compat._escape_text(value, context, compat._EscapeOptions(at_line_start=True))
        == expected
    )


def test_mirrors_url_code_and_math_edge_escaping() -> None:
    assert compat._format_url("a<(b)|\n") == "<a%3C(b)%7C%0A>"
    assert compat._escape_cell_code_span(r"a\|b") == r"a\\\|b"
    assert compat._render_code_span("`x`", "block") == "`` `x` ``"
    assert compat._render_math_span(r"x\|y$", "table") == r"$x\|y\$$"
    assert compat._backtick_fence("a``b", 3) == "```"


def test_mirrors_plain_text_slug_and_empty_inline_helpers() -> None:
    inlines = [
        _inline(
            "link",
            content=[_text("linked")],
            target=SimpleNamespace(kind="relative", value="target"),
        ),
        _inline("image", alt="alt", source=SimpleNamespace(kind="unavailable")),
        _inline("math", text="x"),
        _inline("checkbox", checked=False),
        _inline("line_break"),
    ]

    assert compat._plain_text(inlines) == "linkedaltx[ ]\n"
    assert compat._gfm_slug(" !!! ") == "section"
    assert compat._gfm_slug("A‿ n\N{COMBINING TILDE}") == "a‿-ñ"
    assert compat._sanitize_id("--Å  B--") == "b"
    assert compat._inlines_are_empty([_text(" "), _inline("line_break")])
    assert not compat._inlines_are_empty(inlines)


def test_mirrors_remaining_inline_normalization_and_delimiter_edges() -> None:
    context = _context()
    empty_link = _inline(
        "link",
        content=[_text(" ")],
        target=SimpleNamespace(kind="relative", value=""),
    )
    runs = compat._normalize(
        [_text(""), _text(" ", bold=True), empty_link, _inline("math", text=" ")],
        context,
    )
    assert runs == [compat._TextRun(" ", (False, False, False, False))]

    styled = compat._TextRun("`x]", (False, False, True, False))
    assert compat._delimiters_of(styled, context) >= {"`", "]", "~"}
    nested_link = _inline(
        "link",
        content=[_text("x", code=True)],
        target=SimpleNamespace(kind="relative", value="target"),
    )
    assert compat._emits_backtick([nested_link])
    external_image = compat._NodeRun(
        _inline("image", alt="a`b", source=SimpleNamespace(kind="external"))
    )
    assert compat._delimiters_of(external_image, context) == {"`"}


def test_mirrors_remaining_inline_output_branches() -> None:
    context = _context()
    assert (
        compat._render_inlines(
            [_inline("note_ref", note_id="missing")], "block", context
        )
        == ""
    )
    assert compat._render_inlines([_inline("line_break")], "heading", context) == " "
    assert compat._render_inlines([_inline("line_break")], "table", context) == "\n"
    assert (
        compat._render_inlines([_inline("checkbox", checked=False)], "block", context)
        == "[ ]"
    )
    empty_anchor = _inline(
        "link",
        content=[],
        target=SimpleNamespace(kind="anchor", value="known"),
    )
    anchored = compat._RenderContext({}, {"known": "fragment"}, {}, (), [0])
    assert compat._render_link(empty_anchor, "block", anchored) == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("~x~", "\\~x\\~"),
        ("!", "\\!"),
        ("+ ", "\\+ "),
        ("> quote", "\\> quote"),
        ("===", "\\==="),
        ("12) item", "12\\) item"),
        ("&#12;", "&amp;#12;"),
    ],
)
def test_mirrors_additional_block_escape_forms(value: str, expected: str) -> None:
    assert (
        compat._escape_text(
            value,
            "block",
            compat._EscapeOptions(at_line_start=True, trailing_active=True),
        )
        == expected
    )


def test_mirrors_empty_and_headerless_table_edges() -> None:
    assert compat._render_table(SimpleNamespace(grid=[]), _context()) is None
    empty_slot = SimpleNamespace(
        kind="origin",
        cell=SimpleNamespace(blocks=[], col_span=1, row_span=1),
        origin_row=None,
        origin_col=None,
    )
    assert (
        compat._render_table(
            SimpleNamespace(grid=[[empty_slot]], header_rows=0, kind="data"), _context()
        )
        is None
    )
    filled_slot = SimpleNamespace(
        kind="origin",
        cell=SimpleNamespace(
            blocks=[_block("paragraph", content=[_text("value")])],
            col_span=1,
            row_span=1,
        ),
        origin_row=None,
        origin_col=None,
    )
    assert (
        compat._render_table(
            SimpleNamespace(
                grid=[[filled_slot], [empty_slot]], header_rows=0, kind="data"
            ),
            _context(),
        )
        == "|  |\n| --- |\n| value |"
    )


def test_missing_install_and_generic_native_error_fail_closed(mocker: Any) -> None:
    mocker.patch.object(
        compat.importlib.metadata,
        "version",
        side_effect=compat.importlib.metadata.PackageNotFoundError,
    )
    with pytest.raises(ReverseConversionError) as missing:
        compat._check_version_and_surface()
    assert missing.value.category is ReverseErrorCategory.MALFORMED

    mocker.patch.object(
        compat.importlib.metadata, "version", return_value=compat.PINNED_ANYDOC_VERSION
    )
    mocker.patch.object(
        compat.anydoc, "to_document", side_effect=anydoc.ConvertError("private")
    )
    with pytest.raises(ReverseConversionError) as generic:
        compat.parse_document(b"source", "docx")
    assert generic.value.category is ReverseErrorCategory.MALFORMED
