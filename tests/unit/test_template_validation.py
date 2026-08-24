"""Unit and security coverage for bounded Word template validation."""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any, cast
from xml.etree import ElementTree

import pytest

from md_converter.templates import validation as template_validation
from md_converter.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from md_converter.templates.validation import (
    PANDOC_REQUIRED_STYLES,
    FontPolicy,
    RequiredStyle,
    TemplateFontDeclaration,
    TemplateLimits,
    validate_template,
)

pytestmark = pytest.mark.unit
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
OFFICE_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
STYLE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"


@pytest.fixture
def limits() -> TemplateLimits:
    return TemplateLimits(
        2_000_000, 100, 500_000, 1_000_000, 200.0, 5_000, 50, 20_000, 20, 80
    )


@pytest.fixture
def font_policy() -> FontPolicy:
    return FontPolicy(
        (
            "Liberation Sans",
            "Liberation Serif",
            "Liberation Mono",
            "Carlito",
            "Caladea",
            "DejaVu Sans",
            "DejaVu Serif",
            "DejaVu Sans Mono",
        ),
        (
            ("Arial", "Liberation Sans"),
            ("Times New Roman", "Liberation Serif"),
            ("Courier New", "Liberation Mono"),
            ("Calibri", "Carlito"),
            ("Cambria", "Caladea"),
        ),
    )


@pytest.fixture
def declaration() -> TemplateFontDeclaration:
    return TemplateFontDeclaration(("Calibri", "Cambria", "Courier New"))


def _styles(*, omit: str | None = None, wrong_type: str | None = None) -> bytes:
    entries = []
    for required in PANDOC_REQUIRED_STYLES:
        if required.style_id == omit:
            continue
        style_type = required.style_type
        if required.style_id == wrong_type:
            style_type = (
                "character" if required.style_type == "paragraph" else "paragraph"
            )
        entries.append(
            f'<w:style w:type="{style_type}" w:styleId="{required.style_id}">'
            f'<w:name w:val="{required.name}"/><w:rPr><w:rFonts '
            'w:ascii="Calibri" w:hAnsi="Cambria"/></w:rPr></w:style>'
        )
    return (
        f'<?xml version="1.0"?><w:styles xmlns:w="{WORD_NS}">'
        + "".join(entries)
        + "</w:styles>"
    ).encode()


def _base_entries() -> dict[str, bytes]:
    return {
        "[Content_Types].xml": (
            f'<?xml version="1.0"?><Types xmlns="{TYPE_NS}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            "</Types>"
        ).encode(),
        "_rels/.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{OFFICE_REL}" Target="word/document.xml"/>'
            "</Relationships>"
        ).encode(),
        "word/document.xml": (
            f'<?xml version="1.0"?><w:document xmlns:w="{WORD_NS}"><w:body>'
            '<w:p><w:r><w:rPr><w:rFonts w:ascii="Courier New"/></w:rPr>'
            "<w:t>safe</w:t></w:r></w:p></w:body></w:document>"
        ).encode(),
        "word/styles.xml": _styles(),
        "word/_rels/document.xml.rels": (
            f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
            f'<Relationship Id="rId1" Type="{STYLE_REL}" Target="styles.xml"/>'
            "</Relationships>"
        ).encode(),
    }


