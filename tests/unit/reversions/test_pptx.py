"""Structured PPTX ordering, literal serialization and bounded input tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace

import pytest

from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.options import ReverseExtraction, ReversionOptions
from markweave.reversions.pptx import read_pptx, render_pptx
from tests.pptx_fixtures import (
    LIMITS,
    NS,
    archive,
    limits,
    parts,
    picture,
    relations,
    text_shape,
)

pytestmark = pytest.mark.unit
OPTIONS = ReversionOptions(ReverseExtraction.SLIDES)


def test_presentation_order_and_empty_slides_are_retained() -> None:
    source = archive(
        parts((text_shape("First"), "", text_shape("Third")), order=(3, 2, 1))
    )
    markdown = render_pptx(source, OPTIONS, LIMITS).markdown
    assert markdown == "Third\n\n---\n\n<!-- Empty slide 2. -->\n\n---\n\nFirst\n"


@pytest.mark.parametrize(
    "extraction", [ReverseExtraction.SLIDES, ReverseExtraction.MARP]
)
def test_notes_are_separate_literal_content_and_can_be_excluded(
    extraction: ReverseExtraction,
) -> None:
    source = archive(
        parts(
            (text_shape("Slide"),), notes=text_shape("--> <!-- _class: evil\n::: notes")
        )
    )
    options = ReversionOptions(extraction)
    markdown = render_pptx(source, options, LIMITS).markdown
    assert "\\-\\-\\>" in markdown
    assert "&lt;\\!\\-\\-" in markdown
    assert "\\_class\\:" in markdown
    assert "\\:\\:\\: notes" in markdown
    assert "<!-- _class:" not in markdown
    assert ("::: notes\n" in markdown) == (extraction is ReverseExtraction.SLIDES)
    assert markdown.startswith("---\nmarp: true\n---\n\n") == (
        extraction is ReverseExtraction.MARP
    )
    excluded = render_pptx(
        source, replace(options, include_notes=False), LIMITS
    ).markdown
    assert "evil" not in excluded


def test_groups_bullets_tables_and_notes_decorations() -> None:
    bullet = text_shape(
        "Point",
        paragraph_properties='<a:pPr lvl="1"><a:buChar char="*"/></a:pPr>',
        run_properties='<a:rPr b="1" i="true"/>',
    )
    cell = (
        "<a:tc><a:txBody><a:p><a:r><a:t>Cell | data</a:t></a:r></a:p></a:txBody></a:tc>"
    )
    table = f"<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr>{cell}</a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    notes = text_shape("Keep") + text_shape("123", placeholder='<p:ph type="sldNum"/>')
    source = archive(parts((f"<p:grpSp>{bullet}</p:grpSp>{table}",), notes=notes))
    markdown = render_pptx(source, OPTIONS, LIMITS).markdown
    assert "  - ***Point***" in markdown
    assert r"Cell \| data" in markdown
    assert "Keep" in markdown and "123" not in markdown


def test_unsupported_objects_warn_without_failing_or_losing_neighboring_text() -> None:
    content = (
        text_shape("Before")
        + "<p:graphicFrame/><p:sp/><p:unknown/>"
        + text_shape("After")
    )
    markdown = render_pptx(archive(parts((content,))), OPTIONS, LIMITS).markdown
    assert "Before" in markdown and "After" in markdown
    assert markdown.count("Warning: unsupported") == 3


def test_hidden_slides_are_included_with_warning() -> None:
    members = parts((text_shape("Hidden"),))
    members["ppt/slides/s1.xml"] = members["ppt/slides/s1.xml"].replace(
        b"<p:sld ", b'<p:sld show="0" '
    )
    markdown = render_pptx(archive(members), OPTIONS, LIMITS).markdown
    assert "Hidden" in markdown and "hidden in the source" in markdown


def test_omitted_image_is_explicit_and_does_not_retain_asset() -> None:
    source = archive(parts((picture(),), image=b"not read when omitted"))
    result = render_pptx(source, replace(OPTIONS, include_images=False), LIMITS)
    assert "[Image omitted by request.]" in result.markdown
    assert not result.normalized.assets and not result.normalized.references


def test_unsupported_image_is_positioned_as_unavailable() -> None:
    source = archive(
        parts(
            (text_shape("Before") + picture() + text_shape("After"),),
            image=b"metafile",
            image_type="image/x-emf",
        )
    )
    result = render_pptx(source, OPTIONS, LIMITS)
    assert result.normalized.unavailable_asset_count == 1
    assert not result.normalized.assets
    assert (
        result.markdown.index("Before")
        < result.markdown.index("unavailable")
        < result.markdown.index("After")
    )


@pytest.mark.parametrize(
    "name", ["../evil", "/absolute", "a//b", "a/./b", "a\\b", "a:%20", "a\x01b"]
)
def test_unsafe_archive_names_fail_closed(name: str) -> None:
    members = parts()
    members[name] = b"bad"
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(archive(members), OPTIONS, LIMITS)
    assert raised.value.category is ReverseErrorCategory.MALFORMED


def test_duplicate_zip_names_are_rejected() -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("same", "one")
        with pytest.warns(UserWarning):
            z.writestr("same", "two")
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(data.getvalue(), OPTIONS, LIMITS)


@pytest.mark.parametrize(
    "field",
    [
        "max_pptx_archive_entries",
        "max_pptx_member_bytes",
        "max_pptx_uncompressed_bytes",
        "max_pptx_xml_elements",
        "max_pptx_xml_depth",
        "max_pptx_xml_attributes",
    ],
)
def test_every_reader_bound_is_enforced(field: str) -> None:
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(
            archive(parts((text_shape("Bounded"),))), OPTIONS, limits(**{field: 1})
        )
    assert raised.value.category is ReverseErrorCategory.RESOURCE_LIMIT


@pytest.mark.parametrize(
    "xml",
    [
        b'<!DOCTYPE x [<!ENTITY e "secret">]><x>&e;</x>',
        b'<!DOCTYPE x SYSTEM "file:///etc/passwd"><x/>',
        b"<broken>",
    ],
)
def test_hostile_xml_is_rejected(xml: bytes) -> None:
    members = parts()
    members["ppt/presentation.xml"] = xml
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(members), OPTIONS, LIMITS)


@pytest.mark.parametrize(
    "target",
    [
        "../../../outside.xml",
        "file:///etc/passwd",
        "https://example.test/a",
        "/absolute",
        "../media/%2e%2e/a",
        "..\\outside",
    ],
)
def test_unsafe_relationships_are_rejected(target: str) -> None:
    members = parts()
    members["ppt/_rels/presentation.xml.rels"] = relations((("s1", "slide", target),))
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(members), OPTIONS, LIMITS)


@pytest.mark.parametrize("mode", [True, False])
def test_external_images_fail_even_when_image_output_disabled(mode: bool) -> None:
    members = parts((picture(),), image=b"irrelevant")
    members["ppt/slides/_rels/s1.xml.rels"] = relations(
        (("image", "image", "https://example.test/p.png"),)
    ).replace(b"Target=", b'TargetMode="External" Target=')
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(archive(members), replace(OPTIONS, include_images=mode), LIMITS)
    assert raised.value.category is ReverseErrorCategory.ASSET_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-slide",
        "duplicate-slide",
        "missing-relation",
        "wrong-main-type",
        "duplicate-id",
        "wrong-slide-root",
    ],
)
def test_malformed_presentation_graph_is_rejected(mutation: str) -> None:
    members = parts()
    if mutation == "missing-slide":
        del members["ppt/slides/s1.xml"]
    elif mutation == "duplicate-slide":
        members["ppt/_rels/presentation.xml.rels"] = relations(
            (("s1", "slide", "slides/s1.xml"), ("s2", "slide", "slides/s1.xml"))
        )
    elif mutation == "missing-relation":
        del members["ppt/_rels/presentation.xml.rels"]
    elif mutation == "wrong-main-type":
        members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
            b"presentation.main+xml", b"slideshow.main+xml"
        )
    elif mutation == "duplicate-id":
        members["ppt/presentation.xml"] = members["ppt/presentation.xml"].replace(
            b'id="2"', b'id="1"'
        )
    else:
        members["ppt/slides/s1.xml"] = f"<p:notes {NS}/>".encode()
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(members), OPTIONS, LIMITS)


def test_safe_hyperlinks_are_retained_without_loading_resources() -> None:
    shape = text_shape(
        "Link", run_properties='<a:rPr><a:hlinkClick r:id="link"/></a:rPr>'
    )
    members = parts((shape,))
    members["ppt/slides/_rels/s1.xml.rels"] = relations(
        (("link", "hyperlink", "https://example.test/a"),)
    ).replace(b"Target=", b'TargetMode="External" Target=')
    result = render_pptx(archive(members), OPTIONS, LIMITS)
    assert "[Link](<https://example.test/a>)" in result.markdown


@pytest.mark.parametrize(
    "mutation",
    [
        "content-types-root",
        "content-type-unknown",
        "content-type-duplicate",
        "missing-main",
        "presentation-root",
        "missing-list",
        "empty-list",
        "slide-tree",
        "notes-root",
        "duplicate-notes",
    ],
)
def test_malformed_parts_are_content_free_failures(mutation: str) -> None:
    members = parts((text_shape("Private title"),), notes=text_shape("Private note"))
    if mutation == "content-types-root":
        members["[Content_Types].xml"] = b"<wrong/>"
    elif mutation == "content-type-unknown":
        members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
            b"</Types>", b"<Unknown/></Types>"
        )
    elif mutation == "content-type-duplicate":
        members["[Content_Types].xml"] = members["[Content_Types].xml"].replace(
            b"</Types>", b'<Default Extension="xml" ContentType="duplicate"/></Types>'
        )
    elif mutation == "missing-main":
        members["_rels/.rels"] = relations(())
    elif mutation == "presentation-root":
        members["ppt/presentation.xml"] = b"<wrong/>"
    elif mutation == "missing-list":
        members["ppt/presentation.xml"] = f"<p:presentation {NS}/>".encode()
    elif mutation == "empty-list":
        members["ppt/presentation.xml"] = (
            f"<p:presentation {NS}><p:sldIdLst/></p:presentation>".encode()
        )
    elif mutation == "slide-tree":
        members["ppt/slides/s1.xml"] = f"<p:sld {NS}/>".encode()
    elif mutation == "notes-root":
        members["ppt/notesSlides/n1.xml"] = b"<wrong/>"
    else:
        members["ppt/slides/_rels/s1.xml.rels"] = relations(
            (
                ("notes1", "notesSlide", "../notesSlides/n1.xml"),
                ("notes2", "notesSlide", "../notesSlides/n1.xml"),
            )
        )
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(archive(members), OPTIONS, LIMITS)
    assert raised.value.category is ReverseErrorCategory.MALFORMED
    assert str(raised.value) == "The document is malformed."


@pytest.mark.parametrize(
    "mutation",
    ["wrong-root", "duplicate-id", "empty-id", "empty-kind", "bad-mode", "wrong-child"],
)
def test_relationship_metadata_is_strict(mutation: str) -> None:
    members = parts()
    value = members["ppt/_rels/presentation.xml.rels"]
    if mutation == "wrong-root":
        value = b"<wrong/>"
    elif mutation == "duplicate-id":
        value = value.replace(b'Id="s2"', b'Id="s1"')
    elif mutation == "empty-id":
        value = value.replace(b'Id="s1"', b'Id=""')
    elif mutation == "empty-kind":
        value = value.replace(
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"',
            b'Type=""',
        )
    elif mutation == "bad-mode":
        value = value.replace(b"Target=", b'TargetMode="Other" Target=')
    else:
        value = value.replace(b"<Relationship ", b"<Other ")
    members["ppt/_rels/presentation.xml.rels"] = value
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(members), OPTIONS, LIMITS)


@pytest.mark.parametrize("level", ["no", "-1", "9"])
def test_invalid_bullet_level_is_not_coerced(level: str) -> None:
    shape = text_shape(
        "Item",
        paragraph_properties=f'<a:pPr lvl="{level}"><a:buChar char="*"/></a:pPr>',
    )
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(parts((shape,))), OPTIONS, LIMITS)


def test_numbered_list_line_break_fields_and_internal_link_text() -> None:
    shape = text_shape(
        "Item", paragraph_properties='<a:pPr><a:buAutoNum type="arabicPeriod"/></a:pPr>'
    )
    shape = shape.replace(
        "</a:p>",
        '<a:br/><a:fld><a:rPr><a:hlinkClick r:id="next"/></a:rPr><a:t>Next</a:t></a:fld></a:p>',
    )
    members = parts((shape,))
    members["ppt/slides/_rels/s1.xml.rels"] = relations((("next", "slide", "s1.xml"),))
    markdown = render_pptx(archive(members), OPTIONS, LIMITS).markdown
    assert "1. Item  \nNext (internal presentation link)" in markdown


def test_missing_hyperlink_is_malformed() -> None:
    shape = text_shape(
        "Item", run_properties='<a:rPr><a:hlinkClick r:id="missing"/></a:rPr>'
    )
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(archive(parts((shape,))), OPTIONS, LIMITS)


def test_empty_and_merged_tables_are_explicit() -> None:
    empty = "<p:graphicFrame><a:tbl/></p:graphicFrame>"
    table = '<p:graphicFrame><a:tbl><a:tr><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>Merged</a:t></a:r></a:p></a:txBody></a:tc><a:tc hMerge="1"/></a:tr><a:tr/></a:tbl></p:graphicFrame>'
    markdown = render_pptx(archive(parts((empty + table,))), OPTIONS, LIMITS).markdown
    assert "unsupported table" in markdown
    assert "merged table cells were flattened; text is retained" in markdown
    assert "Merged" in markdown


def test_alternate_content_and_embedded_media_have_positioned_warnings() -> None:
    alternate = (
        '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:Choice/><mc:Fallback>'
        + text_shape("Fallback")
        + "</mc:Fallback></mc:AlternateContent>"
    )
    video = text_shape("Video poster").replace(
        "<p:nvPr>", '<p:nvPr><a:videoFile r:link="movie"/>'
    )
    source = archive(
        parts((text_shape("Before") + alternate + video + text_shape("After"),))
    )
    markdown = render_pptx(source, OPTIONS, LIMITS).markdown
    assert (
        markdown.index("Before")
        < markdown.index("alternate drawing")
        < markdown.index("Fallback")
        < markdown.index("Video poster")
        < markdown.index("audio or video")
        < markdown.index("After")
    )


def test_inherited_master_objects_and_background_images_are_not_silently_omitted() -> (
    None
):
    members = parts((text_shape("Slide"),))
    members["ppt/slides/s1.xml"] = members["ppt/slides/s1.xml"].replace(
        b"<p:cSld>", b'<p:cSld><p:bg><a:blip r:embed="background"/></p:bg>'
    )
    members["ppt/slides/_rels/s1.xml.rels"] = relations(
        (("layout", "slideLayout", "../slideLayouts/layout.xml"),)
    )
    members["ppt/slideLayouts/layout.xml"] = (
        f"<p:sldLayout {NS}><p:cSld><p:spTree>{text_shape('Layout text')}</p:spTree></p:cSld></p:sldLayout>".encode()
    )
    members["ppt/slideLayouts/_rels/layout.xml.rels"] = relations(
        (("master", "slideMaster", "../slideMasters/master.xml"),)
    )
    members["ppt/slideMasters/master.xml"] = (
        f"<p:sldMaster {NS}><p:cSld><p:spTree>{picture()}</p:spTree></p:cSld></p:sldMaster>".encode()
    )
    markdown = render_pptx(archive(members), OPTIONS, LIMITS).markdown
    assert markdown.count("inherited layout or master") == 2
    assert "background image" in markdown


@pytest.mark.parametrize(
    "mutation",
    ["missing-blip", "linked-blip", "missing-relationship", "wrong-relationship"],
)
def test_picture_graph_is_safe_or_explicit(mutation: str) -> None:
    shape = picture()
    members = parts((shape,), image=b"unused", image_type="image/x-emf")
    if mutation == "missing-blip":
        members["ppt/slides/s1.xml"] = members["ppt/slides/s1.xml"].replace(
            b'<a:blip r:embed="image"/>', b""
        )
        assert (
            "unsupported image"
            in render_pptx(archive(members), OPTIONS, LIMITS).markdown
        )
        return
    if mutation == "linked-blip":
        members["ppt/slides/s1.xml"] = members["ppt/slides/s1.xml"].replace(
            b'r:embed="image"', b'r:link="image"'
        )
    elif mutation == "missing-relationship":
        del members["ppt/slides/_rels/s1.xml.rels"]
    else:
        members["ppt/slides/_rels/s1.xml.rels"] = relations(
            (("image", "slide", "../media/image.png"),)
        )
    with pytest.raises(ReverseConversionError):
        read_pptx(archive(members), OPTIONS, LIMITS)


def test_image_count_source_bytes_and_markdown_bytes_are_bounded() -> None:
    source = archive(parts((picture() + picture(),), image=b"large"))
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(source, OPTIONS, limits(max_image_source_bytes=1))
    assert raised.value.category is ReverseErrorCategory.RESOURCE_LIMIT
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(source, OPTIONS, limits(max_asset_count=1))
    assert raised.value.category is ReverseErrorCategory.RESOURCE_LIMIT
    text = archive(parts((text_shape("Rendered text is bounded."),)))
    with pytest.raises(ReverseConversionError) as raised:
        render_pptx(
            text,
            OPTIONS,
            limits(
                max_markdown_bytes=1,
                max_pptx_xml_elements=1000,
                max_pptx_xml_attributes=1000,
            ),
        )
    assert raised.value.category is ReverseErrorCategory.RESOURCE_LIMIT


def test_source_fences_cannot_swallow_later_slides_or_notes() -> None:
    source = archive(
        parts((text_shape("~~~markdown"), text_shape("Last")), notes=text_shape("~~~"))
    )
    markdown = render_pptx(source, OPTIONS, LIMITS).markdown
    assert "\\~\\~\\~markdown" in markdown
    assert markdown.count("\n---\n") == 1
    assert "\n~~~" not in markdown
    assert markdown.endswith("Last\n")


def test_empty_vector_shapes_and_picture_fills_have_explicit_positions() -> None:
    shape = text_shape("").replace(
        "<p:txBody>",
        '<p:spPr><a:blipFill><a:blip r:embed="image"/></a:blipFill></p:spPr><p:txBody>',
    )
    source = archive(
        parts(
            (text_shape("Before") + shape + text_shape("After"),),
            image=b"metafile",
            image_type="image/x-emf",
        )
    )
    result = render_pptx(source, OPTIONS, LIMITS)
    assert (
        result.markdown.index("Before")
        < result.markdown.index("unsupported drawing")
        < result.markdown.index("unavailable")
        < result.markdown.index("After")
    )
    assert result.normalized.unavailable_asset_count == 1


@pytest.mark.parametrize("identity", ["", ' r:id=""'])
def test_action_only_links_retain_label_without_executing_action(identity: str) -> None:
    shape = text_shape(
        "Next",
        run_properties=f'<a:rPr><a:hlinkClick{identity} action="ppaction://hlinkshowjump?jump=nextslide"/></a:rPr>',
    )
    markdown = render_pptx(archive(parts((shape,))), OPTIONS, LIMITS).markdown
    assert "Next (internal presentation link)" in markdown
    assert "ppaction" not in markdown


def test_ragged_table_padding_is_bounded_before_rendering() -> None:
    wide = "<a:tr>" + "<a:tc/>" * 30 + "</a:tr>"
    empty_rows = "<a:tr/>" * 100
    source = archive(
        parts(
            (
                "<p:graphicFrame><a:tbl>"
                + wide
                + empty_rows
                + "</a:tbl></p:graphicFrame>",
            )
        )
    )
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(source, OPTIONS, limits(max_markdown_bytes=1000))
    assert raised.value.category is ReverseErrorCategory.RESOURCE_LIMIT


def test_encrypted_zip_metadata_is_rejected_before_member_read() -> None:
    source = bytearray(archive(parts()))
    offset = source.index(b"PK\x01\x02")
    source[offset + 8] |= 1
    with pytest.raises(ReverseConversionError) as raised:
        read_pptx(bytes(source), OPTIONS, LIMITS)
    assert raised.value.category is ReverseErrorCategory.ENCRYPTED


def test_symlink_members_are_not_accepted_as_package_parts() -> None:
    output = io.BytesIO()
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(output, "w") as package:
        package.writestr(info, "/private")
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(output.getvalue(), OPTIONS, LIMITS)


@pytest.mark.parametrize("source", [b"not a ZIP", b"PK\x03\x04truncated"])
def test_invalid_archive_is_a_safe_document_error(source: bytes) -> None:
    with pytest.raises(ReverseConversionError, match="malformed"):
        read_pptx(source, OPTIONS, LIMITS)
