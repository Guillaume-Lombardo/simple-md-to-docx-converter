"""Bounded, surgical text edits on private copies of admitted DOCX packages.

This is the first T92 Office edit family. It only replaces the text of one
eligible ``w:t`` node per operation in ``word/document.xml``. The edit splices
the exact node bytes, so every other byte of that part and every other package
member is preserved and verified after the write.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from markweave.composer.office_edits import (
    MC_NS,
    OFFICE_EDIT_ENGINE_VERSION,
    XML_SPACE,
    DocxEditError,
    PackageKind,
    ReplaceText,
    TextGrammar,
    TextPart,
    apply_text_edits,
    inspect_text,
    operations_record,
)
from markweave.composer.typed_templates import TypedDocxLimits

__all__ = [
    "DOCX_EDIT_ENGINE_VERSION",
    "DOCX_EDIT_MEDIA_TYPE",
    "DocxEditError",
    "DocxTextTarget",
    "ReplaceText",
    "apply_docx_text_edits",
    "inspect_docx_text",
    "operations_record",
]

DOCX_EDIT_ENGINE_VERSION = OFFICE_EDIT_ENGINE_VERSION
DOCX_EDIT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MAIN = "word/document.xml"
_GRAMMAR = TextGrammar(
    text=f"{_W_NS} t",
    run=f"{_W_NS} r",
    paragraph=f"{_W_NS} p",
    table=f"{_W_NS} tbl",
    # Text inside these containers is owned by another mechanism (tracked
    # changes, content-control bindings, field results or alternate
    # renderings) and would be overwritten or diverge on reopening, so it is
    # never offered as a target.
    excluded_ancestors=frozenset(
        {
            f"{MC_NS} AlternateContent",
            f"{_W_NS} sdt",
            f"{_W_NS} ins",
            f"{_W_NS} del",
            f"{_W_NS} moveFrom",
            f"{_W_NS} moveTo",
            f"{_W_NS} fldSimple",
        }
    ),
    allowed_attributes=frozenset({XML_SPACE}),
    field_character=f"{_W_NS} fldChar",
    field_type=f"{_W_NS} fldCharType",
    adds_space_preserve=True,
)


def _document_part(
    _payloads: dict[str, bytes], _roots: dict[str, ElementTree.Element]
) -> tuple[TextPart, ...]:
    return (TextPart(_MAIN),)


DOCX_PACKAGE = PackageKind(
    main_part=_MAIN,
    root_tag=f"{{{_W_NS}}}document",
    error=DocxEditError,
    grammar=_GRAMMAR,
    text_parts=_document_part,
)


@dataclass(frozen=True, slots=True)
class DocxTextTarget:
    """One editable text node with a stable identity inside its exact package."""

    target: str
    paragraph: int
    in_table: bool
    text: str


def inspect_docx_text(
    content: bytes, limits: TypedDocxLimits
) -> tuple[DocxTextTarget, ...]:
    """Admit an untrusted DOCX copy and list its editable body text targets."""

    return tuple(
        DocxTextTarget(item.target, item.paragraph, item.in_table, item.text)
        for item in inspect_text(content, limits, DOCX_PACKAGE)
    )


def apply_docx_text_edits(
    content: bytes, operations: tuple[ReplaceText, ...], limits: TypedDocxLimits
) -> bytes:
    """Return a new DOCX in which only the requested text nodes changed."""

    return apply_text_edits(content, operations, limits, DOCX_PACKAGE)