def _docx(
    replacements: Mapping[str, bytes] | None = None,
    *,
    remove: frozenset[str] = frozenset(),
    duplicate: tuple[str, bytes] | None = None,
) -> bytes:
    entries = _base_entries()
    entries.update(replacements or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in entries.items():
            if name in remove:
                continue
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
        if duplicate is not None:
            archive.writestr(duplicate[0], duplicate[1])
    return output.getvalue()


def _assert_code(
    data: bytes,
    expected: TemplateValidationErrorCode,
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    with pytest.raises(TemplateValidationError) as captured:
        validate_template(data, declaration, limits, font_policy)
    assert captured.value.code is expected
    assert "safe" not in str(captured.value)


def test_valid_template_returns_immutable_font_and_package_observations(
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    validated = validate_template(_docx(), declaration, limits, font_policy)
    assert len(validated.sha256) == 64
    assert validated.parts == tuple(sorted(_base_entries()))
    assert validated.declared_fonts == ("Calibri", "Cambria", "Courier New")
    assert validated.referenced_fonts == ("Calibri", "Cambria", "Courier New")
    assert validated.resolved_fonts == (
        ("Calibri", "Carlito"),
        ("Cambria", "Caladea"),
        ("Courier New", "Liberation Mono"),
    )


@pytest.mark.parametrize(
    "required", PANDOC_REQUIRED_STYLES, ids=lambda item: item.style_id
)
def test_every_required_pandoc_style_and_type_is_enforced(
    required: RequiredStyle,
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    style_id = required.style_id
    for styles in (_styles(omit=style_id), _styles(wrong_type=style_id)):
        _assert_code(
            _docx({"word/styles.xml": styles}),
            TemplateValidationErrorCode.REQUIRED_STYLES,
            limits,
            declaration,
            font_policy,
        )


@pytest.mark.parametrize(
    "target",
    (
        "https://example.invalid/a",
        "file:///etc/passwd",
        r"\\host\share",
        "//169.254.169.254/a",
    ),
)
def test_every_external_relationship_is_rejected_before_resolution(
    target: str,
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    relationships = (
        f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
        f'<Relationship Id="rId1" Type="{STYLE_REL}" Target="{target}" TargetMode="External"/>'
        "</Relationships>"
    ).encode()
    _assert_code(
        _docx({"word/_rels/document.xml.rels": relationships}),
        TemplateValidationErrorCode.EXTERNAL_RELATIONSHIP,
        limits,
        declaration,
        font_policy,
    )


@pytest.mark.parametrize(
    "case",
    (
        (
            "_rels/.rels",
            b'<Relationships xmlns="urn:not-opc"/>',
            TemplateValidationErrorCode.INVALID_PACKAGE,
        ),
        (
            "word/document.xml",
            f'<w:body xmlns:w="{WORD_NS}"/>'.encode(),
            TemplateValidationErrorCode.INVALID_PACKAGE,
        ),
        (
            "word/styles.xml",
            _styles()
            .replace(b"<w:styles", b"<w:document", 1)
            .replace(b"</w:styles>", b"</w:document>"),
            TemplateValidationErrorCode.REQUIRED_STYLES,
        ),
    ),
)
def test_required_opc_and_word_roots_are_enforced(
    case: tuple[str, bytes, TemplateValidationErrorCode],
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    part_name, payload, expected = case
    _assert_code(
        _docx({part_name: payload}), expected, limits, declaration, font_policy
    )


def test_unknown_relationship_target_mode_is_rejected(
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    relationships = (
        f'<Relationships xmlns="{REL_NS}"><Relationship Id="rId1" '
        f'Type="{STYLE_REL}" Target="styles.xml" TargetMode="Unexpected"/>'
        "</Relationships>"
    ).encode()
    _assert_code(
        _docx({"word/_rels/document.xml.rels": relationships}),
        TemplateValidationErrorCode.INVALID_PACKAGE,
        limits,
        declaration,
        font_policy,
    )


@pytest.mark.parametrize("signal", ("content_type", "relationship", "part"))
def test_independent_macro_signals_are_rejected(
    signal: str,
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    replacements: dict[str, bytes] = {}
    if signal == "content_type":
        replacements["[Content_Types].xml"] = _base_entries()[
            "[Content_Types].xml"
        ].replace(
            b"wordprocessingml.document.main+xml",
            b"wordprocessingml.document.macroEnabled.main+xml",
        )
    elif signal == "relationship":
        replacements["word/_rels/document.xml.rels"] = (
            f'<?xml version="1.0"?><Relationships xmlns="{REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" '
            'Target="styles.xml"/></Relationships>'
        ).encode()
    else:
        replacements["word/vbaProject.bin"] = b"opaque"
    _assert_code(
        _docx(replacements),
        TemplateValidationErrorCode.ACTIVE_CONTENT,
        limits,
        declaration,
        font_policy,
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    (
        (b"not-a-zip", TemplateValidationErrorCode.INVALID_PACKAGE),
        (
            _docx(remove=frozenset({"word/styles.xml"})),
            TemplateValidationErrorCode.INVALID_PACKAGE,
        ),
        (
            _docx(duplicate=("WORD/STYLES.XML", b"duplicate")),
            TemplateValidationErrorCode.INVALID_PACKAGE,
        ),
    ),
)
def test_invalid_packages_fail_with_stable_content_free_categories(
    data: bytes,
    expected: TemplateValidationErrorCode,
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    _assert_code(data, expected, limits, declaration, font_policy)


def test_doctype_and_entity_are_rejected(
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    hostile = _base_entries()["word/document.xml"].replace(
        b"<w:document",
        b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><w:document',
    )
    _assert_code(
        _docx({"word/document.xml": hostile}),
        TemplateValidationErrorCode.INVALID_PACKAGE,
        limits,
        declaration,
        font_policy,
    )


def test_undeclared_and_unknown_fonts_fail_closed(
    limits: TemplateLimits, font_policy: FontPolicy
) -> None:
    for declaration in (
        TemplateFontDeclaration(("Calibri", "Courier New")),
        TemplateFontDeclaration(("Calibri", "Cambria", "Courier New", "Unknown Font")),
        TemplateFontDeclaration(("Calibri", "Cambria", "Courier New", "c\uff21libri")),
    ):
        _assert_code(
            _docx(),
            TemplateValidationErrorCode.FONT_CONTRACT,
            limits,
            declaration,
            font_policy,
        )


def test_explicit_archive_and_xml_limits_fail_closed(
    limits: TemplateLimits,
    declaration: TemplateFontDeclaration,
    font_policy: FontPolicy,
) -> None:
    constrained = TemplateLimits(
        len(_docx()) - 1,
        limits.max_entries,
        limits.max_member_uncompressed_bytes,
        limits.max_total_uncompressed_bytes,
        limits.max_compression_ratio,
        limits.max_xml_elements,
        limits.max_xml_depth,
        limits.max_xml_attributes,
        limits.max_declared_fonts,
        limits.max_font_name_characters,
    )
    _assert_code(
        _docx(),
        TemplateValidationErrorCode.LIMIT_EXCEEDED,
        constrained,
        declaration,
        font_policy,
    )
    shallow = TemplateLimits(
        2_000_000, 100, 500_000, 1_000_000, 200.0, 5_000, 2, 20_000, 20, 80
    )
    _assert_code(
        _docx(),
        TemplateValidationErrorCode.LIMIT_EXCEEDED,
        shallow,
        declaration,
        font_policy,
    )


@pytest.mark.parametrize(
    "arguments",
    (
        (("Carlito", "\uff23arlito"), ()),
        (("Carlito",), (("Calibri", "Missing"),)),
        ((), ()),
    ),
)
def test_font_policy_configuration_rejects_ambiguous_catalogs(
    arguments: tuple[tuple[str, ...], tuple[tuple[str, str], ...]],
) -> None:
    with pytest.raises(ValueError):
        FontPolicy(*arguments)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_archive_bytes", 0),
        ("max_entries", True),
        ("max_compression_ratio", 0.5),
        ("max_compression_ratio", float("nan")),
        ("max_compression_ratio", "invalid"),
    ),
)
def test_template_limits_reject_every_invalid_value(
    limits: TemplateLimits, field: str, value: Any
) -> None:
    with pytest.raises(ValueError):
        replace(limits, **{field: value})


def test_font_policy_resolves_exact_family_and_rejects_duplicate_alias() -> None:
    policy = FontPolicy(("Carlito",), (("Calibri", "Carlito"),))
    assert policy.resolve("\uff23arlito") == "Carlito"
    assert policy.resolve("Calibri") == "Carlito"
    assert policy.resolve("Unknown") is None
    with pytest.raises(ValueError):
        FontPolicy(
            ("Carlito",),
            (("Calibri", "Carlito"), ("\uff23alibri", "Carlito")),
        )


@pytest.mark.parametrize(
    "families",
    (
        (),
        ("Calibri",) * 21,
        cast("tuple[str, ...]", (123,)),
        (" ",),
        ("A" * 81,),
        ("Bad\nFont",),
        ("Calibri", "\uff23alibri"),
    ),
)
def test_font_declaration_bounds_and_normalized_duplicates_fail_closed(
    families: tuple[str, ...], limits: TemplateLimits, font_policy: FontPolicy
) -> None:
    with pytest.raises(TemplateValidationError) as captured:
        TemplateFontDeclaration(families).validate(limits, font_policy)
    assert captured.value.code is TemplateValidationErrorCode.FONT_CONTRACT


@pytest.mark.parametrize(
    "name",
    ("", "../escape.xml", "/absolute.xml", r"word\styles.xml", "C:part.xml", "."),
)
def test_unsafe_opc_part_names_are_rejected_directly(name: str) -> None:
    info = zipfile.ZipInfo("placeholder")
    info.filename = name
    with pytest.raises(TemplateValidationError):
        template_validation._safe_part_name(info)


@pytest.mark.parametrize(
    "target",
    (
        "",
        "https://example.invalid/a",
        "//host/share",
        r"..\escape",
        "../../escape.xml",
        "%2e%2e/%2e%2e/escape.xml",
        "styles.xml?query=1",
    ),
)
def test_invalid_internal_relationship_targets_fail_closed(target: str) -> None:
    with pytest.raises(TemplateValidationError):
        template_validation._internal_target(PurePosixPath("word/document.xml"), target)
    assert (
        template_validation._internal_target(
            PurePosixPath("word/document.xml"), "../word/styles.xml"
        )
        == "word/styles.xml"
    )


def test_invalid_relationship_part_location_and_dangling_id_are_rejected() -> None:
    with pytest.raises(TemplateValidationError):
        template_validation._relationship_source("word/not-a-relationship.rels")
    document = ElementTree.fromstring(  # noqa: S314 - fixed test literal
        f'<w:document xmlns:w="{WORD_NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="absent"/>'
    )
    with pytest.raises(TemplateValidationError):
        template_validation._inspect_relationship_references(
            {"word/document.xml": document}, {}
        )


@pytest.mark.parametrize(
    "limits_override",
    (
        {"max_xml_elements": 1},
        {"max_xml_depth": 1},
        {"max_xml_attributes": 1},
    ),
)
def test_each_xml_complexity_limit_is_enforced(
    limits: TemplateLimits, limits_override: dict[str, int]
) -> None:
    constrained = replace(limits, **limits_override)
    with pytest.raises(TemplateValidationError) as captured:
        template_validation._parse_xml(
            b'<root one="1" two="2"><child/></root>', constrained
        )
    assert captured.value.code is TemplateValidationErrorCode.LIMIT_EXCEEDED


def test_theme_and_font_table_references_are_inventory_observations() -> None:
    root = ElementTree.fromstring(  # noqa: S314 - fixed test literal
        f'<root xmlns:w="{WORD_NS}"><w:font w:name="Carlito"/>'
        '<latin typeface="Caladea"/><ea typeface=""/><cs typeface="DejaVu Serif"/></root>'
    )
    assert template_validation._referenced_fonts({"theme.xml": root}) == (
        "Caladea",
        "Carlito",
        "DejaVu Serif",
    )


def test_duplicate_style_name_and_missing_name_are_rejected() -> None:
    duplicate = ElementTree.fromstring(  # noqa: S314 - fixed test literal
        f'<w:styles xmlns:w="{WORD_NS}">'
        '<w:style w:type="paragraph" w:styleId="One"><w:name w:val="Same"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Two"><w:name w:val="same"/></w:style>'
        "</w:styles>"
    )
    missing = ElementTree.fromstring(  # noqa: S314 - fixed test literal
        f'<w:styles xmlns:w="{WORD_NS}"><w:style w:type="paragraph" w:styleId="One"/></w:styles>'
    )
    for root in (duplicate, missing):
        with pytest.raises(TemplateValidationError) as captured:
            template_validation._inspect_styles(root)
        assert captured.value.code is TemplateValidationErrorCode.REQUIRED_STYLES
