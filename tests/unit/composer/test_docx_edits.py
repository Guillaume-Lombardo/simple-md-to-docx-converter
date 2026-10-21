"""Bounded DOCX text-node edits on synthetic in-memory Word packages."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable

import pytest
from pytest_mock import MockerFixture

from markweave.composer import docx_edits
from markweave.composer.docx_edits import (
    DOCX_EDIT_ENGINE_VERSION,
    DocxEditError,
    ReplaceText,
    apply_docx_text_edits,
    inspect_docx_text,
    operations_record,
)
from markweave.composer.typed_templates import TypedDocxLimits

pytestmark = [pytest.mark.unit]

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MAIN = "word/document.xml"
_DOCUMENT_RELS = "word/_rels/document.xml.rels"
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats'
    '.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
_HYPERLINK = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def _limits(**changes: object) -> TypedDocxLimits:
    values: dict[str, object] = {
        "max_archive_bytes": 100_000,
        "max_entries": 50,
        "max_member_bytes": 100_000,
        "max_total_bytes": 300_000,
        "max_compression_ratio": 1_000,
        "max_xml_elements": 10_000,
        "max_xml_depth": 100,
        "max_xml_attributes": 20_000,
        "max_schema_bytes": 10_000,
        "max_values_bytes": 10_000,
        "max_repeat_items": 4,
        "max_text_characters": 100,
    }
    values.update(changes)
    return TypedDocxLimits(**values)


def _document(body: str, declaration: str = "") -> bytes:
    return (
        f"{declaration}"
        f'<w:document xmlns:w="{_W}" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships" xmlns:w14="http://schemas.microsoft.com/office/word/2010/'
        'wordml" mc:Ignorable="w14">'
        f"<w:body>{body}</w:body></w:document>"
    ).encode()


def _relationships(*entries: tuple[str, str, str, str]) -> bytes:
    nodes = "".join(
        f'<Relationship Id="{identifier}" Type="{kind}" Target="{target}"'
        + (f' TargetMode="{mode}"' if mode else "")
        + "/>"
        for identifier, kind, target, mode in entries
    )
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'relationships">{nodes}</Relationships>'
    ).encode()


def _package(
    body: str = "",
    *,
    document: bytes | None = None,
    extra: dict[str, bytes] | None = None,
    omit: str | None = None,
) -> bytes:
    parts: dict[str, bytes] = {
        "[Content_Types].xml": _CONTENT_TYPES.encode(),
        "_rels/.rels": _ROOT_RELS.encode(),
        _MAIN: document if document is not None else _document(body),
        "word/styles.xml": f'<w:styles xmlns:w="{_W}"/>'.encode(),
    }
    parts.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            if name != omit:
                archive.writestr(name, payload)
    return output.getvalue()


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


_SAMPLE = (
    "<w:p><w:pPr><w:pStyle w:val='Title'/></w:pPr>"
    "<w:r><w:rPr><w:b/></w:rPr><w:t>Bold title</w:t></w:r></w:p>"
    '<w:p><w:r><w:t xml:space="preserve">Plain </w:t></w:r>'
    "<w:r><w:t>A &amp; B</w:t></w:r></w:p>"
    "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
)


def test_inspection_lists_stable_body_and_table_targets() -> None:
    targets = inspect_docx_text(_package(_SAMPLE), _limits())

    assert [
        (item.target, item.paragraph, item.in_table, item.text) for item in targets
    ] == [
        ("word/document.xml#t0", 0, False, "Bold title"),
        ("word/document.xml#t1", 1, False, "Plain "),
        ("word/document.xml#t2", 1, False, "A & B"),
        ("word/document.xml#t3", 2, True, "Cell"),
    ]


def test_replacement_splices_only_target_bytes_and_preserves_package() -> None:
    source = _package(_SAMPLE)
    edited = apply_docx_text_edits(
        source,
        (
            ReplaceText("word/document.xml#t0", "Bold title", "New <title> & more"),
            ReplaceText("word/document.xml#t3", "Cell", " padded "),
        ),
        _limits(),
    )

    before, after = _members(source), _members(edited)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {_MAIN}
    expected = (
        before[_MAIN]
        .replace(b"<w:t>Bold title</w:t>", b"<w:t>New &lt;title&gt; &amp; more</w:t>")
        .replace(b"<w:t>Cell</w:t>", b'<w:t xml:space="preserve"> padded </w:t>')
    )
    assert after[_MAIN] == expected
    assert b'mc:Ignorable="w14"' in after[_MAIN]
    assert [item.text for item in inspect_docx_text(edited, _limits())] == [
        "New <title> & more",
        "Plain ",
        "A & B",
        " padded ",
    ]


def test_existing_preserve_attribute_and_empty_text_node_are_kept() -> None:
    source = _package(
        '<w:p><w:r><w:t xml:space="preserve"> a </w:t></w:r>'
        "<w:r><w:t></w:t></w:r></w:p>"
    )
    edited = apply_docx_text_edits(
        source,
        (
            ReplaceText("word/document.xml#t0", " a ", "b"),
            ReplaceText("word/document.xml#t1", "", " c"),
        ),
        _limits(),
    )

    assert _members(edited)[_MAIN].endswith(
        b'<w:t xml:space="preserve">b</w:t></w:r>'
        b'<w:r><w:t xml:space="preserve"> c</w:t></w:r></w:p></w:body></w:document>'
    )


@pytest.mark.parametrize(
    "body",
    [
        "<w:p><w:ins><w:r><w:t>tracked</w:t></w:r></w:ins></w:p>",
        "<w:p><w:del><w:r><w:t>deleted</w:t></w:r></w:del></w:p>",
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>bound</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>",
        "<w:p><w:fldSimple w:instr='PAGE'><w:r><w:t>1</w:t></w:r></w:fldSimple></w:p>",
        "<w:p><w:r><w:fldChar w:fldCharType='begin'/></w:r>"
        "<w:r><w:instrText>DATE</w:instrText></w:r>"
        "<w:r><w:fldChar w:fldCharType='separate'/></w:r>"
        "<w:r><w:t>today</w:t></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r></w:p>",
        "<w:p><mc:AlternateContent><mc:Choice Requires='w14'><w:r><w:t>new</w:t>"
        "</w:r></mc:Choice></mc:AlternateContent></w:p>",
        "<w:p><w:r><w:t/></w:r></w:p>",
        "<w:p><w:r><w:t xml:space='default'>space</w:t></w:r></w:p>",
        "<w:p><w:r><w:t w:custom='1'>attribute</w:t></w:r></w:p>",
        "<w:p><w:r><w:t>tab&#9;inside</w:t></w:r></w:p>",
        "<w:p><w:t>no run</w:t></w:p>",
        "<w:p><w:r><w:t>before<w:b/>after</w:t></w:r></w:p>",
        "<w:p><w:r><w:t>a<x:y xmlns:x='urn:example'/>b</w:t></w:r></w:p>",
        "<w:p><w:r><w:t>a<w:t>b</w:t>c</w:t></w:r></w:p>",
        "<w:p><w:r><w:t>a<!-- note -->b</w:t></w:r></w:p>",
        "<w:p><w:r><w:t>a<?marker value?>b</w:t></w:r></w:p>",
    ],
)
def test_text_owned_by_other_mechanisms_is_not_a_target(body: str) -> None:
    source = _package(body)

    assert inspect_docx_text(source, _limits()) == ()
    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            source, (ReplaceText("word/document.xml#t0", "", "x"),), _limits()
        )
    assert rejected.value.code == "unsupported_target"
    assert rejected.value.targets == ("word/document.xml#t0",)


def test_field_result_exclusion_ends_with_the_field() -> None:
    body = (
        "<w:p><w:r><w:fldChar w:fldCharType='begin'/></w:r>"
        "<w:r><w:t>result</w:t></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r>"
        "<w:r><w:t>after</w:t></w:r></w:p>"
    )

    assert [item.target for item in inspect_docx_text(_package(body), _limits())] == [
        "word/document.xml#t1"
    ]


@pytest.mark.parametrize(
    ("target", "code"),
    [
        ("word/document.xml#t9", "unsupported_target"),
        ("word/document.xml#t01", "unsupported_target"),
        ("word/document.xml#t-1", "unsupported_target"),
        ("word/document.xml#t", "unsupported_target"),
        ("word/document.xml#t\u0661", "unsupported_target"),
        ("word/styles.xml#t0", "unsupported_target"),
        ("word/document.xml#t" + "1" * 80, "unsupported_target"),
    ],
)
def test_unknown_or_malformed_targets_are_rejected(target: str, code: str) -> None:
    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            _package(_SAMPLE), (ReplaceText(target, "Cell", "x"),), _limits()
        )
    assert rejected.value.code == code
    assert len(rejected.value.targets[0]) <= 64


def test_non_string_target_is_rejected() -> None:
    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            _package(_SAMPLE),
            (ReplaceText(7, "Cell", "x"),),  # ty: ignore[invalid-argument-type]
            _limits(),
        )
    assert rejected.value.code == "unsupported_target"


@pytest.mark.parametrize(
    ("operations", "code"),
    [
        ((), "no_operations"),
        (
            (
                ReplaceText("word/document.xml#t0", "Bold title", "a"),
                ReplaceText("word/document.xml#t0", "Bold title", "b"),
            ),
            "duplicate_target",
        ),
        ((ReplaceText("word/document.xml#t0", "Stale", "a"),), "target_changed"),
        ((ReplaceText("word/document.xml#t0", "Bold title", "a\nb"),), "invalid_text"),
        (
            (ReplaceText("word/document.xml#t0", "Bold title", "\ufffe"),),
            "invalid_text",
        ),
        (
            (ReplaceText("word/document.xml#t0", "Bold title", "x" * 101),),
            "invalid_text",
        ),
        ((ReplaceText("word/document.xml#t0", "Bold\x00", "a"),), "invalid_text"),
        (
            (ReplaceText("word/document.xml#t0", "Bold title", 1),),  # ty: ignore[invalid-argument-type]
            "invalid_text",
        ),
    ],
)
def test_invalid_operations_never_produce_a_document(
    operations: tuple[ReplaceText, ...], code: str
) -> None:
    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(_package(_SAMPLE), operations, _limits())
    assert rejected.value.code == code


def test_operation_record_is_canonical_and_versioned() -> None:
    record = operations_record((ReplaceText("word/document.xml#t0", "é", "ü"),))

    assert record == (
        '{"engine_version":1,"operations":[{"expected_text":"é","op":"replace_text",'
        '"target":"word/document.xml#t0","text":"ü"}]}'
    )
    assert json.loads(record)["engine_version"] == DOCX_EDIT_ENGINE_VERSION


def test_external_hyperlinks_are_preserved_but_never_loaded() -> None:
    source = _package(
        "<w:p><w:hyperlink r:id='rIdLink'><w:r><w:t>link</w:t></w:r></w:hyperlink></w:p>",
        extra={
            _DOCUMENT_RELS: _relationships(
                ("rIdLink", _HYPERLINK, "https://example.invalid/", "External"),
                ("rIdStyles", _IMAGE.replace("image", "styles"), "styles.xml", ""),
            )
        },
    )

    edited = apply_docx_text_edits(
        source, (ReplaceText("word/document.xml#t0", "link", "label"),), _limits()
    )

    assert _members(edited)[_DOCUMENT_RELS] == _members(source)[_DOCUMENT_RELS]


@pytest.mark.parametrize(
    ("relationships", "code"),
    [
        (
            _relationships(
                ("rIdImage", _IMAGE, "https://example.invalid/a.png", "External")
            ),
            "external_relationship",
        ),
        (
            _relationships(("", _HYPERLINK, "https://example.invalid/", "External")),
            "external_relationship",
        ),
        (
            _relationships(
                ("rIdLink", _HYPERLINK, "https://example.invalid/a", "External"),
                ("rIdLink", _HYPERLINK, "https://example.invalid/b", "External"),
            ),
            "invalid_package",
        ),
        (
            _relationships(
                ("rIdLink", _HYPERLINK, "https://example.invalid/a", "External"),
                ("rIdLink", _IMAGE.replace("image", "styles"), "styles.xml", ""),
            ),
            "invalid_package",
        ),
        (
            _relationships(
                ("rIdMacro", _IMAGE.replace("image", "vbaProject"), "styles.xml", "")
            ),
            "active_content",
        ),
        (b"<NotRelationships/>", "invalid_package"),
    ],
)
def test_unsafe_relationships_fail_closed(relationships: bytes, code: str) -> None:
    source = _package("<w:p/>", extra={_DOCUMENT_RELS: relationships})

    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text(source, _limits())
    assert rejected.value.code == code


def _invalid_zip() -> bytes:
    return b"PK invalid"


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (lambda: b"", "invalid_package"),
        (_invalid_zip, "invalid_package"),
        (lambda: _package(omit=_MAIN), "invalid_package"),
        (lambda: _package(document=b"<wrong/>"), "invalid_package"),
        (lambda: _package(document=b"<w:document"), "invalid_package"),
        (
            lambda: _package("", extra={"_xmlsignatures/sig1.xml": b"<x/>"}),
            "active_content",
        ),
        (
            lambda: _package("", extra={"word/vbaProject.bin": b"bin"}),
            "active_content",
        ),
        (
            lambda: _package(
                document=_document(
                    "<w:p/>", '<?xml version="1.0" encoding="ISO-8859-1"?>'
                )
            ),
            "unsupported_encoding",
        ),
        (
            lambda: _package(document=_document("<w:p/>").decode().encode("utf-16")),
            "unsupported_encoding",
        ),
    ],
)
def test_unsafe_or_malformed_packages_fail_closed(
    factory: Callable[[], bytes], code: str
) -> None:
    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text(factory(), _limits())
    assert rejected.value.code == code


def test_document_type_declarations_are_rejected(mocker: MockerFixture) -> None:
    mocker.patch.object(
        docx_edits.package_validation,
        "_parse_xml",
        side_effect=lambda payload, _limits: docx_edits.ElementTree.fromstring(payload),
    )
    document = b'<!DOCTYPE w:document [<!ENTITY e "x">]>' + _document("<w:p/>")

    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text(_package(document=document), _limits())
    assert rejected.value.code == "invalid_package"


def test_utf8_declaration_and_non_bytes_input() -> None:
    declared = _package(
        document=_document(
            "<w:p><w:r><w:t>x</w:t></w:r></w:p>",
            '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        )
    )
    assert len(inspect_docx_text(declared, _limits())) == 1
    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text("PK", _limits())  # ty: ignore[invalid-argument-type]
    assert rejected.value.code == "invalid_package"


@pytest.mark.parametrize(
    "limits",
    [
        {"max_archive_bytes": 200},
        {"max_member_bytes": 200},
    ],
)
def test_admission_limits_are_reported(limits: dict[str, int]) -> None:
    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text(_package(_SAMPLE), _limits(**limits))
    assert rejected.value.code == "limit_exceeded"


def test_growth_beyond_member_limit_is_rejected_before_publication() -> None:
    source = _package(_SAMPLE)
    size = len(_members(source)[_MAIN])

    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            source,
            (ReplaceText("word/document.xml#t0", "Bold title", "x" * 100),),
            _limits(max_member_bytes=size + 10),
        )
    assert rejected.value.code == "limit_exceeded"


def test_growth_beyond_archive_limit_is_rejected_before_publication(
    mocker: MockerFixture,
) -> None:
    source = _package(_SAMPLE)
    mocker.patch.object(docx_edits, "_write_package", return_value=b"x" * 100_001)

    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            source, (ReplaceText("word/document.xml#t0", "Bold title", "x"),), _limits()
        )
    assert rejected.value.code == "limit_exceeded"


@pytest.mark.parametrize("change", ["member", "extra", "count", "text"])
def test_post_write_verification_detects_unexpected_changes(
    mocker: MockerFixture, change: str
) -> None:
    source = _package(_SAMPLE)
    original = docx_edits._write_package

    def tampered(content: bytes, document_xml: bytes) -> bytes:
        output = original(content, document_xml)
        members = _members(output)
        if change == "member":
            members["word/styles.xml"] = (
                f'<w:styles xmlns:w="{_W}"><w:x/></w:styles>'.encode()
            )
        elif change == "extra":
            members["word/extra.xml"] = b"<x/>"
        elif change == "count":
            members[_MAIN] = members[_MAIN].replace(
                b"</w:body>", b"<w:p><w:r><w:t>new</w:t></w:r></w:p></w:body>"
            )
        else:
            members[_MAIN] = members[_MAIN].replace(b">Cell<", b">Other<")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        return buffer.getvalue()

    mocker.patch.object(docx_edits, "_write_package", side_effect=tampered)

    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            source, (ReplaceText("word/document.xml#t0", "Bold title", "x"),), _limits()
        )
    assert rejected.value.code == "package_changed"


def test_unterminated_start_tag_is_invalid() -> None:
    with pytest.raises(DocxEditError) as rejected:
        docx_edits._start_tag_end(b"<w:t a='>'", 0)
    assert rejected.value.code == "invalid_package"


def test_error_message_is_content_free() -> None:
    error = DocxEditError("target_changed", ("word/document.xml#t0",))

    assert str(error) == "DOCX edit target changed"


def test_expat_rejects_markup_the_tree_parser_did_not_see(
    mocker: MockerFixture,
) -> None:
    def parse(payload: bytes, _limits: object) -> docx_edits.ElementTree.Element:
        if payload == b"<w:document":
            payload = _document("<w:p/>")
        return docx_edits.ElementTree.fromstring(payload)

    mocker.patch.object(docx_edits.package_validation, "_parse_xml", side_effect=parse)

    with pytest.raises(DocxEditError) as rejected:
        inspect_docx_text(_package(document=b"<w:document"), _limits())
    assert rejected.value.code == "invalid_package"


def test_directory_entries_and_whitespace_are_tolerated() -> None:
    source = _package(
        "\n  <!-- outside -->\n  <w:p>\n    <w:r><w:t>kept</w:t></w:r>\n  </w:p>\n",
        extra={"word/media/": b""},
    )

    edited = apply_docx_text_edits(
        source, (ReplaceText("word/document.xml#t0", "kept", "changed"),), _limits()
    )

    with zipfile.ZipFile(io.BytesIO(edited)) as archive:
        assert archive.getinfo("word/media/").is_dir()
    assert [item.text for item in inspect_docx_text(edited, _limits())] == ["changed"]


def test_long_document_edits_every_target_in_one_bounded_pass() -> None:
    body = "".join(
        f"<w:p><w:r><w:t>Line {index}</w:t></w:r></w:p>" for index in range(2_000)
    )
    source = _package(body)
    limits = _limits(max_member_bytes=1_000_000, max_total_bytes=2_000_000)
    targets = inspect_docx_text(source, limits)

    edited = apply_docx_text_edits(
        source,
        tuple(
            ReplaceText(item.target, item.text, item.text.upper()) for item in targets
        ),
        limits,
    )

    assert [item.text for item in inspect_docx_text(edited, limits)] == [
        f"LINE {index}" for index in range(2_000)
    ]


def test_markup_inside_a_text_node_keeps_later_targets_stable() -> None:
    source = _package(
        "<w:p><w:r><w:t>before<w:b/>after</w:t></w:r>"
        "<w:r><w:t>kept</w:t></w:r></w:p><w:p><w:r><w:t>next</w:t></w:r></w:p>"
    )

    assert [
        (item.target, item.paragraph, item.text)
        for item in inspect_docx_text(source, _limits())
    ] == [("word/document.xml#t1", 0, "kept"), ("word/document.xml#t2", 1, "next")]
    edited = apply_docx_text_edits(
        source, (ReplaceText("word/document.xml#t1", "kept", "changed"),), _limits()
    )
    assert b"<w:t>before<w:b/>after</w:t>" in _members(edited)[_MAIN]
    with pytest.raises(DocxEditError) as rejected:
        apply_docx_text_edits(
            source,
            (ReplaceText("word/document.xml#t0", "beforeafter", "x"),),
            _limits(),
        )
    assert rejected.value.code == "unsupported_target"
