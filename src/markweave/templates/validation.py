"""Bounded, side-effect-free validation of untrusted Word reference templates."""

from __future__ import annotations

import hashlib
import io
import math
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from markweave.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)

_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_DOCX_MAIN_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_READ_CHUNK_BYTES = 64 * 1024
_ZIP_SYSTEM_UNIX = 3
_SUPPORTED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_FONT_ATTRIBUTES = frozenset({"ascii", "hAnsi", "eastAsia", "cs"})
_PROHIBITED_REL_SUFFIXES = (
    "/attachedtemplate",
    "/afchunk",
    "/oleobject",
    "/package",
    "/control",
    "/vbaproject",
)
_PROHIBITED_PART_SEGMENTS = frozenset({"activex", "customui", "embeddings"})
_PROHIBITED_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vba",
    "activex",
    "oleobject",
    "embeddedpackage",
    "controlproperties",
)


@dataclass(frozen=True)
class TemplateLimits:
    """Explicit validation bounds whose production values remain owned by T18."""

    max_archive_bytes: int
    max_entries: int
    max_member_uncompressed_bytes: int
    max_total_uncompressed_bytes: int
    max_compression_ratio: float
    max_xml_elements: int
    max_xml_depth: int
    max_xml_attributes: int
    max_declared_fonts: int
    max_font_name_characters: int

    def __post_init__(self) -> None:
        integers = (
            self.max_archive_bytes,
            self.max_entries,
            self.max_member_uncompressed_bytes,
            self.max_total_uncompressed_bytes,
            self.max_xml_elements,
            self.max_xml_depth,
            self.max_xml_attributes,
            self.max_declared_fonts,
            self.max_font_name_characters,
        )
        if any(type(value) is not int or value <= 0 for value in integers):
            raise ValueError("Template integer limits must be positive integers")
        if type(self.max_compression_ratio) not in {int, float}:
            raise ValueError(
                "Template compression ratio must be finite and at least one"
            )
        try:
            valid_ratio = (
                math.isfinite(self.max_compression_ratio)
                and self.max_compression_ratio >= 1.0
            )
        except OverflowError, TypeError, ValueError:
            valid_ratio = False
        if not valid_ratio:
            raise ValueError(
                "Template compression ratio must be finite and at least one"
            )


def _font_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


@dataclass(frozen=True)
class FontPolicy:
    """Immutable approved font families and source-to-installed substitutions."""

    approved_families: tuple[str, ...]
    substitutions: tuple[tuple[str, str], ...]
    supported_scripts: tuple[str, ...] = ("Latn", "Grek")

    def __post_init__(self) -> None:
        approved_keys = tuple(_font_key(value) for value in self.approved_families)
        if not approved_keys or any(not key for key in approved_keys):
            raise ValueError("Approved font families must be non-empty")
        if len(set(approved_keys)) != len(approved_keys):
            raise ValueError("Approved font families must be normalized-unique")
        sources: set[str] = set()
        approved = set(approved_keys)
        for source, target in self.substitutions:
            source_key, target_key = _font_key(source), _font_key(target)
            if not source_key or source_key in sources:
                raise ValueError("Font substitutions must have unique source families")
            if target_key not in approved:
                raise ValueError("Font substitutions must target approved families")
            sources.add(source_key)
        script_keys = tuple(_font_key(value) for value in self.supported_scripts)
        if not script_keys or any(not key for key in script_keys):
            raise ValueError("Supported font scripts must be non-empty")
        if len(set(script_keys)) != len(script_keys):
            raise ValueError("Supported font scripts must be normalized-unique")

    def resolve(self, family: str) -> str | None:
        """Resolve one source family to its installed approved family."""

        key = _font_key(family)
        for approved in self.approved_families:
            if _font_key(approved) == key:
                return approved
        for source, target in self.substitutions:
            if _font_key(source) == key:
                return target
        return None


APPROVED_FONT_POLICY = FontPolicy(
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
        ("Consolas", "Liberation Mono"),
        ("Calibri", "Carlito"),
        ("Aptos", "Carlito"),
        ("Aptos Display", "Carlito"),
        ("Cambria", "Caladea"),
        ("Cambria Math", "DejaVu Serif"),
    ),
)


