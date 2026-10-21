"""Bounded, surgical text edits on private copies of admitted DOCX packages.

This is the first T92 Office edit family. It only replaces the text of one
eligible ``w:t`` node per operation in ``word/document.xml``. The edit splices
the exact node bytes, so every other byte of that part and every other package
member is preserved and verified after the write.
"""

from __future__ import annotations

import copy
import io
import json
import unicodedata
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree
from xml.parsers import expat
from xml.sax.saxutils import escape

from markweave.composer.typed_templates import TypedDocxLimits
from markweave.templates import validation as package_validation
from markweave.templates.errors import TemplateValidationError

DOCX_EDIT_ENGINE_VERSION = 1
DOCX_EDIT_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_HYPERLINK_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_MAIN = "word/document.xml"
_REQUIRED = frozenset({"[Content_Types].xml", "_rels/.rels", _MAIN})
_UNSAFE_PART_MARKERS = ("_xmlsignatures", "digitalsignature", ".sigs", "vba", "macros")
_TEXT = f"{_W_NS} t"
_RUN = f"{_W_NS} r"
_PARAGRAPH = f"{_W_NS} p"
_TABLE = f"{_W_NS} tbl"
_FIELD_CHARACTER = f"{_W_NS} fldChar"
_FIELD_TYPE = f"{_W_NS} fldCharType"
_XML_SPACE = f"{_XML_NS} space"
# Text inside these containers is owned by another mechanism (tracked changes,
# content-control bindings, field results or alternate renderings) and would be
# overwritten or diverge on reopening, so it is never offered as a target.
_EXCLUDED_ANCESTORS = frozenset(
    {
        f"{_MC_NS} AlternateContent",
        f"{_W_NS} sdt",
        f"{_W_NS} ins",
        f"{_W_NS} del",
        f"{_W_NS} moveFrom",
        f"{_W_NS} moveTo",
        f"{_W_NS} fldSimple",
    }
)
_TARGET_PREFIX = f"{_MAIN}#t"
_MAXIMUM_TARGET_CHARACTERS = 64
_QUOTES = frozenset(b"\"'")
_TAG_CLOSE = ord(">")
_SLASH = ord("/")


class DocxEditError(ValueError):
    """Content-free, stable rejection naming only the affected target identities."""

    def __init__(self, code: str, targets: tuple[str, ...] = ()) -> None:
        super().__init__(f"DOCX edit {code.replace('_', ' ')}")
        self.code = code
        self.targets = targets


@dataclass(frozen=True, slots=True)
class DocxTextTarget:
    """One editable text node with a stable identity inside its exact package."""

    target: str
    paragraph: int
    in_table: bool
    text: str


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


