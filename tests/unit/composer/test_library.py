"""Deterministic library text extraction, chunking, query terms and scoring."""

from __future__ import annotations

import pytest

from markweave.composer.library import (
    CHUNK_CHARACTERS,
    MAXIMUM_SNIPPET_CHARACTERS,
    LibraryContentError,
    chunk_paragraphs,
    extract_paragraphs,
    query_terms,
    score_chunk,
)
from markweave.composer.pptx_edits import PPTX_EDIT_MEDIA_TYPE
from markweave.composer.typed_templates import TypedDocxLimits
from tests.pptx_fixtures import archive, parts, text_shape

pytestmark = [pytest.mark.unit]


def _limits() -> TypedDocxLimits:
    return TypedDocxLimits(
        max_archive_bytes=100_000,
        max_entries=50,
        max_member_bytes=100_000,
        max_total_bytes=300_000,
        max_compression_ratio=1_000,
        max_xml_elements=10_000,
        max_xml_depth=100,
        max_xml_attributes=20_000,
        max_schema_bytes=10_000,
        max_values_bytes=10_000,
        max_repeat_items=4,
        max_text_characters=100,
    )


def test_markdown_paragraphs_are_normalized_and_control_characters_removed() -> None:
    content = b"# Title\r\n\r\nFirst  line\nwrapped\x07here\n\n \n\nLast\n"

    assert extract_paragraphs(content, "text/markdown", _limits()) == (
        "# Title",
        "First line wrapped here",
        "Last",
    )


def test_presentation_text_is_read_from_slides_and_notes() -> None:
    deck = archive(
        parts(
            (text_shape("Opening slide"), text_shape("Closing slide")),
            notes=text_shape("Speaker note"),
        )
    )

    paragraphs = extract_paragraphs(deck, PPTX_EDIT_MEDIA_TYPE, _limits())

    assert any("Opening slide" in paragraph for paragraph in paragraphs)
    assert any("Speaker note" in paragraph for paragraph in paragraphs)


@pytest.mark.parametrize(
    ("content", "media_type", "code"),
    [
        (b"\xff\xfe", "text/plain", "invalid_text"),
        (b"\n \n\t\n", "text/plain", "no_text"),
        (b"%PDF-1.7", "application/pdf", "unsupported_media"),
        (b"PK not a package", PPTX_EDIT_MEDIA_TYPE, "invalid_package"),
    ],
)
def test_unreadable_sources_are_rejected(
    content: bytes, media_type: str, code: str
) -> None:
    with pytest.raises(LibraryContentError) as raised:
        extract_paragraphs(content, media_type, _limits())

    assert raised.value.code == code
    assert code.replace("_", " ") in str(raised.value)


def test_chunks_pack_whole_paragraphs_and_split_only_oversized_ones() -> None:
    small = ("a" * 500, "b" * 500, "c" * 500)
    oversized = ("d" * (CHUNK_CHARACTERS * 2 + 10),)

    assert chunk_paragraphs(small, maximum_chunks=5) == (
        f"{'a' * 500}\n\n{'b' * 500}",
        "c" * 500,
    )
    assert [len(chunk) for chunk in chunk_paragraphs(oversized, maximum_chunks=5)] == [
        CHUNK_CHARACTERS,
        CHUNK_CHARACTERS,
        10,
    ]
    assert chunk_paragraphs((), maximum_chunks=1) == ()


def test_chunk_count_is_bounded() -> None:
    with pytest.raises(LibraryContentError) as raised:
        chunk_paragraphs(("x" * CHUNK_CHARACTERS,) * 3, maximum_chunks=2)

    assert raised.value.code == "limit_exceeded"


def test_query_terms_are_distinct_case_folded_and_bounded() -> None:
    assert query_terms("Turbine turbine, STORM a") == ("turbine", "storm")
    for query in ("a", "!!", " ".join(f"term{index}" for index in range(9))):
        with pytest.raises(LibraryContentError) as raised:
            query_terms(query)
        assert raised.value.code == "invalid_query"


def test_chunks_score_only_when_every_term_matches() -> None:
    text = f"{'x' * 400} Turbine storm turbine {'y' * 400}"

    scored = score_chunk(text, ("turbine", "storm"))

    assert scored is not None
    assert scored.score == 3
    start = text.index("Turbine") - MAXIMUM_SNIPPET_CHARACTERS // 4
    assert scored.snippet == text[start : start + MAXIMUM_SNIPPET_CHARACTERS]
    assert score_chunk(text, ("turbine", "banana")) is None
