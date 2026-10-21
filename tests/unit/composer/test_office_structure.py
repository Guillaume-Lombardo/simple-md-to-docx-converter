"""Bounded structural Office edits on synthetic in-memory DOCX and PPTX packages."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image
from pytest_mock import MockerFixture

from markweave.composer import office_structure
from markweave.composer.docx_edits import DOCX_PACKAGE
from markweave.composer.office_edits import DocxEditError, PptxEditError
from markweave.composer.office_structure import (
    MoveSlide,
    ReplaceImage,
    SetParagraphStyle,
    apply_structure_edits,
    inspect_structure,
    prepare_image,
    structure_record,
)
from markweave.composer.pptx_edits import PPTX_PACKAGE
from markweave.composer.typed_templates import TypedDocxLimits
from markweave.conversion.images import ImageLimits
from tests.pptx_fixtures import archive, parts, text_shape

pytestmark = [pytest.mark.unit]

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MAIN = "word/document.xml"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
_IMAGE_LIMITS = ImageLimits(1_000_000, 2_000, 2_000, 4_000_000, 1_000, 32)


def _limits() -> TypedDocxLimits:
    return TypedDocxLimits(
        max_archive_bytes=200_000,
        max_entries=50,
        max_member_bytes=100_000,
        max_total_bytes=400_000,
        max_compression_ratio=1_000,
        max_xml_elements=10_000,
        max_xml_depth=100,
        max_xml_attributes=20_000,
        max_schema_bytes=10_000,
        max_values_bytes=10_000,
        max_repeat_items=4,
        max_text_characters=100,
    )


def _png(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format="PNG")
    return output.getvalue()


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (0, 0, 255)).save(output, format="JPEG")
    return output.getvalue()


_STYLES = (
    f'<w:styles xmlns:w="{_W}">'
    '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
    "</w:style>"
    '<w:style w:type="paragraph" w:styleId="Heading1">'
    '<w:name w:val="heading 1"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Quote"/>'
    '<w:style w:type="character" w:styleId="Strong"/>'
    "</w:styles>"
)
_BODY = (
    "<w:p><w:pPr><w:pStyle w:val='Normal'/><w:jc w:val='left'/></w:pPr>"
    "<w:r><w:t>Styled</w:t></w:r></w:p>"
    "<w:p><w:pPr><w:jc w:val='left'/></w:pPr><w:r><w:t>Unstyled</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Bare</w:t></w:r></w:p>"
    "<w:p w:rsidR='00AB'/>"
    "<w:p><w:pPr/><w:r><w:t>Empty properties</w:t></w:r></w:p>"
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    "<w:sdt><w:sdtContent><w:p><w:r><w:t>Control</w:t></w:r></w:p>"
    "</w:sdtContent></w:sdt>"
    "<w:p><w:pPr><w:pStyle w:val='Normal'/><w:pPrChange w:id='1'/></w:pPr></w:p>"
    "<w:p><w:pPr><w:pStyle w:val='Normal' w:extra='1'/></w:pPr></w:p>"
)


def _docx(body: str = _BODY, *, image: bytes | None = None) -> bytes:
    relationships = (
        f'<Relationships xmlns="{_RELS}">'
        + (
            f'<Relationship Id="img" Type="{_IMAGE_REL}" Target="media/image1.png"/>'
            if image is not None
            else ""
        )
        + "</Relationships>"
    )
    members = {
        "[Content_Types].xml": (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            b'content-types"><Default Extension="rels" ContentType="application/'
            b'vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="xml" ContentType="application/xml"/>'
            b'<Default Extension="png" ContentType="image/png"/>'
            b'<Override PartName="/word/document.xml" ContentType="application/'
            b'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b"</Types>"
        ),
        "_rels/.rels": (
            f'<Relationships xmlns="{_RELS}"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"
        ).encode(),
        _MAIN: (
            f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
        ).encode(),
        "word/styles.xml": _STYLES.encode(),
        "word/_rels/document.xml.rels": relationships.encode(),
    }
    if image is not None:
        members["word/media/image1.png"] = image
    return archive(members)


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        return {name: package.read(name) for name in package.namelist()}


def _styles(content: bytes) -> list[tuple[str, str | None]]:
    return [
        (item.target, item.style)
        for item in inspect_structure(content, _limits(), DOCX_PACKAGE).paragraphs
    ]


def test_docx_inspection_lists_eligible_paragraphs_styles_and_images() -> None:
    image = _png()
    structure = inspect_structure(_docx(image=image), _limits(), DOCX_PACKAGE)

    assert [
        (item.target, item.in_table, item.style, item.text)
        for item in structure.paragraphs
    ] == [
        (f"{_MAIN}#p0", False, "Normal", "Styled"),
        (f"{_MAIN}#p1", False, None, "Unstyled"),
        (f"{_MAIN}#p2", False, None, "Bare"),
        (f"{_MAIN}#p3", False, None, ""),
        (f"{_MAIN}#p4", False, None, "Empty properties"),
        (f"{_MAIN}#p5", True, None, "Cell"),
    ]
    assert [(item.style_id, item.name) for item in structure.paragraph_styles] == [
        ("Normal", "Normal"),
        ("Heading1", "heading 1"),
        ("Quote", "Quote"),
    ]
    assert [(item.target, item.media_type, item.size) for item in structure.images] == [
        ("word/media/image1.png", "image/png", len(image))
    ]
    assert structure.images[0].sha256 == hashlib.sha256(image).hexdigest()
    assert structure.slides == ()


def test_paragraph_styles_are_spliced_for_every_declaration_shape() -> None:
    source = _docx()
    operations = tuple(
        SetParagraphStyle(f"{_MAIN}#p{index}", expected, "Heading1")
        for index, expected in (
            (0, "Normal"),
            (1, None),
            (2, None),
            (3, None),
            (4, None),
        )
    )

    edited = apply_structure_edits(source, operations, _limits(), DOCX_PACKAGE)

    before, after = _members(source), _members(edited)
    assert {name for name in before if before[name] != after[name]} == {_MAIN}
    assert _styles(edited)[:6] == [
        (f"{_MAIN}#p{index}", "Heading1") for index in range(5)
    ] + [(f"{_MAIN}#p5", None)]
    document = after[_MAIN]
    assert b"<w:pPr><w:pStyle w:val=\"Heading1\"/><w:jc w:val='left'/></w:pPr>" in (
        document
    )
    assert (
        b"<w:p w:rsidR='00AB'><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr></w:p>"
        in (document)
    )
    assert b'<w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Empty' in document
    record = json.loads(structure_record(operations))
    assert record["operations"][1] == {
        "op": "set_paragraph_style",
        "target": f"{_MAIN}#p1",
        "expected_style": None,
        "style": "Heading1",
    }


def test_style_values_are_escaped_and_prefixes_follow_the_document() -> None:
    body = "<x:p><x:r><x:t>Prefixed</x:t></x:r></x:p>"
    document = f'<x:document xmlns:x="{_W}"><x:body>{body}</x:body></x:document>'
    members = _members(_docx())
    members[_MAIN] = document.encode()
    members["word/styles.xml"] = (
        f'<w:styles xmlns:w="{_W}"><w:style w:type="paragraph" w:styleId="a&amp;b"/>'
        "</w:styles>"
    ).encode()
    source = archive(members)

    edited = apply_structure_edits(
        source,
        (SetParagraphStyle(f"{_MAIN}#p0", None, "a&b"),),
        _limits(),
        DOCX_PACKAGE,
    )

    assert b'<x:pPr><x:pStyle x:val="a&amp;b"/></x:pPr>' in _members(edited)[_MAIN]
    assert _styles(edited) == [(f"{_MAIN}#p0", "a&b")]


def test_default_namespace_paragraphs_get_qualified_style_attributes() -> None:
    other = "urn:example:other"
    paragraphs = (
        "<p><r><t>Bare</t></r></p>",
        f'<p><pPr><pStyle xmlns:s="{_W}" s:val="Normal"/></pPr><r><t>A</t></r></p>',
        "<p><pPr/><r><t>Empty</t></r></p>",
        "<p/>",
        f'<p xmlns:w="{other}"><pPr xmlns:q="{_W}"><jc q:val="left"/></pPr>'
        "<r><t>Scoped</t></r></p>",
        f'<p><pPr xmlns:w="{other}"/><r><t>Rebound</t></r></p>',
    )
    members = _members(_docx())
    members[_MAIN] = (
        f'<document xmlns="{_W}"><body>{"".join(paragraphs)}</body></document>'
    ).encode()
    source = archive(members)
    expected = ("Normal" if index == 1 else None for index in range(6))
    operations = tuple(
        SetParagraphStyle(f"{_MAIN}#p{index}", style, "Heading1")
        for index, style in enumerate(expected)
    )

    edited = apply_structure_edits(source, operations, _limits(), DOCX_PACKAGE)

    declare = f'xmlns:w="{_W}"'
    style = f'<w:pStyle {declare} w:val="Heading1"/>'
    assert (
        _members(edited)[_MAIN]
        == (
            f'<document xmlns="{_W}"><body>'
            f'<p><w:pPr {declare}><w:pStyle w:val="Heading1"/></w:pPr>'
            "<r><t>Bare</t></r></p>"
            f"<p><pPr>{style}</pPr><r><t>A</t></r></p>"
            f"<p><pPr>{style}</pPr><r><t>Empty</t></r></p>"
            f'<p><w:pPr {declare}><w:pStyle w:val="Heading1"/></w:pPr></p>'
            f'<p xmlns:w="{other}"><pPr xmlns:q="{_W}"><q:pStyle q:val="Heading1"/>'
            '<jc q:val="left"/></pPr><r><t>Scoped</t></r></p>'
            f'<p><pPr xmlns:w="{other}">{style}</pPr><r><t>Rebound</t></r></p>'
            "</body></document>"
        ).encode()
    )
    assert _styles(edited) == [(f"{_MAIN}#p{index}", "Heading1") for index in range(6)]
    before, after = _members(source), _members(edited)
    assert {name for name in before if before[name] != after[name]} == {_MAIN}


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (SetParagraphStyle(f"{_MAIN}#p6", None, "Heading1"), "unsupported_target"),
        (SetParagraphStyle(f"{_MAIN}#p7", "Normal", "Heading1"), "unsupported_target"),
        (SetParagraphStyle(f"{_MAIN}#p8", "Normal", "Heading1"), "unsupported_target"),
        (SetParagraphStyle(f"{_MAIN}#p99", None, "Heading1"), "unsupported_target"),
        (SetParagraphStyle(f"{_MAIN}#p01", None, "Heading1"), "unsupported_target"),
        (
            SetParagraphStyle("word/styles.xml#p0", None, "Heading1"),
            "unsupported_target",
        ),
        (SetParagraphStyle(f"{_MAIN}#t0", None, "Heading1"), "unsupported_target"),
        (SetParagraphStyle(f"{_MAIN}#p0", "Normal", "Strong"), "unknown_style"),
        (SetParagraphStyle(f"{_MAIN}#p0", "Normal", "Missing"), "unknown_style"),
        (SetParagraphStyle(f"{_MAIN}#p0", "Heading1", "Quote"), "target_changed"),
        (SetParagraphStyle(f"{_MAIN}#p1", "Normal", "Quote"), "target_changed"),
    ],
)
def test_invalid_style_operations_fail_closed(
    operation: SetParagraphStyle, code: str
) -> None:
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(_docx(), (operation,), _limits(), DOCX_PACKAGE)

    assert raised.value.code == code


def test_duplicate_or_misplaced_operations_are_rejected() -> None:
    style = SetParagraphStyle(f"{_MAIN}#p0", "Normal", "Quote")
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(_docx(), (style, style), _limits(), DOCX_PACKAGE)
    assert raised.value.code == "duplicate_target"
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(_docx(), (), _limits(), DOCX_PACKAGE)
    assert raised.value.code == "no_operations"
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(_docx(), (MoveSlide(256, 1, 1),), _limits(), DOCX_PACKAGE)
    assert raised.value.code == "unsupported_operation"
    deck = archive(parts((text_shape("One"),)))
    with pytest.raises(PptxEditError) as rejected:
        apply_structure_edits(deck, (style,), _limits(), PPTX_PACKAGE)
    assert rejected.value.code == "unsupported_operation"


def test_images_are_replaced_only_in_their_part() -> None:
    original = _png()
    source = _docx(image=original)
    replacement = prepare_image(_jpeg(), "photo.jpeg", "image/png", _IMAGE_LIMITS)

    edited = apply_structure_edits(
        source,
        (
            ReplaceImage(
                "word/media/image1.png",
                hashlib.sha256(original).hexdigest(),
                replacement,
            ),
        ),
        _limits(),
        DOCX_PACKAGE,
    )

    before, after = _members(source), _members(edited)
    assert {name for name in before if before[name] != after[name]} == {
        "word/media/image1.png"
    }
    assert after["word/media/image1.png"] == replacement


@pytest.mark.parametrize(
    ("target", "digest", "content", "code"),
    [
        ("word/media/missing.png", "x", b"", "unsupported_target"),
        ("word/media/image1.png", "0" * 64, None, "target_changed"),
        ("word/media/image1.png", None, b"", "invalid_image"),
        ("word/media/image1.png", None, b"not an image", "invalid_image"),
        ("word/media/image1.png", None, "jpeg", "invalid_image"),
    ],
)
def test_invalid_image_operations_fail_closed(
    target: str, digest: str | None, content: bytes | str | None, code: str
) -> None:
    original = _png()
    payload = (
        _png((0, 255, 0))
        if content is None
        else _jpeg()
        if isinstance(content, str)
        else content
    )
    operation = ReplaceImage(
        target, digest or hashlib.sha256(original).hexdigest(), payload
    )
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(
            _docx(image=original), (operation,), _limits(), DOCX_PACKAGE
        )

    assert raised.value.code == code


def test_duplicate_image_targets_are_rejected() -> None:
    original = _png()
    operation = ReplaceImage(
        "word/media/image1.png", hashlib.sha256(original).hexdigest(), _png((0, 9, 9))
    )
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(
            _docx(image=original), (operation, operation), _limits(), DOCX_PACKAGE
        )
    assert raised.value.code == "duplicate_target"


def _deck(count: int = 3) -> bytes:
    return archive(
        parts(
            tuple(text_shape(f"Slide {index}") for index in range(1, count + 1)),
            image=_png(),
        )
    )


def test_slides_are_listed_in_presentation_order_and_moved() -> None:
    source = _deck()
    structure = inspect_structure(source, _limits(), PPTX_PACKAGE)
    assert [
        (item.slide_id, item.position, item.title) for item in structure.slides
    ] == [
        (1, 1, "Slide 1"),
        (2, 2, "Slide 2"),
        (3, 3, "Slide 3"),
    ]
    assert [item.target for item in structure.images] == ["ppt/media/image.png"]
    assert structure.paragraphs == ()

    edited = apply_structure_edits(
        source,
        (MoveSlide(3, 3, 1), MoveSlide(1, 2, 3)),
        _limits(),
        PPTX_PACKAGE,
    )

    before, after = _members(source), _members(edited)
    assert {name for name in before if before[name] != after[name]} == {
        "ppt/presentation.xml"
    }
    assert [
        item.slide_id
        for item in inspect_structure(edited, _limits(), PPTX_PACKAGE).slides
    ] == [3, 2, 1]
    assert (
        b'<p:sldId id="3" r:id="s3"/><p:sldId id="2" r:id="s2"/>'
        in (after["ppt/presentation.xml"])
    )


def test_slide_entries_with_children_keep_their_exact_bytes() -> None:
    members = parts((text_shape("A"), text_shape("B")))
    members["ppt/presentation.xml"] = members["ppt/presentation.xml"].replace(
        b'<p:sldId id="1" r:id="s1"/>',
        b'<p:sldId id="1" r:id="s1">\n  <p:extLst/>\n</p:sldId>',
    )
    source = archive(members)

    edited = apply_structure_edits(
        source, (MoveSlide(1, 1, 2),), _limits(), PPTX_PACKAGE
    )

    assert (
        _members(edited)["ppt/presentation.xml"].count(
            b'<p:sldId id="1" r:id="s1">\n  <p:extLst/>\n</p:sldId>'
        )
        == 1
    )
    assert [
        item.title for item in inspect_structure(edited, _limits(), PPTX_PACKAGE).slides
    ] == ["B", "A"]


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        (MoveSlide(9, 1, 2), "unsupported_target"),
        (MoveSlide(1, 1, 0), "invalid_position"),
        (MoveSlide(1, 1, 4), "invalid_position"),
        (MoveSlide(1, 2, 3), "target_changed"),
    ],
)
def test_invalid_slide_moves_fail_closed(operation: MoveSlide, code: str) -> None:
    with pytest.raises(PptxEditError) as raised:
        apply_structure_edits(_deck(), (operation,), _limits(), PPTX_PACKAGE)

    assert raised.value.code == code


def test_a_slide_moves_at_most_once_and_needs_a_slide_list() -> None:
    with pytest.raises(PptxEditError) as raised:
        apply_structure_edits(
            _deck(), (MoveSlide(1, 1, 2), MoveSlide(1, 2, 3)), _limits(), PPTX_PACKAGE
        )
    assert raised.value.code == "unsupported_target"
    with pytest.raises(PptxEditError) as raised:
        apply_structure_edits(
            archive(parts(())), (MoveSlide(1, 1, 1),), _limits(), PPTX_PACKAGE
        )
    assert raised.value.code == "unsupported_target"


def test_non_numeric_slide_identifiers_are_rejected() -> None:
    members = parts((text_shape("A"),))
    members["ppt/presentation.xml"] = members["ppt/presentation.xml"].replace(
        b'id="1"', b'id="x"'
    )
    with pytest.raises(PptxEditError) as raised:
        inspect_structure(archive(members), _limits(), PPTX_PACKAGE)
    assert raised.value.code == "invalid_package"


def test_unchanged_bytes_are_proven_after_writing(mocker: MockerFixture) -> None:
    source = _docx()
    original = office_structure._write_package

    def tamper(content: bytes, replacements: dict[str, bytes]) -> bytes:
        return original(content, {**replacements, "word/styles.xml": b"<x/>"})

    mocker.patch.object(office_structure, "_write_package", side_effect=tamper)
    with pytest.raises(DocxEditError) as raised:
        apply_structure_edits(
            source,
            (SetParagraphStyle(f"{_MAIN}#p0", "Normal", "Quote"),),
            _limits(),
            DOCX_PACKAGE,
        )
    assert raised.value.code in {"package_changed", "invalid_package"}


def test_prepared_images_are_reencoded_without_metadata() -> None:
    png = prepare_image(_jpeg(), "photo.jpg", "image/png", _IMAGE_LIMITS)
    jpeg = prepare_image(_png(), "image.png", "image/jpeg", _IMAGE_LIMITS)

    with Image.open(io.BytesIO(png)) as image:
        assert (image.format, image.info) == ("PNG", {})
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG"
    for source, filename, media_type in (
        (_png(), "image.svg", "image/png"),
        (_png(), "image.png", "image/gif"),
        (b"<svg/>", "image.png", "image/png"),
        (_png(), "image.jpg", "image/png"),
    ):
        with pytest.raises(ValueError, match="Replacement image"):
            prepare_image(source, filename, media_type, _IMAGE_LIMITS)