@dataclass(frozen=True)
class TemplateFontDeclaration:
    """Expected source families supplied alongside one template version."""

    families: tuple[str, ...]

    def validate(self, limits: TemplateLimits, policy: FontPolicy) -> tuple[str, ...]:
        """Return normalized names after bounded policy validation."""

        if not self.families or len(self.families) > limits.max_declared_fonts:
            _font_contract()
        normalized: list[str] = []
        keys: set[str] = set()
        for family in self.families:
            if type(family) is not str:
                _font_contract()
            clean = unicodedata.normalize("NFKC", family).strip()
            if (
                not clean
                or len(clean) > limits.max_font_name_characters
                or any(unicodedata.category(char).startswith("C") for char in clean)
            ):
                _font_contract()
            key = _font_key(clean)
            if key in keys or policy.resolve(clean) is None:
                _font_contract()
            keys.add(key)
            normalized.append(clean)
        return tuple(normalized)


@dataclass(frozen=True)
class RequiredStyle:
    """One Pandoc 3.10.2 reference style contract entry."""

    style_id: str
    name: str
    style_type: str


PANDOC_REQUIRED_STYLES = (
    RequiredStyle("Normal", "Normal", "paragraph"),
    RequiredStyle("BodyText", "Body Text", "paragraph"),
    RequiredStyle("FirstParagraph", "First Paragraph", "paragraph"),
    RequiredStyle("Compact", "Compact", "paragraph"),
    RequiredStyle("Title", "Title", "paragraph"),
    RequiredStyle("Subtitle", "Subtitle", "paragraph"),
    RequiredStyle("Author", "Author", "paragraph"),
    RequiredStyle("Date", "Date", "paragraph"),
    RequiredStyle("AbstractTitle", "Abstract Title", "paragraph"),
    RequiredStyle("Abstract", "Abstract", "paragraph"),
    RequiredStyle("Bibliography", "Bibliography", "paragraph"),
    *(RequiredStyle(f"Heading{i}", f"heading {i}", "paragraph") for i in range(1, 10)),
    RequiredStyle("BlockText", "Block Text", "paragraph"),
    RequiredStyle("FootnoteText", "Footnote Text", "paragraph"),
    RequiredStyle("FootnoteBlockText", "Footnote Block Text", "paragraph"),
    RequiredStyle("DefaultParagraphFont", "Default Paragraph Font", "character"),
    RequiredStyle("Table", "Table", "table"),
    RequiredStyle("DefinitionTerm", "Definition Term", "paragraph"),
    RequiredStyle("Definition", "Definition", "paragraph"),
    RequiredStyle("Caption", "Caption", "paragraph"),
    RequiredStyle("TableCaption", "Table Caption", "paragraph"),
    RequiredStyle("ImageCaption", "Image Caption", "paragraph"),
    RequiredStyle("Figure", "Figure", "paragraph"),
    RequiredStyle("CaptionedFigure", "Captioned Figure", "paragraph"),
    RequiredStyle("BodyTextChar", "Body Text Char", "character"),
    RequiredStyle("VerbatimChar", "Verbatim Char", "character"),
    RequiredStyle("SectionNumber", "Section Number", "character"),
    RequiredStyle("FootnoteReference", "Footnote Reference", "character"),
    RequiredStyle("Hyperlink", "Hyperlink", "character"),
    RequiredStyle("TOCHeading", "TOC Heading", "paragraph"),
)


@dataclass(frozen=True)
class ValidatedTemplate:
    """Immutable observations safe to associate with a future template version."""

    sha256: str
    parts: tuple[str, ...]
    declared_fonts: tuple[str, ...]
    referenced_fonts: tuple[str, ...]
    resolved_fonts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Member:
    info: zipfile.ZipInfo
    name: str
    key: str


def _error(code: TemplateValidationErrorCode, message: str) -> None:
    raise TemplateValidationError(code, message)


def _invalid_package() -> None:
    _error(
        TemplateValidationErrorCode.INVALID_PACKAGE, "Word template package is invalid."
    )


def _limit_exceeded() -> None:
    _error(
        TemplateValidationErrorCode.LIMIT_EXCEEDED,
        "Word template exceeds configured limits.",
    )


def _font_contract() -> None:
    _error(
        TemplateValidationErrorCode.FONT_CONTRACT,
        "Word template font contract is invalid.",
    )


def _part_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _safe_part_name(info: zipfile.ZipInfo) -> str:
    name = info.filename.removesuffix("/") if info.is_dir() else info.filename
    if not name or "\0" in name or "\\" in name or name.startswith(("/", "//")):
        _invalid_package()
    path = PurePosixPath(name)
    if path.parts in {(), (".",)} or ".." in path.parts or ":" in path.parts[0]:
        _invalid_package()
    if path.as_posix() != name:
        _invalid_package()
    return name


