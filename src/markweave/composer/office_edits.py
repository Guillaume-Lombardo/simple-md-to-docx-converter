"""Shared admission, text-node location, splicing and verification for Office edits.

Each supported format supplies its package kind and text grammar. An edit only
replaces the whole text of eligible text nodes by splicing their exact bytes, so
every other byte of an edited part and every other package member is preserved
and verified after the write. Formats never share target identities.
"""

from __future__ import annotations

import copy
import io
import json
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import ClassVar
from xml.etree import ElementTree
from xml.parsers import expat
from xml.sax.saxutils import escape

from markweave.composer.typed_templates import TypedDocxLimits
from markweave.templates import validation as package_validation
from markweave.templates.errors import TemplateValidationError

OFFICE_EDIT_ENGINE_VERSION = 1
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_SPACE = "http://www.w3.org/XML/1998/namespace space"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_HYPERLINK_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_UNSAFE_PART_MARKERS = ("_xmlsignatures", "digitalsignature", ".sigs", "vba", "macros")
_TARGET_SEPARATOR = "#t"
MAXIMUM_TARGET_CHARACTERS = 64
_QUOTES = frozenset(b"\"'")
_TAG_CLOSE = ord(">")
_SLASH = ord("/")


class OfficeEditError(ValueError):
    """Content-free, stable rejection naming only the affected target identities."""

    format: ClassVar[str] = "office"

    def __init__(self, code: str, targets: tuple[str, ...] = ()) -> None:
        super().__init__(f"{self.format.upper()} edit {code.replace('_', ' ')}")
        self.code = code
        self.targets = targets


class DocxEditError(OfficeEditError):
    """A rejected Word edit."""

    format = "docx"


class PptxEditError(OfficeEditError):
    """A rejected PowerPoint edit."""

    format = "pptx"


