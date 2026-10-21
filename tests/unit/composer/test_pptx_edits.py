"""Bounded PPTX slide and notes text edits on synthetic in-memory packages."""

from __future__ import annotations

import io
import zipfile

import pytest

from markweave.composer.office_edits import PptxEditError, ReplaceText
from markweave.composer.pptx_edits import apply_pptx_text_edits, inspect_pptx_text
from markweave.composer.typed_templates import TypedDocxLimits
from tests.pptx_fixtures import NS, R, archive, parts, relations, text_shape

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


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        return {name: package.read(name) for name in package.namelist()}


def _table(text: str) -> str:
    return (
        "<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody>"
        f"<a:p><a:r><a:t>{text}</a:t></a:r></a:p>"
        "</a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )


def _deck() -> bytes:
    return archive(
        parts(
            (text_shape("First title") + _table("Cell"), text_shape("Second title")),
            notes=text_shape("Speaker note"),
            order=(2, 1),
        )
    )


def test_targets_follow_presentation_order_with_notes_after_their_slide() -> None:
    targets = inspect_pptx_text(_deck(), _limits())

    assert [
        (item.target, item.slide, item.notes, item.paragraph, item.in_table, item.text)
        for item in targets
    ] == [
        ("ppt/slides/s2.xml#t0", 1, False, 0, False, "Second title"),
        ("ppt/slides/s1.xml#t0", 2, False, 0, False, "First title"),
        ("ppt/slides/s1.xml#t1", 2, False, 1, True, "Cell"),
        ("ppt/notesSlides/n1.xml#t0", 2, True, 0, False, "Speaker note"),
    ]


def test_edits_across_slides_and_notes_change_only_those_parts() -> None:
    source = _deck()

    edited = apply_pptx_text_edits(
        source,
        (
            ReplaceText("ppt/slides/s1.xml#t1", "Cell", " A & <B> "),
            ReplaceText("ppt/notesSlides/n1.xml#t0", "Speaker note", "New note"),
        ),
        _limits(),
    )

    before, after = _members(source), _members(edited)
    assert {name for name in before if before[name] != after[name]} == {
        "ppt/slides/s1.xml",
        "ppt/notesSlides/n1.xml",
    }
    assert b"<a:t> A &amp; &lt;B&gt; </a:t>" in after["ppt/slides/s1.xml"]
    assert b"xml:space" not in after["ppt/slides/s1.xml"]
    assert [item.text for item in inspect_pptx_text(edited, _limits())] == [
        "Second title",
        "First title",
        " A & <B> ",
        "New note",
    ]


@pytest.mark.parametrize(
    "content",
    [
        "<p:sp><p:txBody><a:p><a:fld id='{1}' type='slidenum'><a:t>1</a:t>"
        "</a:fld></a:p></p:txBody></p:sp>",
        "<mc:AlternateContent xmlns:mc='http://schemas.openxmlformats.org/"
        "markup-compatibility/2006'><mc:Choice Requires='p14'><p:sp><p:txBody>"
        "<a:p><a:r><a:t>new</a:t></a:r></a:p></p:txBody></p:sp></mc:Choice>"
        "</mc:AlternateContent>",
        "<p:sp><p:txBody><a:p><a:r><a:t>a<a:b/>b</a:t></a:r></a:p></p:txBody></p:sp>",
        "<p:sp><p:txBody><a:p><a:r><a:t xml:space='preserve'>x</a:t></a:r></a:p>"
        "</p:txBody></p:sp>",
    ],
)
def test_text_owned_by_other_mechanisms_is_not_a_target(content: str) -> None:
    source = archive(parts((content,)))

    assert inspect_pptx_text(source, _limits()) == ()
    with pytest.raises(PptxEditError) as rejected:
        apply_pptx_text_edits(
            source, (ReplaceText("ppt/slides/s1.xml#t0", "x", "y"),), _limits()
        )
    assert rejected.value.code == "unsupported_target"
    assert str(rejected.value) == "PPTX edit unsupported target"


@pytest.mark.parametrize(
    "target",
    ["ppt/presentation.xml#t0", "ppt/slides/s9.xml#t0", "ppt/slides/s1.xml#t7"],
)
def test_targets_outside_slide_text_are_rejected(target: str) -> None:
    with pytest.raises(PptxEditError) as rejected:
        apply_pptx_text_edits(_deck(), (ReplaceText(target, "x", "y"),), _limits())
    assert rejected.value.code == "unsupported_target"
    assert rejected.value.targets == (target,)


def test_empty_presentation_has_no_targets() -> None:
    members = parts(())
    members["ppt/presentation.xml"] = f"<p:presentation {NS}/>".encode()

    assert inspect_pptx_text(archive(members), _limits()) == ()


def _broken(case: str) -> dict[str, bytes]:
    members = parts((text_shape("One"), text_shape("Two")), notes=text_shape("N"))
    if case == "unknown_relationship":
        members["ppt/presentation.xml"] = (
            f'<p:presentation {NS}><p:sldIdLst><p:sldId id="1" r:id="missing"/>'
            "</p:sldIdLst></p:presentation>"
        ).encode()
    elif case == "wrong_type":
        members["ppt/_rels/presentation.xml.rels"] = relations(
            (("s1", "theme", "slides/s1.xml"), ("s2", "slide", "slides/s2.xml"))
        )
    elif case == "duplicate_slide":
        members["ppt/presentation.xml"] = (
            f'<p:presentation {NS}><p:sldIdLst><p:sldId id="1" r:id="s1"/>'
            '<p:sldId id="2" r:id="s1"/></p:sldIdLst></p:presentation>'
        ).encode()
    elif case == "wrong_slide_root":
        members["ppt/slides/s2.xml"] = f"<p:notes {NS}/>".encode()
    elif case == "missing_slide":
        members["ppt/_rels/presentation.xml.rels"] = relations(
            (("s1", "slide", "slides/s1.xml"), ("s2", "slide", "slides/s3.xml"))
        )
    elif case == "external_slide":
        members["ppt/_rels/presentation.xml.rels"] = (
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="s1" Type="{R}/slide" '
            'Target="slides/s1.xml"/><Relationship Id="s2" Type="'
            f'{R}/hyperlink" Target="https://example.invalid/" '
            'TargetMode="External"/></Relationships>'
        ).encode()
        members["ppt/presentation.xml"] = (
            f'<p:presentation {NS}><p:sldIdLst><p:sldId id="1" r:id="s2"/>'
            "</p:sldIdLst></p:presentation>"
        ).encode()
    elif case == "two_notes":
        members["ppt/slides/_rels/s1.xml.rels"] = relations(
            (
                ("n1", "notesSlide", "../notesSlides/n1.xml"),
                ("n2", "notesSlide", "../notesSlides/n1.xml"),
            )
        )
    elif case == "shared_notes":
        members["ppt/slides/_rels/s2.xml.rels"] = relations(
            (("notes", "notesSlide", "../notesSlides/n1.xml"),)
        )
    else:
        members["ppt/notesSlides/n1.xml"] = f"<p:sld {NS}/>".encode()
    return members


@pytest.mark.parametrize(
    "case",
    [
        "unknown_relationship",
        "wrong_type",
        "duplicate_slide",
        "wrong_slide_root",
        "missing_slide",
        "external_slide",
        "two_notes",
        "shared_notes",
        "wrong_notes_root",
    ],
)
def test_inconsistent_slide_structure_fails_closed(case: str) -> None:
    with pytest.raises(PptxEditError) as rejected:
        inspect_pptx_text(archive(_broken(case)), _limits())
    assert rejected.value.code == "invalid_package"


def test_wrong_presentation_root_fails_closed() -> None:
    members = parts((text_shape("One"),))
    members["ppt/presentation.xml"] = f"<p:sld {NS}/>".encode()

    with pytest.raises(PptxEditError) as rejected:
        inspect_pptx_text(archive(members), _limits())
    assert rejected.value.code == "invalid_package"