def _preflight(archive: zipfile.ZipFile, limits: TemplateLimits) -> tuple[_Member, ...]:
    infos = archive.infolist()
    if not infos:
        _invalid_package()
    if len(infos) > limits.max_entries:
        _limit_exceeded()
    members: list[_Member] = []
    keys: set[str] = set()
    file_keys: set[str] = set()
    total = 0
    for info in infos:
        name = _safe_part_name(info)
        key = _part_key(name)
        mode = (
            stat.S_IFMT(info.external_attr >> 16)
            if info.create_system == _ZIP_SYSTEM_UNIX
            else 0
        )
        valid_modes = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
        if (
            key in keys
            or info.flag_bits & 1
            or mode not in valid_modes
            or info.compress_type not in _SUPPORTED_COMPRESSION
            or info.file_size < 0
            or info.compress_size < 0
        ):
            _invalid_package()
        keys.add(key)
        if not info.is_dir():
            file_keys.add(key)
        if (
            info.file_size > limits.max_member_uncompressed_bytes
            or info.file_size / max(info.compress_size, 1)
            > limits.max_compression_ratio
        ):
            _limit_exceeded()
        total += info.file_size
        if total > limits.max_total_uncompressed_bytes:
            _limit_exceeded()
        members.append(_Member(info, name, key))
    for member in members:
        parent = PurePosixPath(member.name).parent
        while parent.parts not in {(), (".",)}:
            if _part_key(parent.as_posix()) in file_keys:
                _invalid_package()
            parent = parent.parent
    return tuple(members)


def _read_member(
    archive: zipfile.ZipFile,
    member: _Member,
    limits: TemplateLimits,
    total_read: int,
) -> tuple[bytes, int]:
    payload = bytearray()
    actual = 0
    try:
        with archive.open(member.info) as source:
            while True:
                read_size = min(
                    _READ_CHUNK_BYTES,
                    limits.max_member_uncompressed_bytes - actual + 1,
                    limits.max_total_uncompressed_bytes - total_read - actual + 1,
                    member.info.file_size - actual + 1,
                )
                chunk = source.read(max(read_size, 1))
                if not chunk:
                    break
                actual += len(chunk)
                if (
                    actual > member.info.file_size
                    or actual > limits.max_member_uncompressed_bytes
                    or total_read + actual > limits.max_total_uncompressed_bytes
                ):
                    _limit_exceeded()
                payload.extend(chunk)
    except TemplateValidationError:
        raise
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        zlib.error,
    ):
        _invalid_package()
    if actual != member.info.file_size:
        _invalid_package()
    return bytes(payload), total_read + actual