@dataclass(frozen=True, slots=True)
class ReplaceText:
    """Replace the whole text of one target that still holds ``expected_text``."""

    target: str
    expected_text: str
    text: str

    def as_record(self) -> dict[str, str]:
        return {
            "op": "replace_text",
            "target": self.target,
            "expected_text": self.expected_text,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class TextGrammar:
    """Expat names that locate editable runs of one markup language."""

    text: str
    run: str
    paragraph: str
    table: str
    excluded_ancestors: frozenset[str]
    allowed_attributes: frozenset[str]
    field_character: str | None = None
    field_type: str | None = None
    adds_space_preserve: bool = False


@dataclass(frozen=True, slots=True)
class TextPart:
    """One package part whose body text may be edited, with display context."""

    name: str
    slide: int | None = None
    notes: bool = False


@dataclass(frozen=True, slots=True)
class PackageKind:
    """A supported Office format, its main part and its editable text parts."""

    main_part: str
    root_tag: str
    error: type[OfficeEditError]
    grammar: TextGrammar
    text_parts: Callable[
        [dict[str, bytes], dict[str, ElementTree.Element]], tuple[TextPart, ...]
    ]


@dataclass(frozen=True, slots=True)
class LocatedText:
    """One eligible text node with its stable identity and display context."""

    target: str
    part: TextPart
    paragraph: int
    in_table: bool
    text: str


@dataclass(slots=True)
class _PendingText:
    start: int
    content_start: int
    self_closing: bool
    eligible: bool
    preserves_space: bool
    in_table: bool
    paragraph: int
    depth: int
    parts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _TextNode:
    ordinal: int
    paragraph: int
    in_table: bool
    eligible: bool
    text: str
    start: int
    content_start: int
    content_end: int
    end: int
    preserves_space: bool


@dataclass(frozen=True, slots=True)
class _Admitted:
    payloads: dict[str, bytes]
    parts: tuple[TextPart, ...]


def operations_record(operations: tuple[ReplaceText, ...]) -> str:
    """Return the canonical frozen operation record stored with a revision."""

    return json.dumps(
        {
            "engine_version": OFFICE_EDIT_ENGINE_VERSION,
            "operations": [operation.as_record() for operation in operations],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _package_limits(limits: TypedDocxLimits) -> package_validation.TemplateLimits:
    return package_validation.TemplateLimits(
        limits.max_archive_bytes,
        limits.max_entries,
        limits.max_member_bytes,
        limits.max_total_bytes,
        limits.max_compression_ratio,
        limits.max_xml_elements,
        limits.max_xml_depth,
        limits.max_xml_attributes,
        limits.max_entries,
        limits.max_text_characters,
    )


def _inspect_relationships(
    roots: dict[str, ElementTree.Element],
    payloads: dict[str, bytes],
    kind: PackageKind,
) -> None:
    """Allow external hyperlinks, which are never fetched; reject every other one."""

    filtered: dict[str, ElementTree.Element] = {}
    external: dict[str, set[str]] = {}
    for name, root in roots.items():
        if not name.endswith(".rels"):
            filtered[name] = root
            continue
        clone = copy.deepcopy(root)
        for node in list(clone):
            if node.get("TargetMode", "").casefold() != "external":
                continue
            identifier = node.get("Id", "")
            if node.get("Type") != _HYPERLINK_REL or not identifier:
                raise kind.error("external_relationship")
            source = package_validation._relationship_source(name).as_posix()
            identifiers = external.setdefault(source, set())
            if identifier in identifiers:
                raise kind.error("invalid_package")
            identifiers.add(identifier)
            clone.remove(node)
        filtered[name] = clone
    internal = package_validation._inspect_relationships(
        filtered,
        {package_validation._part_key(name) for name in payloads},
        kind.main_part,
    )
    merged = dict(internal)
    for source, identifiers in external.items():
        if identifiers & internal.get(source, frozenset()):
            raise kind.error("invalid_package")
        merged[source] = internal.get(source, frozenset()) | frozenset(identifiers)
    package_validation._inspect_relationship_references(roots, merged)


def _admit(content: bytes, limits: TypedDocxLimits, kind: PackageKind) -> _Admitted:
    """Reuse the bounded Office archive preflight, reads and OPC checks."""

    if type(content) is not bytes or not content:
        raise kind.error("invalid_package")
    if len(content) > limits.max_archive_bytes:
        raise kind.error("limit_exceeded")
    configured = _package_limits(limits)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = package_validation._preflight(archive, configured)
            payloads: dict[str, bytes] = {}
            total = 0
            for member in members:
                if member.info.is_dir():
                    continue
                payloads[member.name], total = package_validation._read_member(
                    archive, member, configured, total
                )
        if not payloads.keys() >= {_CONTENT_TYPES, _ROOT_RELS, kind.main_part}:
            raise kind.error("invalid_package")
        if any(
            any(marker in name.casefold() for marker in _UNSAFE_PART_MARKERS)
            for name in payloads
        ):
            raise kind.error("active_content")
        package_validation._inspect_active_parts(payloads)
        roots = {
            name: package_validation._parse_xml(payload, configured)
            for name, payload in payloads.items()
            if name == _CONTENT_TYPES or name.endswith((".xml", ".rels"))
        }
        if roots[kind.main_part].tag != kind.root_tag:
            raise kind.error("invalid_package")
        package_validation._inspect_content_types(roots[_CONTENT_TYPES], kind.main_part)
        for name, root in roots.items():
            if name.endswith(".rels") and root.tag != f"{{{_REL_NS}}}Relationships":
                raise kind.error("invalid_package")
        _inspect_relationships(roots, payloads, kind)
        parts = kind.text_parts(payloads, roots)
    except OfficeEditError:
        raise
    except TemplateValidationError as exc:
        raise kind.error(exc.code.value) from exc
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise kind.error("invalid_package") from exc
    return _Admitted(payloads, parts)


def internal_target(source: str, target: str) -> str:
    """Resolve an admitted internal relationship target from its source part."""

    return package_validation._internal_target(PurePosixPath(source), target)


def _start_tag_end(payload: bytes, start: int, error: type[OfficeEditError]) -> int:
    """Return the index of the ``>`` closing the start tag at ``start``."""

    quote = 0
    for index in range(start + 1, len(payload)):
        byte = payload[index]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in _QUOTES:
            quote = byte
        elif byte == _TAG_CLOSE:
            return index
    raise error("invalid_package")


def _text_nodes(  # noqa: PLR0915 - one streaming pass
    payload: bytes, grammar: TextGrammar, error: type[OfficeEditError]
) -> tuple[_TextNode, ...]:
    """Locate every text node with exact byte spans and edit eligibility."""

    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise error("unsupported_encoding")
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[str] = []
    nodes: list[_TextNode] = []
    current: _PendingText | None = None
    paragraph = -1
    fields = 0

    def reject(*_arguments: object) -> None:
        raise error("invalid_package")

    def declaration(_version: str, encoding: str | None, _standalone: int) -> None:
        if encoding is not None and encoding.casefold().replace("-", "") != "utf8":
            raise error("unsupported_encoding")

    def nested(*_arguments: object) -> None:
        # A text node holds only character data; any markup inside it is not
        # editable text and would be silently dropped by a replacement.
        if current is not None:
            current.eligible = False

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal current, paragraph, fields
        if current is not None:
            current.eligible = False
        elif name == grammar.paragraph:
            paragraph += 1
        elif name == grammar.field_character:
            kind = attributes.get(grammar.field_type or "")
            if kind == "begin":
                fields += 1
            elif kind == "end":
                fields = max(fields - 1, 0)
        elif name == grammar.text:
            begin = parser.CurrentByteIndex
            close = _start_tag_end(payload, begin, error)
            self_closing = payload[close - 1] == _SLASH
            current = _PendingText(
                start=begin,
                content_start=close + 1,
                self_closing=self_closing,
                eligible=(
                    not self_closing
                    and bool(stack)
                    and stack[-1] == grammar.run
                    and fields == 0
                    and not grammar.excluded_ancestors.intersection(stack)
                    and set(attributes) <= grammar.allowed_attributes
                    and attributes.get(XML_SPACE, "preserve") == "preserve"
                ),
                preserves_space=XML_SPACE in attributes,
                in_table=grammar.table in stack,
                paragraph=paragraph,
                depth=len(stack),
            )
        stack.append(name)

    def characters(data: str) -> None:
        if current is not None:
            current.parts.append(data)

    def end(name: str) -> None:
        nonlocal current
        stack.pop()
        if name != grammar.text or current is None or len(stack) != current.depth:
            return
        value = "".join(current.parts)
        if current.self_closing:
            content_end, close = current.content_start, current.content_start - 1
        else:
            content_end = parser.CurrentByteIndex
            close = payload.index(b">", content_end)
        nodes.append(
            _TextNode(
                ordinal=len(nodes),
                paragraph=current.paragraph,
                in_table=current.in_table,
                eligible=current.eligible and _plain(value),
                text=value,
                start=current.start,
                content_start=current.content_start,
                content_end=content_end,
                end=close + 1,
                preserves_space=current.preserves_space,
            )
        )
        current = None

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.XmlDeclHandler = declaration
    parser.CommentHandler = nested
    parser.ProcessingInstructionHandler = nested
    parser.StartElementHandler = start
    parser.CharacterDataHandler = characters
    parser.EndElementHandler = end
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise error("invalid_package") from exc
    return tuple(nodes)


def _split_target(target: object) -> tuple[str, int] | None:
    if type(target) is not str or len(target) > MAXIMUM_TARGET_CHARACTERS:
        return None
    part, separator, digits = target.rpartition(_TARGET_SEPARATOR)
    if (
        not separator
        or not digits.isascii()
        or not digits.isdigit()
        or (len(digits) > 1 and digits.startswith("0"))
    ):
        return None
    return part, int(digits)


def inspect_text(
    content: bytes, limits: TypedDocxLimits, kind: PackageKind
) -> tuple[LocatedText, ...]:
    """Admit an untrusted Office copy and list its editable text targets."""

    admitted = _admit(content, limits, kind)
    return tuple(
        LocatedText(
            f"{part.name}{_TARGET_SEPARATOR}{node.ordinal}",
            part,
            node.paragraph,
            node.in_table,
            node.text,
        )
        for part in admitted.parts
        for node in _text_nodes(admitted.payloads[part.name], kind.grammar, kind.error)
        if node.eligible
    )


def _plain(value: str) -> bool:
    return all(
        unicodedata.category(character) not in {"Cc", "Cs"}
        and character not in {"\ufffe", "\uffff"}
        for character in value
    )


def _valid_text(value: object, limits: TypedDocxLimits) -> bool:
    return (
        type(value) is str
        and len(value) <= limits.max_text_characters
        and _plain(value)
    )


def _replacement(
    payload: bytes, node: _TextNode, text: str, grammar: TextGrammar
) -> bytes:
    start_tag = payload[node.start : node.content_start]
    if (
        grammar.adds_space_preserve
        and not node.preserves_space
        and text != text.strip(" ")
    ):
        start_tag = start_tag[:-1] + b' xml:space="preserve">'
    return (
        start_tag + escape(text).encode("utf-8") + payload[node.content_end : node.end]
    )


def _splice(
    document: bytes,
    nodes: tuple[_TextNode, ...],
    selected: dict[int, ReplaceText],
    grammar: TextGrammar,
) -> bytes:
    segments: list[bytes] = []
    cursor = 0
    for ordinal in sorted(selected):
        node = nodes[ordinal]
        segments += (
            document[cursor : node.start],
            _replacement(document, node, selected[ordinal].text, grammar),
        )
        cursor = node.end
    segments.append(document[cursor:])
    return b"".join(segments)


def _write_package(source: bytes, replacements: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in original.infolist():
            payload = replacements.get(info.filename)
            target.writestr(info, original.read(info) if payload is None else payload)
    return output.getvalue()


def apply_text_edits(
    content: bytes,
    operations: tuple[ReplaceText, ...],
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> bytes:
    """Return a new package in which only the requested text nodes changed."""

    if not operations:
        raise kind.error("no_operations")
    admitted = _admit(content, limits, kind)
    editable = {part.name for part in admitted.parts}
    nodes: dict[str, tuple[_TextNode, ...]] = {}
    selected: dict[str, dict[int, ReplaceText]] = {}
    for operation in operations:
        located = _split_target(operation.target)
        if located is None or located[0] not in editable:
            raise kind.error(
                "unsupported_target",
                (str(operation.target)[:MAXIMUM_TARGET_CHARACTERS],),
            )
        part, ordinal = located
        if part not in nodes:
            nodes[part] = _text_nodes(admitted.payloads[part], kind.grammar, kind.error)
        if ordinal >= len(nodes[part]) or not nodes[part][ordinal].eligible:
            raise kind.error("unsupported_target", (operation.target,))
        chosen = selected.setdefault(part, {})
        if ordinal in chosen:
            raise kind.error("duplicate_target", (operation.target,))
        if not _valid_text(operation.text, limits) or not _valid_text(
            operation.expected_text, limits
        ):
            raise kind.error("invalid_text", (operation.target,))
        if nodes[part][ordinal].text != operation.expected_text:
            raise kind.error("target_changed", (operation.target,))
        chosen[ordinal] = operation
    revised = {
        part: _splice(admitted.payloads[part], nodes[part], chosen, kind.grammar)
        for part, chosen in selected.items()
    }
    if any(len(payload) > limits.max_member_bytes for payload in revised.values()):
        raise kind.error("limit_exceeded")
    output = _write_package(content, revised)
    if len(output) > limits.max_archive_bytes:
        raise kind.error("limit_exceeded")
    _verify(admitted, nodes, selected, output, limits, kind)
    return output


def _verify(  # noqa: PLR0913, PLR0917 - the complete before/after evidence
    admitted: _Admitted,
    nodes: dict[str, tuple[_TextNode, ...]],
    selected: dict[str, dict[int, ReplaceText]],
    output: bytes,
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> None:
    """Re-admit the copy and prove that only the requested text changed."""

    revised = _admit(output, limits, kind)
    if (
        revised.payloads.keys() != admitted.payloads.keys()
        or revised.parts != admitted.parts
        or any(
            revised.payloads[name] != payload
            for name, payload in admitted.payloads.items()
            if name not in selected
        )
    ):
        raise kind.error("package_changed")
    for part, chosen in selected.items():
        after = _text_nodes(revised.payloads[part], kind.grammar, kind.error)
        if len(after) != len(nodes[part]):
            raise kind.error("package_changed")
        for before, current in zip(nodes[part], after, strict=True):
            expected = (
                chosen[before.ordinal].text if before.ordinal in chosen else before.text
            )
            if (
                current.text != expected
                or current.eligible != before.eligible
                or current.paragraph != before.paragraph
                or current.in_table != before.in_table
            ):
                raise kind.error("package_changed")