def operations_record(operations: tuple[ReplaceText, ...]) -> str:
    """Return the canonical frozen operation record stored with a revision."""

    return json.dumps(
        {
            "engine_version": DOCX_EDIT_ENGINE_VERSION,
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
    roots: dict[str, ElementTree.Element], payloads: dict[str, bytes]
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
                raise DocxEditError("external_relationship")
            source = package_validation._relationship_source(name).as_posix()
            identifiers = external.setdefault(source, set())
            if identifier in identifiers:
                raise DocxEditError("invalid_package")
            identifiers.add(identifier)
            clone.remove(node)
        filtered[name] = clone
    internal = package_validation._inspect_relationships(
        filtered, {package_validation._part_key(name) for name in payloads}
    )
    merged = dict(internal)
    for source, identifiers in external.items():
        if identifiers & internal.get(source, frozenset()):
            raise DocxEditError("invalid_package")
        merged[source] = internal.get(source, frozenset()) | frozenset(identifiers)
    package_validation._inspect_relationship_references(roots, merged)


def _admit(content: bytes, limits: TypedDocxLimits) -> dict[str, bytes]:
    """Reuse the bounded Word archive preflight, reads and OPC checks."""

    if type(content) is not bytes or not content:
        raise DocxEditError("invalid_package")
    if len(content) > limits.max_archive_bytes:
        raise DocxEditError("limit_exceeded")
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
        if not payloads.keys() >= _REQUIRED:
            raise DocxEditError("invalid_package")
        if any(
            any(marker in name.casefold() for marker in _UNSAFE_PART_MARKERS)
            for name in payloads
        ):
            raise DocxEditError("active_content")
        package_validation._inspect_active_parts(payloads)
        roots = {
            name: package_validation._parse_xml(payload, configured)
            for name, payload in payloads.items()
            if name == "[Content_Types].xml" or name.endswith((".xml", ".rels"))
        }
        if roots[_MAIN].tag != f"{{{_W_NS}}}document":
            raise DocxEditError("invalid_package")
        package_validation._inspect_content_types(roots["[Content_Types].xml"])
        for name, root in roots.items():
            if name.endswith(".rels") and root.tag != f"{{{_REL_NS}}}Relationships":
                raise DocxEditError("invalid_package")
        _inspect_relationships(roots, payloads)
    except DocxEditError:
        raise
    except TemplateValidationError as exc:
        raise DocxEditError(exc.code.value) from exc
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise DocxEditError("invalid_package") from exc
    return payloads


def _start_tag_end(payload: bytes, start: int) -> int:
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
    raise DocxEditError("invalid_package")


def _text_nodes(payload: bytes) -> tuple[_TextNode, ...]:  # noqa: PLR0915 - one streaming pass
    """Locate every ``w:t`` node with exact byte spans and edit eligibility."""

    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise DocxEditError("unsupported_encoding")
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[str] = []
    nodes: list[_TextNode] = []
    current: _PendingText | None = None
    paragraph = -1
    fields = 0

    def reject(*_arguments: object) -> None:
        raise DocxEditError("invalid_package")

    def declaration(_version: str, encoding: str | None, _standalone: int) -> None:
        if encoding is not None and encoding.casefold().replace("-", "") != "utf8":
            raise DocxEditError("unsupported_encoding")

    def nested(*_arguments: object) -> None:
        # A text node holds only character data; any markup inside it is not
        # editable text and would be silently dropped by a replacement.
        if current is not None:
            current.eligible = False

    def start(name: str, attributes: dict[str, str]) -> None:
        nonlocal current, paragraph, fields
        if current is not None:
            current.eligible = False
        elif name == _PARAGRAPH:
            paragraph += 1
        elif name == _FIELD_CHARACTER:
            kind = attributes.get(_FIELD_TYPE)
            if kind == "begin":
                fields += 1
            elif kind == "end":
                fields = max(fields - 1, 0)
        elif name == _TEXT:
            begin = parser.CurrentByteIndex
            close = _start_tag_end(payload, begin)
            self_closing = payload[close - 1] == _SLASH
            current = _PendingText(
                start=begin,
                content_start=close + 1,
                self_closing=self_closing,
                eligible=(
                    not self_closing
                    and bool(stack)
                    and stack[-1] == _RUN
                    and fields == 0
                    and not _EXCLUDED_ANCESTORS.intersection(stack)
                    and set(attributes) <= {_XML_SPACE}
                    and attributes.get(_XML_SPACE, "preserve") == "preserve"
                ),
                preserves_space=_XML_SPACE in attributes,
                in_table=_TABLE in stack,
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
        if name != _TEXT or current is None or len(stack) != current.depth:
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
        raise DocxEditError("invalid_package") from exc
    return tuple(nodes)


def _target(ordinal: int) -> str:
    return f"{_TARGET_PREFIX}{ordinal}"


def _ordinal(target: str) -> int | None:
    if (
        type(target) is not str
        or len(target) > _MAXIMUM_TARGET_CHARACTERS
        or not target.startswith(_TARGET_PREFIX)
    ):
        return None
    digits = target.removeprefix(_TARGET_PREFIX)
    if (
        not digits.isascii()
        or not digits.isdigit()
        or (len(digits) > 1 and digits.startswith("0"))
    ):
        return None
    return int(digits)


def inspect_docx_text(
    content: bytes, limits: TypedDocxLimits
) -> tuple[DocxTextTarget, ...]:
    """Admit an untrusted DOCX copy and list its editable body text targets."""

    payloads = _admit(content, limits)
    return tuple(
        DocxTextTarget(_target(node.ordinal), node.paragraph, node.in_table, node.text)
        for node in _text_nodes(payloads[_MAIN])
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


def _replacement(payload: bytes, node: _TextNode, text: str) -> bytes:
    start_tag = payload[node.start : node.content_start]
    if not node.preserves_space and text != text.strip(" "):
        start_tag = start_tag[:-1] + b' xml:space="preserve">'
    return (
        start_tag + escape(text).encode("utf-8") + payload[node.content_end : node.end]
    )


def _write_package(source: bytes, document_xml: bytes) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in original.infolist():
            payload = document_xml if info.filename == _MAIN else original.read(info)
            target.writestr(info, payload)
    return output.getvalue()


def apply_docx_text_edits(
    content: bytes, operations: tuple[ReplaceText, ...], limits: TypedDocxLimits
) -> bytes:
    """Return a new DOCX in which only the requested text nodes changed."""

    if not operations:
        raise DocxEditError("no_operations")
    payloads = _admit(content, limits)
    document = payloads[_MAIN]
    nodes = _text_nodes(document)
    selected: dict[int, ReplaceText] = {}
    for operation in operations:
        ordinal = _ordinal(operation.target)
        if ordinal is None or ordinal >= len(nodes) or not nodes[ordinal].eligible:
            raise DocxEditError(
                "unsupported_target",
                (str(operation.target)[:_MAXIMUM_TARGET_CHARACTERS],),
            )
        if ordinal in selected:
            raise DocxEditError("duplicate_target", (operation.target,))
        if not _valid_text(operation.text, limits) or not _valid_text(
            operation.expected_text, limits
        ):
            raise DocxEditError("invalid_text", (operation.target,))
        if nodes[ordinal].text != operation.expected_text:
            raise DocxEditError("target_changed", (operation.target,))
        selected[ordinal] = operation
    segments: list[bytes] = []
    cursor = 0
    for ordinal in sorted(selected):
        node = nodes[ordinal]
        segments += (
            document[cursor : node.start],
            _replacement(document, node, selected[ordinal].text),
        )
        cursor = node.end
    segments.append(document[cursor:])
    revised = b"".join(segments)
    if len(revised) > limits.max_member_bytes:
        raise DocxEditError("limit_exceeded")
    output = _write_package(content, revised)
    if len(output) > limits.max_archive_bytes:
        raise DocxEditError("limit_exceeded")
    _verify(payloads, nodes, selected, output, limits)
    return output


def _verify(
    payloads: dict[str, bytes],
    nodes: tuple[_TextNode, ...],
    selected: dict[int, ReplaceText],
    output: bytes,
    limits: TypedDocxLimits,
) -> None:
    """Re-admit the copy and prove that only the requested text changed."""

    revised = _admit(output, limits)
    if revised.keys() != payloads.keys() or any(
        revised[name] != payload for name, payload in payloads.items() if name != _MAIN
    ):
        raise DocxEditError("package_changed")
    after = _text_nodes(revised[_MAIN])
    if len(after) != len(nodes):
        raise DocxEditError("package_changed")
    for before, current in zip(nodes, after, strict=True):
        expected = (
            selected[before.ordinal].text if before.ordinal in selected else before.text
        )
        if (
            current.text != expected
            or current.eligible != before.eligible
            or current.paragraph != before.paragraph
            or current.in_table != before.in_table
        ):
            raise DocxEditError("package_changed")
