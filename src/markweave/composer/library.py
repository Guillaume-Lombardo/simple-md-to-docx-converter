"""Bounded text extraction, citable chunks and search scoring for the library.

Library documents are read only by these deterministic parsers after a malware
scan. Their text never grants access by itself: callers filter authorization
before any chunk is returned, transmitted, or used to support a citation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from markweave.composer.docx_edits import DOCX_EDIT_MEDIA_TYPE, DOCX_PACKAGE
from markweave.composer.office_edits import OfficeEditError, text_lines
from markweave.composer.pptx_edits import PPTX_EDIT_MEDIA_TYPE, PPTX_PACKAGE
from markweave.composer.typed_templates import TypedDocxLimits

LIBRARY_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": DOCX_EDIT_MEDIA_TYPE,
    ".pptx": PPTX_EDIT_MEDIA_TYPE,
}
CHUNK_CHARACTERS = 1_200
MAXIMUM_QUERY_TERMS = 8
MAXIMUM_SNIPPET_CHARACTERS = 320
_OFFICE_KINDS = {
    DOCX_EDIT_MEDIA_TYPE: DOCX_PACKAGE,
    PPTX_EDIT_MEDIA_TYPE: PPTX_PACKAGE,
}
_TERM = re.compile(r"\w{2,}", re.UNICODE)


class LibraryContentError(ValueError):
    """A library source cannot be parsed into citable text."""

    def __init__(self, code: str) -> None:
        super().__init__(f"Library content {code.replace('_', ' ')}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """One authorized chunk ranked for a query, with a bounded snippet."""

    score: int
    snippet: str


def normalize_search(value: str) -> str:
    """Fold text the same way for storage, SQL prefiltering, and scoring."""

    return unicodedata.normalize("NFKC", value).casefold()


def _readable(value: str) -> str:
    return "".join(
        " " if unicodedata.category(character) in {"Cc", "Cs"} else character
        for character in value
    )


def extract_paragraphs(
    content: bytes, media_type: str, limits: TypedDocxLimits
) -> tuple[str, ...]:
    """Return non-empty readable paragraphs from an admitted library source."""

    if media_type in _OFFICE_KINDS:
        try:
            lines = text_lines(content, limits, _OFFICE_KINDS[media_type])
        except OfficeEditError as exc:
            raise LibraryContentError(exc.code) from exc
    elif media_type in {"text/markdown", "text/plain"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LibraryContentError("invalid_text") from exc
        lines = tuple(re.split(r"\n\s*\n", text.replace("\r\n", "\n")))
    else:
        raise LibraryContentError("unsupported_media")
    paragraphs = tuple(
        cleaned for line in lines if (cleaned := " ".join(_readable(line).split()))
    )
    if not paragraphs:
        raise LibraryContentError("no_text")
    return paragraphs


def chunk_paragraphs(
    paragraphs: tuple[str, ...], *, maximum_chunks: int
) -> tuple[str, ...]:
    """Pack whole paragraphs into bounded chunks; split only oversized ones."""

    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [
            paragraph[start : start + CHUNK_CHARACTERS]
            for start in range(0, len(paragraph), CHUNK_CHARACTERS)
        ]
        for piece in pieces:
            if current and len(current) + 2 + len(piece) > CHUNK_CHARACTERS:
                chunks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    if len(chunks) > maximum_chunks:
        raise LibraryContentError("limit_exceeded")
    return tuple(chunks)


def query_terms(query: str) -> tuple[str, ...]:
    """Return distinct case-folded search terms, bounded in number."""

    terms: list[str] = []
    for match in _TERM.finditer(normalize_search(query)):
        if match.group() not in terms:
            terms.append(match.group())
    if not terms or len(terms) > MAXIMUM_QUERY_TERMS:
        raise LibraryContentError("invalid_query")
    return tuple(terms)


def score_chunk(
    text: str, search_text: str, terms: tuple[str, ...]
) -> ScoredChunk | None:
    """Rank a chunk containing every term, with a snippet near the first match.

    ``search_text`` is ``normalize_search(text)``. Folding can change lengths
    (``ß`` becomes ``ss``), so the match offset is mapped back onto the text.
    """

    positions = [search_text.find(term) for term in terms]
    if any(position < 0 for position in positions):
        return None
    score = sum(search_text.count(term) for term in terms)
    first = _original_offset(text, min(positions))
    start = max(0, first - MAXIMUM_SNIPPET_CHARACTERS // 4)
    snippet = text[start : start + MAXIMUM_SNIPPET_CHARACTERS]
    return ScoredChunk(score, snippet)


def _original_offset(text: str, folded_offset: int) -> int:
    folded = 0
    for index, character in enumerate(text):
        if folded >= folded_offset:
            return index
        folded += len(normalize_search(character))
    return len(text)
