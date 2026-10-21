"""Bounded, surgical text edits on slides and speaker notes of admitted PPTX copies.

Slides are taken in presentation order from ``p:sldIdLst``; each slide's notes
part follows it. Only whole ``a:t`` text runs are replaced; field text,
alternate content, and any run containing markup are never targets, and every
other package byte is preserved and verified after the write.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree import ElementTree

from markweave.composer.office_edits import (
    MC_NS,
    PackageKind,
    PptxEditError,
    ReplaceText,
    TextGrammar,
    TextPart,
    apply_text_edits,
    inspect_text,
    internal_target,
)
from markweave.composer.typed_templates import TypedDocxLimits

PPTX_EDIT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAIN = "ppt/presentation.xml"
_SLIDE_REL = f"{_R_NS}/slide"
_NOTES_REL = f"{_R_NS}/notesSlide"
_GRAMMAR = TextGrammar(
    text=f"{_A_NS} t",
    run=f"{_A_NS} r",
    paragraph=f"{_A_NS} p",
    table=f"{_A_NS} tbl",
    # Field text (slide numbers, dates) sits under ``a:fld`` rather than a run,
    # so the run-parent rule already excludes it.
    excluded_ancestors=frozenset({f"{MC_NS} AlternateContent"}),
    allowed_attributes=frozenset(),
)


def _relationships(
    roots: dict[str, ElementTree.Element], part: str
) -> list[ElementTree.Element]:
    folder, _, filename = part.rpartition("/")
    root = roots.get(f"{folder}/_rels/{filename}.rels")
    return [] if root is None else list(root.iter(f"{{{_REL_NS}}}Relationship"))


def _internal(node: ElementTree.Element, source: str) -> str:
    # Admission already rejected external non-hyperlink relationships and
    # internal targets that name no package part.
    return internal_target(source, node.get("Target", ""))


def _slide_parts(
    _payloads: dict[str, bytes], roots: dict[str, ElementTree.Element]
) -> tuple[TextPart, ...]:
    """Return slides in presentation order, each followed by its notes part."""

    targets = {node.get("Id"): node for node in _relationships(roots, _MAIN)}
    slide_list = roots[_MAIN].find(f"{{{_P_NS}}}sldIdLst")
    entries = [] if slide_list is None else list(slide_list)
    parts: list[TextPart] = []
    seen: set[str] = set()
    for number, entry in enumerate(entries, start=1):
        relationship = targets.get(entry.get(f"{{{_R_NS}}}id"))
        if relationship is None or relationship.get("Type") != _SLIDE_REL:
            raise PptxEditError("invalid_package")
        slide = _internal(relationship, _MAIN)
        if slide in seen or roots[slide].tag != f"{{{_P_NS}}}sld":
            raise PptxEditError("invalid_package")
        seen.add(slide)
        parts.append(TextPart(slide, number))
        notes = [
            node
            for node in _relationships(roots, slide)
            if node.get("Type") == _NOTES_REL
        ]
        if len(notes) > 1:
            raise PptxEditError("invalid_package")
        if notes:
            name = _internal(notes[0], slide)
            if name in seen or roots[name].tag != f"{{{_P_NS}}}notes":
                raise PptxEditError("invalid_package")
            seen.add(name)
            parts.append(TextPart(name, number, notes=True))
    return tuple(parts)


PPTX_PACKAGE = PackageKind(
    main_part=_MAIN,
    root_tag=f"{{{_P_NS}}}presentation",
    error=PptxEditError,
    grammar=_GRAMMAR,
    text_parts=_slide_parts,
)


@dataclass(frozen=True, slots=True)
class PptxTextTarget:
    """One editable slide or notes text run with its stable identity."""

    target: str
    slide: int
    notes: bool
    paragraph: int
    in_table: bool
    text: str


def inspect_pptx_text(
    content: bytes, limits: TypedDocxLimits
) -> tuple[PptxTextTarget, ...]:
    """Admit an untrusted PPTX copy and list its editable slide and notes text."""

    return tuple(
        PptxTextTarget(
            item.target,
            item.part.slide or 0,
            item.part.notes,
            item.paragraph,
            item.in_table,
            item.text,
        )
        for item in inspect_text(content, limits, PPTX_PACKAGE)
    )


def apply_pptx_text_edits(
    content: bytes, operations: tuple[ReplaceText, ...], limits: TypedDocxLimits
) -> bytes:
    """Return a new PPTX in which only the requested text runs changed."""

    return apply_text_edits(content, operations, limits, PPTX_PACKAGE)