def _parse_xml(payload: bytes, limits: TemplateLimits) -> ElementTree.Element:
    try:
        root = DefusedElementTree.fromstring(payload)
    except DefusedXmlException, ElementTree.ParseError, UnicodeError, ValueError:
        _invalid_package()
    elements = attributes = 0
    stack: list[tuple[ElementTree.Element, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        elements += 1
        attributes += len(node.attrib)
        if (
            elements > limits.max_xml_elements
            or attributes > limits.max_xml_attributes
            or depth > limits.max_xml_depth
        ):
            _limit_exceeded()
        stack.extend((child, depth + 1) for child in node)
    return root


def _relationship_source(part_name: str) -> PurePosixPath:
    path = PurePosixPath(part_name)
    if part_name == _ROOT_RELS:
        return PurePosixPath("/")
    if path.parent.name != "_rels" or not path.name.endswith(".rels"):
        _invalid_package()
    return path.parent.parent / path.name.removesuffix(".rels")


def _internal_target(source: PurePosixPath, target: str) -> str:
    decoded = unquote(target)
    split = urlsplit(decoded)
    if (
        not decoded
        or "\0" in decoded
        or "\\" in decoded
        or split.scheme
        or split.netloc
        or decoded.startswith(("/", "//"))
        or split.query
    ):
        _invalid_package()
    combined = (
        PurePosixPath(split.path)
        if source == PurePosixPath("/")
        else source.parent / PurePosixPath(split.path)
    )
    parts: list[str] = []
    for part in combined.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if not parts:
                _invalid_package()
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        _invalid_package()
    return PurePosixPath(*parts).as_posix()


def _inspect_relationships(
    roots: dict[str, ElementTree.Element], part_keys: set[str]
) -> dict[str, frozenset[str]]:
    office_targets: list[str] = []
    identifiers_by_source: dict[str, frozenset[str]] = {}
    for part_name, root in roots.items():
        if not part_name.endswith(".rels"):
            continue
        if root.tag != f"{{{_REL_NS}}}Relationships":
            _invalid_package()
        source = _relationship_source(part_name)
        if (
            source != PurePosixPath("/")
            and _part_key(source.as_posix()) not in part_keys
        ):
            _invalid_package()
        identifiers: set[str] = set()
        for node in root.iter(f"{{{_REL_NS}}}Relationship"):
            identifier = node.attrib.get("Id", "")
            relationship_type = node.attrib.get("Type", "")
            target = node.attrib.get("Target", "")
            if (
                not identifier
                or identifier in identifiers
                or not relationship_type
                or not target
            ):
                _invalid_package()
            identifiers.add(identifier)
            target_mode = node.attrib.get("TargetMode", "").casefold()
            if target_mode == "external":
                _error(
                    TemplateValidationErrorCode.EXTERNAL_RELATIONSHIP,
                    "Word template contains an external relationship.",
                )
            if target_mode not in {"", "internal"}:
                _invalid_package()
            lowered_type = relationship_type.casefold()
            if lowered_type.endswith(_PROHIBITED_REL_SUFFIXES):
                _error(
                    TemplateValidationErrorCode.ACTIVE_CONTENT,
                    "Word template contains active content.",
                )
            resolved = _internal_target(source, target)
            if _part_key(resolved) not in part_keys:
                _invalid_package()
            if part_name == _ROOT_RELS and relationship_type == _OFFICE_DOCUMENT_REL:
                office_targets.append(resolved)
        identifiers_by_source[source.as_posix()] = frozenset(identifiers)
    if office_targets != ["word/document.xml"]:
        _invalid_package()
    return identifiers_by_source


def _inspect_relationship_references(
    roots: dict[str, ElementTree.Element],
    identifiers_by_source: dict[str, frozenset[str]],
) -> None:
    for part_name, root in roots.items():
        if part_name.endswith(".rels") or part_name == _CONTENT_TYPES:
            continue
        identifiers = identifiers_by_source.get(part_name, frozenset())
        for node in root.iter():
            for attribute, value in node.attrib.items():
                if (
                    attribute.startswith(f"{{{_OFFICE_REL_NS}}}")
                    and value not in identifiers
                ):
                    _invalid_package()


def _inspect_content_types(root: ElementTree.Element) -> None:
    main_parts: list[str] = []
    for kind in ("Default", "Override"):
        for node in root.iter(f"{{{_TYPE_NS}}}{kind}"):
            lowered = node.attrib.get("ContentType", "").casefold()
            if any(marker in lowered for marker in _PROHIBITED_CONTENT_TYPE_MARKERS):
                _error(
                    TemplateValidationErrorCode.ACTIVE_CONTENT,
                    "Word template contains active content.",
                )
    for node in root.iter(f"{{{_TYPE_NS}}}Override"):
        part_name = node.attrib.get("PartName", "")
        content_type = node.attrib.get("ContentType", "")
        if content_type == _DOCX_MAIN_TYPE:
            main_parts.append(part_name)
    if main_parts != ["/word/document.xml"]:
        _invalid_package()


def _inspect_styles(root: ElementTree.Element) -> None:
    if root.tag != f"{{{_WORD_NS}}}styles":
        _error(
            TemplateValidationErrorCode.REQUIRED_STYLES,
            "Word template style contract is invalid.",
        )
    styles: dict[str, tuple[str, str]] = {}
    names: set[tuple[str, str]] = set()
    for node in root.iter(f"{{{_WORD_NS}}}style"):
        style_id = node.attrib.get(f"{{{_WORD_NS}}}styleId", "")
        style_type = node.attrib.get(f"{{{_WORD_NS}}}type", "")
        name_node = node.find(f"{{{_WORD_NS}}}name")
        name = (
            "" if name_node is None else name_node.attrib.get(f"{{{_WORD_NS}}}val", "")
        )
        normalized_name = _font_key(name)
        if (
            not style_id
            or not style_type
            or not name
            or style_id in styles
            or (style_type, normalized_name) in names
        ):
            _error(
                TemplateValidationErrorCode.REQUIRED_STYLES,
                "Word template style contract is invalid.",
            )
        styles[style_id] = (style_type, normalized_name)
        names.add((style_type, normalized_name))
    for required in PANDOC_REQUIRED_STYLES:
        if styles.get(required.style_id) != (
            required.style_type,
            _font_key(required.name),
        ):
            _error(
                TemplateValidationErrorCode.REQUIRED_STYLES,
                "Word template style contract is invalid.",
            )


def _referenced_fonts(
    roots: dict[str, ElementTree.Element], policy: FontPolicy
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    fonts: dict[str, str] = {}
    scripts: dict[str, str] = {}
    supported_scripts = {_font_key(value) for value in policy.supported_scripts}
    for root in roots.values():
        for node in root.iter():
            local_name = node.tag.rsplit("}", 1)[-1]
            if local_name == "font":
                value = node.attrib.get(f"{{{_WORD_NS}}}name")
                if value:
                    fonts.setdefault(_font_key(value), value)
            if local_name == "rFonts":
                for attribute, value in node.attrib.items():
                    if attribute.rsplit("}", 1)[-1] in _FONT_ATTRIBUTES and value:
                        fonts.setdefault(_font_key(value), value)
            if local_name in {"latin", "ea", "cs"}:
                value = node.attrib.get("typeface")
                if value:
                    fonts.setdefault(_font_key(value), value)
            if node.tag == f"{{{_DRAWING_NS}}}font":
                family = node.attrib.get("typeface", "")
                script = node.attrib.get("script", "")
                if family:
                    script_key = _font_key(script)
                    if not script_key or script_key not in supported_scripts:
                        _font_contract()
                    fonts.setdefault(_font_key(family), family)
                    scripts.setdefault(script_key, script)
    return (
        tuple(fonts[key] for key in sorted(fonts)),
        tuple(scripts[key] for key in sorted(scripts)),
    )


def _validate_template(
    data: bytes,
    declaration: TemplateFontDeclaration,
    limits: TemplateLimits,
    policy: FontPolicy,
    *,
    enforce_referenced_fonts: bool,
) -> ValidatedTemplate:
    """Inspect one DOCX without extraction or relationship access."""

    declared = declaration.validate(limits, policy)
    if type(data) is not bytes or not data:
        _invalid_package()
    if len(data) > limits.max_archive_bytes:
        _limit_exceeded()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except OSError, zipfile.BadZipFile:
        _invalid_package()
    payloads: dict[str, bytes] = {}
    with archive:
        members = _preflight(archive, limits)
        total_read = 0
        for member in members:
            if member.info.is_dir():
                continue
            payloads[member.name], total_read = _read_member(
                archive, member, limits, total_read
            )
    required = {_CONTENT_TYPES, _ROOT_RELS, "word/document.xml", "word/styles.xml"}
    if not required <= payloads.keys():
        _invalid_package()
    for part_name in payloads:
        lowered_parts = set(PurePosixPath(part_name.casefold()).parts)
        lowered_name = PurePosixPath(part_name).name.casefold()
        if (
            lowered_parts & _PROHIBITED_PART_SEGMENTS
            or "vbaproject" in lowered_name
            or lowered_name.endswith((".bin", ".xls", ".xlsx", ".docm", ".dotm"))
        ):
            _error(
                TemplateValidationErrorCode.ACTIVE_CONTENT,
                "Word template contains active content.",
            )
    roots = {
        part_name: _parse_xml(payload, limits)
        for part_name, payload in payloads.items()
        if part_name == _CONTENT_TYPES or part_name.endswith((".xml", ".rels"))
    }
    if roots[_CONTENT_TYPES].tag != f"{{{_TYPE_NS}}}Types":
        _invalid_package()
    if roots["word/document.xml"].tag != f"{{{_WORD_NS}}}document":
        _invalid_package()
    _inspect_content_types(roots[_CONTENT_TYPES])
    relationship_ids = _inspect_relationships(
        roots, {_part_key(name) for name in payloads}
    )
    _inspect_relationship_references(roots, relationship_ids)
    _inspect_styles(roots["word/styles.xml"])
    referenced, _ = _referenced_fonts(roots, policy)
    declared_keys = {_font_key(family) for family in declared}
    resolved_keys = {
        _font_key(resolved)
        for family in declared
        if (resolved := policy.resolve(family)) is not None
    }
    if enforce_referenced_fonts and any(
        _font_key(family) not in declared_keys | resolved_keys for family in referenced
    ):
        _font_contract()
    resolved = tuple((family, policy.resolve(family) or "") for family in declared)
    return ValidatedTemplate(
        hashlib.sha256(data).hexdigest(),
        tuple(sorted(payloads)),
        declared,
        referenced,
        resolved,
    )


def validate_template(
    data: bytes,
    declaration: TemplateFontDeclaration,
    limits: TemplateLimits,
    policy: FontPolicy,
) -> ValidatedTemplate:
    """Validate an activation candidate, including its expected-font declaration."""

    return _validate_template(
        data,
        declaration,
        limits,
        policy,
        enforce_referenced_fonts=True,
    )
