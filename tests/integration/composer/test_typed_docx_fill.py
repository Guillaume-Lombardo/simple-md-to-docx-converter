"""Real redistributable Word corpus qualification for typed content controls."""

from __future__ import annotations

import copy
import io
import zipfile
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import cast
from xml.etree import ElementTree as ET

import pytest
from defusedxml.ElementTree import fromstring
from pypdf import PdfReader

from markweave.composer.docx_fill import (
    TYPED_DOCX_FILL_ENGINE_VERSION,
    fill_docx,
    validate_template,
)
from markweave.composer.typed_templates import (
    TypedDocxLimits,
    TypedTemplateError,
    validate_values,
)
from tests.integration.document_engines.test_libreoffice_pdf import (
    _converter,
    _trace,
    _workspace,
)

_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = _ROOT / "spikes/anydoc/corpus/docx/text.docx"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAIN = "word/document.xml"


def _limits(**changes: object) -> TypedDocxLimits:
    values = {
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


def _schema() -> dict[str, object]:
    return {
        "fields": [
            {
                "name": "author.name",
                "type": "text",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 80},
            },
            {
                "name": "decision.date",
                "type": "date",
                "required": True,
                "constraints": {},
            },
            {
                "name": "decision.approved",
                "type": "boolean",
                "required": True,
                "constraints": {},
            },
            {
                "name": "finding.count",
                "type": "integer",
                "required": True,
                "constraints": {"minimum": 0, "maximum": 3},
            },
        ],
        "repeats": [
            {
                "name": "findings",
                "min_items": 0,
                "max_items": 3,
                "fields": [
                    {
                        "name": "title",
                        "type": "text",
                        "required": True,
                        "constraints": {"min_length": 1, "max_length": 80},
                    },
                    {
                        "name": "score",
                        "type": "integer",
                        "required": True,
                        "constraints": {"minimum": 0, "maximum": 100},
                    },
                ],
            }
        ],
    }


def _values() -> dict[str, object]:
    return {
        "author.name": "Ada Lovelace",
        "decision.date": "2026-09-24",
        "decision.approved": True,
        "finding.count": 2,
        "findings": [
            {"title": "First finding", "score": 73},
            {"title": "Second finding", "score": 91},
        ],
    }


