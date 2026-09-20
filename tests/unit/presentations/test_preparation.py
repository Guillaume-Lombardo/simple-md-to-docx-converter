"""Presentation dialect boundaries, safe preprocessing, and immutable options."""

from dataclasses import replace
from pathlib import PurePosixPath

import pytest

from markweave.conversion.archive import ApprovedDocument
from markweave.conversion.errors import ConversionError
from markweave.presentations.models import PresentationDialect, PresentationOptions
from markweave.presentations.preparation import plan_presentation

pytestmark = pytest.mark.unit


def plan(text: str, **options):
    return plan_presentation(
        ApprovedDocument(text, PurePosixPath("document.md"), ()),
        PresentationOptions(**options),
    )


def test_plain_markdown_outline_preserves_content():
    result = plan("## First\n\n- Hello\n\n## Second\n\nWorld\n")
    assert result.titles == ("First", "Second")
    assert result.document.markdown == "## First\n\n- Hello\n\n## Second\n\nWorld\n"
    assert result.options.dialect is PresentationDialect.MARKDOWN


def test_marp_notes_and_explicit_breaks_with_style_warning():
    result = plan(
        "---\nmarp: true\ntheme: gaia\n---\n# One\n\n<!-- Presenter note -->\n\n---\n\n# Two\n\n<!-- _class: lead -->\n"
    )
    assert result.titles == ("One", "Two")
    assert result.explicit_breaks
    assert "::: notes\nPresenter note\n:::" in result.document.markdown
    assert "theme:" not in result.document.markdown
    assert any("Unsupported" in warning for warning in result.warnings)


def test_code_is_not_treated_as_marp_directives_or_slide_boundaries():
    source = "## Example\n\n```md\n---\n<!-- _class: lead -->\n::: executable\n```\n"
    result = plan(source, dialect=PresentationDialect.MARP)
    assert result.document.markdown == source
    assert not result.explicit_breaks
    assert result.titles == ("Example",)


@pytest.mark.parametrize(
    "source",
    [
        "<script>alert(1)</script>",
        '<img src="file:///etc/passwd">',
        "![remote](https://example.com/a.png)",
        "![escape](../a.png)",
        '::: {background-image="/etc/passwd"}\nBad\n:::',
        "---\n- not a mapping\n---\nText",
        "---\nmarp: false\n---\nText",
    ],
)
def test_unsafe_or_unsupported_input_fails_before_engine(source):
    with pytest.raises(ConversionError):
        plan(source, dialect=PresentationDialect.MARP)


@pytest.mark.parametrize("level", [0, 7, True, "2", 2.5])
def test_invalid_heading_level(level):
    with pytest.raises(ValueError):
        PresentationOptions(slide_level=level)


def test_options_are_canonical_and_closed():
    options = PresentationOptions(PresentationDialect.MARP, 3)
    assert PresentationOptions.from_json(options.canonical_json()) == options
    with pytest.raises(ValueError):
        PresentationOptions.from_json(
            '{"dialect":"auto","slide_level":2,"filter":"evil"}'
        )
    assert replace(options, slide_level=4).canonical_json() != options.canonical_json()


def test_long_slide_warns_without_truncating():
    text = "## Long\n\n" + "retained " * 200
    result = plan(text)
    assert result.document.markdown == text
    assert any("substantial" in warning for warning in result.warnings)


def test_metadata_cover_and_notes_do_not_create_false_slide_breaks():
    result = plan(
        "---\ntitle: My deck\n---\n## One\n\n::: notes\n## Hidden\n\n---\n\nMore notes\n:::\n\n## Two\n"
    )
    assert not result.explicit_breaks
    assert result.titles == ("My deck", "One", "Two")