def _sdt(tag: str, placeholder: str, *, block: bool) -> ET.Element:
    control = ET.Element(_W + "sdt")
    properties = ET.SubElement(control, _W + "sdtPr")
    ET.SubElement(properties, _W + "tag", {_W + "val": tag})
    content = ET.SubElement(control, _W + "sdtContent")
    parent = ET.SubElement(content, _W + "p") if block else content
    run = ET.SubElement(parent, _W + "r")
    ET.SubElement(run, _W + "t").text = placeholder
    return control


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _repack(source: bytes, replacement: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in original.infolist():
            target.writestr(info, replacement.get(info.filename, original.read(info)))
        for name, payload in replacement.items():
            if name not in original.namelist():
                target.writestr(name, payload)
    return output.getvalue()


def _safe_source() -> bytes:
    """Derive a local-only qualified subset from the MIT Word corpus."""

    original = _SOURCE.read_bytes()
    parts = _parts(original)
    document = fromstring(parts[_MAIN])
    relationships = fromstring(parts["word/_rels/document.xml.rels"])
    external_ids = {
        node.get("Id") for node in relationships if node.get("TargetMode") == "External"
    }
    for node in tuple(relationships):
        if node.get("Id") in external_ids:
            relationships.remove(node)
    for parent in document.iter():
        for child in tuple(parent):
            if (
                child.tag == _W + "hyperlink"
                and child.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                in external_ids
            ):
                parent.remove(child)
        for attribute in tuple(parent.attrib):
            if attribute.startswith(
                "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
            ):
                del parent.attrib[attribute]
        for child in tuple(parent):
            if (
                child.tag
                == "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
            ):
                fallback = child.find(
                    "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
                )
                assert fallback is not None
                index = list(parent).index(child)
                parent.remove(child)
                for replacement in tuple(fallback):
                    parent.insert(index, replacement)
                    index += 1
    ET.register_namespace(
        "", "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    return _repack(
        original,
        {
            _MAIN: ET.tostring(document, encoding="utf-8", xml_declaration=True),
            "word/_rels/document.xml.rels": ET.tostring(
                relationships, encoding="utf-8", xml_declaration=True
            ),
        },
    )


def _template() -> bytes:
    source = _safe_source()
    root = fromstring(_parts(source)[_MAIN])
    body = root.find(_W + "body")
    assert body is not None
    index = next(
        (i for i, child in enumerate(body) if child.tag == _W + "sectPr"), len(body)
    )
    for name in ("author.name", "decision.date", "decision.approved", "finding.count"):
        body.insert(index, _sdt(name, "PLACEHOLDER", block=True))
        index += 1
    repeat = ET.Element(_W + "sdt")
    properties = ET.SubElement(repeat, _W + "sdtPr")
    ET.SubElement(properties, _W + "tag", {_W + "val": "repeat:findings"})
    content = ET.SubElement(repeat, _W + "sdtContent")
    paragraph = ET.SubElement(content, _W + "p")
    paragraph.append(_sdt("findings.title", "TITLE", block=False))
    paragraph.append(_sdt("findings.score", "SCORE", block=False))
    body.insert(index, repeat)
    return _repack(
        source, {_MAIN: ET.tostring(root, encoding="utf-8", xml_declaration=True)}
    )


@pytest.mark.integration
@pytest.mark.light_coverage
def test_real_docx_repeat_and_non_target_preservation() -> None:
    assert TYPED_DOCX_FILL_ENGINE_VERSION == 1
    source = _safe_source()
    template_bytes = _template()
    validated = validate_template(_schema(), template_bytes, _limits())
    values = validate_values(validated, _values())
    assert not values.missing_paths and not values.ambiguous_paths
    result = fill_docx(validated, values, _limits())
    assert result == fill_docx(validated, values, _limits())

    original_parts, template_parts, result_parts = map(
        _parts, (source, template_bytes, result)
    )
    assert original_parts.keys() == template_parts.keys() == result_parts.keys()
    assert [
        name for name in result_parts if result_parts[name] != template_parts[name]
    ] == [_MAIN]
    assert all(
        original_parts[name] == result_parts[name]
        for name in original_parts
        if name != _MAIN
    )
    original_body = fromstring(original_parts[_MAIN]).find(_W + "body")
    result_body = fromstring(result_parts[_MAIN]).find(_W + "body")
    assert original_body is not None and result_body is not None
    assert [ET.tostring(node) for node in original_body] == [
        ET.tostring(node) for node in result_body if node.tag != _W + "sdt"
    ]

    visible = " ".join(node.text or "" for node in result_body.iter(_W + "t"))
    assert all(
        item in visible
        for item in (
            "Ada Lovelace",
            "2026-09-24",
            "true",
            "First finding",
            "73",
            "Second finding",
            "91",
        )
    )
    repeat = next(
        node
        for node in result_body.iter(_W + "sdt")
        if node.find(f"{_W}sdtPr/{_W}tag").get(_W + "val") == "repeat:findings"
    )
    assert len(repeat.find(_W + "sdtContent")) == 2


@pytest.mark.integration
@pytest.mark.requires_libreoffice
def test_qualified_typed_docx_opens_and_renders_in_libreoffice(
    tmp_path: Path,
) -> None:
    template_bytes = _template()
    validated = validate_template(_schema(), template_bytes, _limits())
    filled = fill_docx(validated, validate_values(validated, _values()), _limits())
    rendered = _converter(_workspace(tmp_path)).convert(filled, _trace(template_bytes))
    text = "\n".join(
        page.extract_text() for page in PdfReader(BytesIO(rendered.pdf)).pages
    )
    for expected in ("Ada Lovelace", "2026-09-24", "First finding", "Second finding"):
        assert expected in text
    assert "PLACEHOLDER" not in text


@pytest.mark.integration
@pytest.mark.light_coverage
@pytest.mark.parametrize(
    "mutation,code",
    [
        ("duplicate", "unsupported_target"),
        ("drawing", "unsupported_target"),
        ("external", "external_relationship"),
        ("signature", "active_content"),
        ("traversal", "invalid_package"),
    ],
)
def test_malformed_real_docx_fails_closed(mutation: str, code: str) -> None:
    template = _template()
    parts = _parts(template)
    if mutation in {"duplicate", "drawing"}:
        root = fromstring(parts[_MAIN])
        body = root.find(_W + "body")
        assert body is not None
        first = next(node for node in body if node.tag == _W + "sdt")
        if mutation == "duplicate":
            body.append(copy.deepcopy(first))
        else:
            ET.SubElement(first.find(_W + "sdtContent")[0], _W + "drawing")
        parts[_MAIN] = ET.tostring(root, encoding="utf-8")
    elif mutation == "external":
        relationships = fromstring(parts["word/_rels/document.xml.rels"])
        ET.SubElement(
            relationships,
            "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
            {
                "Id": "unsafe",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                "Target": "https://example.org",
                "TargetMode": "External",
            },
        )
        parts["word/_rels/document.xml.rels"] = ET.tostring(relationships)
    elif mutation == "signature":
        parts["_xmlsignatures/sig1.xml"] = b"<Signature/>"
    else:
        parts["../outside.xml"] = b"<x/>"
    with pytest.raises(TypedTemplateError) as rejected:
        validate_template(_schema(), _repack(template, parts), _limits())
    assert rejected.value.code == code


@pytest.mark.integration
@pytest.mark.light_coverage
def test_invalid_values_never_produce_docx() -> None:
    template = validate_template(_schema(), _template(), _limits())
    raw = _values()
    raw["findings"] = [{"title": "Bad", "score": 101}]
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(template, raw)
    assert rejected.value.code == "invalid_value"
    assert rejected.value.paths == ("findings[0].score",)


@pytest.mark.integration
@pytest.mark.light_coverage
def test_repeat_limit_rejects_large_filled_document() -> None:
    template = validate_template(_schema(), _template(), _limits())
    raw = _values()
    raw["findings"] = [{"title": "X", "score": 1}] * 4
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(template, raw)
    assert rejected.value.code == "invalid_value"


@pytest.mark.integration
@pytest.mark.light_coverage
@pytest.mark.parametrize(
    "case,code",
    [
        ("unknown_tag", "unsupported_target"),
        ("missing_tag", "unsupported_target"),
        ("second_tag", "unsupported_target"),
        ("tag_attributes", "unsupported_target"),
        ("empty_tag_value", "unsupported_target"),
        ("second_text_control", "unsupported_target"),
        ("extra_property", "unsupported_target"),
        ("word_date_control", "unsupported_target"),
        ("text_attributes", "unsupported_target"),
        ("extra_control_child", "unsupported_target"),
        ("multi_run", "unsupported_target"),
        ("run_attributes", "unsupported_target"),
        ("run_embedded_object", "unsupported_target"),
        ("run_second_text", "unsupported_target"),
        ("scalar_table", "unsupported_target"),
        ("scalar_in_table", "unsupported_target"),
        ("repeat_extra", "unsupported_target"),
        ("repeat_nested_id", "unsupported_target"),
        ("repeat_missing", "missing_target"),
        ("repeat_missing_field", "unsupported_target"),
        ("repeat_second_paragraph", "unsupported_target"),
        ("missing_body", "invalid_package"),
        ("nested_tag_wrong", "unsupported_target"),
        ("repeat_not_body", "unsupported_target"),
        ("repeat_paragraph_id", "unsupported_target"),
    ],
)
def test_unsupported_control_shapes_are_rejected(  # noqa: PLR0912, PLR0915 - mutation matrix
    case: str, code: str
) -> None:
    template = _template()
    root = fromstring(_parts(template)[_MAIN])
    body = root.find(_W + "body")
    assert body is not None
    controls = [node for node in body if node.tag == _W + "sdt"]
    first, repeat = controls[0], controls[-1]
    first_properties = first.find(_W + "sdtPr")
    first_content = first.find(_W + "sdtContent")
    repeat_content = repeat.find(_W + "sdtContent")
    assert first_properties is not None and first_content is not None
    assert repeat_content is not None
    if case == "unknown_tag":
        first_properties.find(_W + "tag").set(_W + "val", "unknown")
    elif case == "missing_tag":
        first_properties.remove(first_properties.find(_W + "tag"))
    elif case == "second_tag":
        ET.SubElement(first_properties, _W + "tag", {_W + "val": "author.name"})
    elif case == "tag_attributes":
        first_properties.find(_W + "tag").set("unexpected", "1")
    elif case == "empty_tag_value":
        first_properties.find(_W + "tag").set(_W + "val", "")
    elif case == "second_text_control":
        ET.SubElement(first_properties, _W + "text")
        ET.SubElement(first_properties, _W + "text")
    elif case == "extra_property":
        ET.SubElement(first_properties, _W + "dataBinding")
    elif case == "word_date_control":
        ET.SubElement(first_properties, _W + "date", {_W + "fullDate": "2020-01-01"})
    elif case == "text_attributes":
        ET.SubElement(first_properties, _W + "text", {_W + "multiLine": "1"})
    elif case == "extra_control_child":
        ET.SubElement(first, _W + "r")
    elif case == "multi_run":
        first_content[0].append(ET.Element(_W + "r"))
    elif case == "run_attributes":
        first_content[0].find(_W + "r").set("unexpected", "1")
    elif case == "run_embedded_object":
        ET.SubElement(first_content[0].find(_W + "r"), _W + "object")
    elif case == "run_second_text":
        ET.SubElement(first_content[0].find(_W + "r"), _W + "t").text = "extra"
    elif case == "scalar_table":
        first_content[0].tag = _W + "tbl"
    elif case == "scalar_in_table":
        body.remove(first)
        table = ET.SubElement(body, _W + "tbl")
        ET.SubElement(ET.SubElement(table, _W + "tr"), _W + "tc").append(first)
    elif case == "repeat_extra":
        ET.SubElement(repeat_content[0], _W + "drawing")
    elif case == "repeat_nested_id":
        nested = repeat_content[0].find(_W + "sdt")
        ET.SubElement(nested.find(_W + "sdtPr"), _W + "id", {_W + "val": "1"})
    elif case == "repeat_missing":
        body.remove(repeat)
    elif case == "repeat_missing_field":
        repeat_content[0].remove(repeat_content[0].findall(_W + "sdt")[-1])
    elif case == "repeat_second_paragraph":
        ET.SubElement(repeat_content, _W + "p")
    elif case == "missing_body":
        root.remove(body)
    elif case == "nested_tag_wrong":
        nested = repeat_content[0].find(_W + "sdt")
        nested.find(f"{_W}sdtPr/{_W}tag").set(_W + "val", "wrong.title")
    elif case == "repeat_not_body":
        body.remove(repeat)
        ET.SubElement(body, _W + "p").append(repeat)
    else:
        repeat_content[0].set(
            "{http://schemas.microsoft.com/office/word/2010/wordml}paraId", "1"
        )
    mutated = _repack(template, {_MAIN: ET.tostring(root, encoding="utf-8")})
    with pytest.raises(TypedTemplateError) as rejected:
        validate_template(_schema(), mutated, _limits())
    assert rejected.value.code == code


@pytest.mark.integration
@pytest.mark.light_coverage
@pytest.mark.parametrize(
    "case,code",
    [
        ("empty", "invalid_package"),
        ("invalid_zip", "invalid_package"),
        ("too_large", "limit_exceeded"),
        ("missing_document", "invalid_package"),
        ("wrong_root", "invalid_package"),
        ("wrong_content_types_root", "invalid_package"),
        ("markup_compatibility", "unsupported_target"),
        ("other_part_sdt", "unsupported_target"),
        ("macro_relationship", "active_content"),
    ],
)
def test_package_admission_rejects_unsafe_inputs(case: str, code: str) -> None:
    template = _template()
    parts = _parts(template)
    limits = _limits()
    if case == "empty":
        template = b""
    elif case == "invalid_zip":
        template = b"PK invalid"
    elif case == "too_large":
        limits = _limits(max_archive_bytes=len(template) - 1)
    elif case == "missing_document":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, payload in parts.items():
                if name != _MAIN:
                    archive.writestr(name, payload)
        template = output.getvalue()
    elif case == "wrong_root":
        parts[_MAIN] = b"<wrong/>"
        template = _repack(template, parts)
    elif case == "wrong_content_types_root":
        template = _repack(template, {"[Content_Types].xml": b"<wrong/>"})
    elif case == "markup_compatibility":
        document = fromstring(parts[_MAIN])
        document.set(
            "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable",
            "w14",
        )
        template = _repack(template, {_MAIN: ET.tostring(document)})
    elif case == "other_part_sdt":
        parts["docProps/core.xml"] = (
            b'<x xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:sdt/></x>'
        )
        template = _repack(template, parts)
    else:
        relationships = fromstring(parts["word/_rels/document.xml.rels"])
        ET.SubElement(
            relationships,
            "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship",
            {
                "Id": "macro",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/vbaProject",
                "Target": "styles.xml",
            },
        )
        template = _repack(
            template, {"word/_rels/document.xml.rels": ET.tostring(relationships)}
        )
    with pytest.raises(TypedTemplateError) as rejected:
        validate_template(_schema(), template, limits)
    assert rejected.value.code == code


@pytest.mark.integration
@pytest.mark.light_coverage
def test_package_admission_preserves_explicit_safe_zip_directory() -> None:
    source = _template()
    with_directory = _repack(source, {"word/media/": b""})
    validated = validate_template(_schema(), with_directory, _limits())
    filled = fill_docx(validated, validate_values(validated, _values()), _limits())
    with zipfile.ZipFile(io.BytesIO(filled)) as archive:
        assert archive.getinfo("word/media/").is_dir()
    assert b"Ada Lovelace" in _parts(filled)[_MAIN]


@pytest.mark.integration
@pytest.mark.light_coverage
def test_package_admission_rejects_non_bytes_even_when_zip_compatible() -> None:
    with pytest.raises(TypedTemplateError) as rejected:
        validate_template(_schema(), cast("bytes", bytearray(_template())), _limits())
    assert rejected.value.code == "invalid_package"


@pytest.mark.integration
@pytest.mark.light_coverage
def test_fill_rechecks_values_and_template_identity() -> None:
    template = validate_template(_schema(), _template(), _limits())
    incomplete = validate_values(template, {"author.name": "Ada"})
    with pytest.raises(TypedTemplateError, match="unresolved values"):
        fill_docx(template, incomplete, _limits())
    approved = validate_values(template, _values())
    approved.normalized["finding.count"] = 99
    with pytest.raises(TypedTemplateError) as rejected:
        fill_docx(template, approved, _limits())
    assert rejected.value.code == "invalid_value"
    with pytest.raises(TypedTemplateError) as rejected:
        fill_docx(
            template,
            validate_values(template, _values()),
            _limits(max_text_characters=99),
        )
    assert rejected.value.code == "template_changed"
    with pytest.raises(TypedTemplateError) as rejected:
        fill_docx(
            replace(template, sha256="0" * 64),
            validate_values(template, _values()),
            _limits(),
        )
    assert rejected.value.code == "template_changed"
    omitted_after_review = validate_values(template, _values())
    del omitted_after_review.normalized["finding.count"]
    with pytest.raises(TypedTemplateError) as rejected:
        fill_docx(template, omitted_after_review, _limits())
    assert rejected.value.code == "unresolved_values"
    assert rejected.value.paths == ("finding.count",)


@pytest.mark.integration
@pytest.mark.light_coverage
def test_real_docx_inline_scalar_and_literal_repeat_run_preserve_structure() -> None:
    source = _template()
    root = fromstring(_parts(source)[_MAIN])
    body = root.find(_W + "body")
    assert body is not None
    first = next(child for child in body if child.tag == _W + "sdt")
    index = list(body).index(first)
    body.remove(first)
    inline_paragraph = ET.Element(_W + "p")
    inline_paragraph.append(first)
    body.insert(index, inline_paragraph)
    repeat = next(
        child
        for child in body
        if child.tag == _W + "sdt"
        and child.find(f"{_W}sdtPr/{_W}tag").get(_W + "val") == "repeat:findings"
    )
    prototype = repeat.find(f"{_W}sdtContent/{_W}p")
    assert prototype is not None
    prototype.insert(0, ET.Element(_W + "pPr"))
    literal = ET.SubElement(prototype, _W + "r")
    ET.SubElement(literal, _W + "t").text = " reviewed literal"
    modified = _repack(source, {_MAIN: ET.tostring(root)})
    template = validate_template(_schema(), modified, _limits())
    filled = fill_docx(template, validate_values(template, _values()), _limits())
    document = fromstring(_parts(filled)[_MAIN])
    assert len(document.findall(f".//{_W}t[.=' reviewed literal']")) == 2
    assert b"Ada Lovelace" in _parts(filled)[_MAIN]
    assert document.find(f".//{_W}p/{_W}sdt") is not None


@pytest.mark.integration
@pytest.mark.light_coverage
def test_optional_field_renders_empty_and_preserves_spaces() -> None:
    schema = _schema()
    fields = schema["fields"]
    assert isinstance(fields, list)
    fields.append(
        {"name": "note", "type": "text", "required": False, "constraints": {}}
    )
    template_bytes = _template()
    root = fromstring(_parts(template_bytes)[_MAIN])
    body = root.find(_W + "body")
    assert body is not None
    body.insert(-1, _sdt("note", "OLD PLACEHOLDER", block=True))
    template_bytes = _repack(template_bytes, {_MAIN: ET.tostring(root)})
    template = validate_template(schema, template_bytes, _limits())
    filled = fill_docx(template, validate_values(template, _values()), _limits())
    assert b"OLD PLACEHOLDER" not in _parts(filled)[_MAIN]
    supplied = _values()
    supplied["note"] = " padded "
    filled = fill_docx(template, validate_values(template, supplied), _limits())
    assert b'xml:space="preserve"> padded </' in _parts(filled)[_MAIN]


@pytest.mark.integration
@pytest.mark.light_coverage
def test_fill_rejects_document_growth_beyond_configured_member_limit() -> None:
    template_bytes = _template()
    source_xml_bytes = len(_parts(template_bytes)[_MAIN])
    limits = _limits(max_member_bytes=source_xml_bytes + 1)
    template = validate_template(_schema(), template_bytes, limits)
    values = _values()
    values["author.name"] = "A" * 80
    values["finding.count"] = 3
    values["findings"] = [{"title": "B" * 80, "score": 100}] * 3
    with pytest.raises(TypedTemplateError) as rejected:
        fill_docx(template, validate_values(template, values), limits)
    assert rejected.value.code == "limit_exceeded"
