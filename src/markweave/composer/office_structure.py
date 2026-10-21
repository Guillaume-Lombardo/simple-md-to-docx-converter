"""Bounded structural Office edits: paragraph styles, slide order, and images.

These T92 edit families reuse the text-edit admission of an untrusted private
copy. Each operation names a stable target and the value the reviewer saw, then
splices exact bytes: a ``w:pStyle`` declaration, the order of ``p:sldId``
entries, or one image part re-encoded from a validated upload. The rewritten
copy is admitted again and every other part, byte, text node, and target is
proven unchanged before it can be published.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.parsers import expat
from xml.sax.saxutils import quoteattr

from PIL import Image

from markweave.composer.docx_edits import DOCX_PACKAGE
from markweave.composer.office_edits import (
    MAXIMUM_TARGET_CHARACTERS,
    OFFICE_EDIT_ENGINE_VERSION,
    OfficeEditError,
    PackageKind,
    _admit,
    _Admitted,
    _package_limits,
    _start_tag_end,
    _text_nodes,
    _write_package,
    internal_target,
)
from markweave.composer.pptx_edits import PPTX_PACKAGE
from markweave.composer.typed_templates import TypedDocxLimits
from markweave.conversion.errors import ConversionError
from markweave.conversion.images import ImageLimits, normalize_image
from markweave.templates import validation as package_validation

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_IMAGE_REL = f"{_R_NS}/image"
_DOCUMENT = "word/document.xml"
_STYLES = "word/styles.xml"
_PRESENTATION = "ppt/presentation.xml"
_PARAGRAPH_SEPARATOR = "#p"
_IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG"}
_JPEG_QUALITY = 90
_MAXIMUM_STYLE_ID_CHARACTERS = 253
# Paragraphs under these elements belong to another mechanism (tracked
# changes, content controls, alternate renderings, or text boxes inside
# drawings), so their style is never offered as a target.
_EXCLUDED_PARAGRAPH_ANCESTORS = frozenset(
    {
        f"{_MC_NS} AlternateContent",
        f"{_W_NS} sdt",
        f"{_W_NS} ins",
        f"{_W_NS} del",
        f"{_W_NS} moveFrom",
        f"{_W_NS} moveTo",
        f"{_W_NS} txbxContent",
    }
)


@dataclass(frozen=True, slots=True)
class SetParagraphStyle:
    """Apply one existing paragraph style to a paragraph that still has ``expected``."""

    target: str
    expected_style: str | None
    style: str

    def as_record(self) -> dict[str, object]:
        return {
            "op": "set_paragraph_style",
            "target": self.target,
            "expected_style": self.expected_style,
            "style": self.style,
        }


@dataclass(frozen=True, slots=True)
class MoveSlide:
    """Move the slide with ``slide_id`` from ``expected_position`` to ``position``."""

    slide_id: int
    expected_position: int
    position: int

    def as_record(self) -> dict[str, object]:
        return {
            "op": "move_slide",
            "slide_id": self.slide_id,
            "expected_position": self.expected_position,
            "position": self.position,
        }


@dataclass(frozen=True, slots=True)
class ReplaceImage:
    """Replace one image part whose bytes still hash to ``expected_sha256``.

    ``content`` must come from :func:`prepare_image`, which re-encodes a
    validated upload into the target's own format without metadata.
    """

    target: str
    expected_sha256: str
    content: bytes

    def as_record(self) -> dict[str, object]:
        return {
            "op": "replace_image",
            "target": self.target,
            "expected_sha256": self.expected_sha256,
            "sha256": hashlib.sha256(self.content).hexdigest(),
            "size": len(self.content),
        }


StructureOperation = SetParagraphStyle | MoveSlide | ReplaceImage


@dataclass(frozen=True, slots=True)
class ParagraphTarget:
    target: str
    paragraph: int
    in_table: bool
    style: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ParagraphStyle:
    style_id: str
    name: str


@dataclass(frozen=True, slots=True)
class SlideTarget:
    slide_id: int
    position: int
    part: str
    title: str


@dataclass(frozen=True, slots=True)
class ImageTarget:
    target: str
    media_type: str
    sha256: str
    size: int
    used_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OfficeStructure:
    """Every structural target a reviewer may change, with its current value."""

    paragraphs: tuple[ParagraphTarget, ...]
    paragraph_styles: tuple[ParagraphStyle, ...]
    slides: tuple[SlideTarget, ...]
    images: tuple[ImageTarget, ...]


class _Bindings:
    """The namespace prefixes in scope while a part is parsed."""

    def __init__(self) -> None:
        self._uris: dict[str, list[str]] = {}

    def declare(self, prefix: str | None, uri: str | None) -> None:
        self._uris.setdefault(prefix or "", []).append(uri or "")

    def undeclare(self, prefix: str | None) -> None:
        self._uris[prefix or ""].pop()

    def wordprocessing_prefix(self) -> bytes | None:
        """Return a non-empty in-scope prefix (with colon) bound to WordprocessingML.

        Attributes need such a prefix because a default namespace never
        applies to them; ``None`` means the markup inserted there must declare
        its own.
        """

        bound = sorted(
            prefix
            for prefix, uris in self._uris.items()
            if prefix and uris and uris[-1] == _W_NS
        )
        if not bound:
            return None
        return ("w" if "w" in bound else bound[0]).encode("utf-8") + b":"


@dataclass(slots=True)
class _Paragraph:
    ordinal: int
    start: int
    start_close: int
    self_closing: bool
    in_table: bool
    eligible: bool
    depth: int
    scope: bytes | None
    style: str | None = None
    properties: tuple[int, int, bool, bytes | None] | None = None
    style_span: tuple[int, int] | None = None
    end: int = 0


@dataclass(frozen=True, slots=True)
class _SlideEntry:
    slide_id: int
    relationship: str
    start: int
    end: int


def structure_record(operations: tuple[StructureOperation, ...]) -> str:
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


def _prefix(payload: bytes, start: int) -> bytes:
    """Return the namespace prefix (with colon) of the element starting at ``start``."""

    end = start + 1
    while end < len(payload) and payload[end : end + 1] not in {
        b" ",
        b"\t",
        b"\r",
        b"\n",
        b">",
        b"/",
    }:
        end += 1
    name = payload[start + 1 : end]
    return name.split(b":")[0] + b":" if b":" in name else b""


def _paragraphs(payload: bytes, error: type[OfficeEditError]) -> tuple[_Paragraph, ...]:
    """Locate every ``w:p`` with exact byte spans of its style declaration."""

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[str] = []
    found: list[_Paragraph] = []
    open_paragraphs: list[_Paragraph] = []
    bindings = _Bindings()

    def reject(*_arguments: object) -> None:
        raise error("invalid_package")

    def start(name: str, attributes: dict[str, str]) -> None:
        begin = parser.CurrentByteIndex
        if name == f"{_W_NS} p":
            close = _start_tag_end(payload, begin, error)
            paragraph = _Paragraph(
                ordinal=len(found),
                start=begin,
                start_close=close,
                self_closing=payload[close - 1] == ord("/"),
                in_table=f"{_W_NS} tbl" in stack,
                eligible=not _EXCLUDED_PARAGRAPH_ANCESTORS.intersection(stack)
                and not open_paragraphs,
                depth=len(stack),
                scope=bindings.wordprocessing_prefix(),
            )
            found.append(paragraph)
            open_paragraphs.append(paragraph)
        elif open_paragraphs:
            current = open_paragraphs[-1]
            if name == f"{_W_NS} pPr" and len(stack) == current.depth + 1:
                close = _start_tag_end(payload, begin, error)
                current.properties = (
                    begin,
                    close,
                    payload[close - 1] == ord("/"),
                    bindings.wordprocessing_prefix(),
                )
            elif (
                name == f"{_W_NS} pStyle"
                and current.properties is not None
                and len(stack) == current.depth + 2
            ):
                close = _start_tag_end(payload, begin, error)
                value = attributes.get(f"{_W_NS} val")
                if (
                    payload[close - 1] != ord("/")
                    or set(attributes) != {f"{_W_NS} val"}
                    or not value
                ):
                    current.eligible = False
                current.style = value
                current.style_span = (begin, close + 1)
            elif name == f"{_W_NS} pPrChange":
                # A tracked formatting change owns this paragraph's properties.
                current.eligible = False
        stack.append(name)

    def end(name: str) -> None:
        stack.pop()
        if name == f"{_W_NS} p" and open_paragraphs:
            current = open_paragraphs.pop()
            current.end = (
                current.start_close + 1
                if current.self_closing
                else payload.index(b">", parser.CurrentByteIndex) + 1
            )

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.StartNamespaceDeclHandler = bindings.declare
    parser.EndNamespaceDeclHandler = bindings.undeclare
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise error("invalid_package") from exc
    return tuple(found)


def _paragraph_styles(
    admitted: _Admitted, limits: TypedDocxLimits
) -> tuple[ParagraphStyle, ...]:
    payload = admitted.payloads.get(_STYLES)
    if payload is None:
        return ()
    root = package_validation._parse_xml(payload, _package_limits(limits))
    styles: list[ParagraphStyle] = []
    for node in root.iter(f"{{{_W_NS}}}style"):
        style_id = node.get(f"{{{_W_NS}}}styleId")
        if node.get(f"{{{_W_NS}}}type") != "paragraph" or not style_id:
            continue
        name = node.find(f"{{{_W_NS}}}name")
        styles.append(
            ParagraphStyle(
                style_id,
                style_id if name is None else name.get(f"{{{_W_NS}}}val", style_id),
            )
        )
    return tuple(styles)


def _paragraph_texts(payload: bytes, kind: PackageKind) -> dict[int, str]:
    texts: dict[int, list[str]] = {}
    for node in _text_nodes(payload, kind.grammar, kind.error):
        texts.setdefault(node.paragraph, []).append(node.text)
    return {paragraph: "".join(parts) for paragraph, parts in texts.items()}


def _slide_entries(payload: bytes) -> tuple[_SlideEntry, ...]:
    """Locate each ``p:sldId`` of ``p:sldIdLst`` with its exact byte span."""

    error = PPTX_PACKAGE.error
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    stack: list[str] = []
    entries: list[_SlideEntry] = []
    pending: list[tuple[int, int, int, str]] = []

    def reject(*_arguments: object) -> None:
        raise error("invalid_package")

    def start(name: str, attributes: dict[str, str]) -> None:
        if name == f"{_P_NS} sldId" and stack[-1:] == [f"{_P_NS} sldIdLst"]:
            identifier = attributes.get("id", "")
            if not identifier.isascii() or not identifier.isdigit():
                raise error("invalid_package")
            begin = parser.CurrentByteIndex
            close = _start_tag_end(payload, begin, error)
            pending.append(
                (
                    begin,
                    close + 1 if payload[close - 1] == ord("/") else -1,
                    int(identifier),
                    attributes.get(f"{_R_NS} id", ""),
                )
            )
        stack.append(name)

    def end(name: str) -> None:
        stack.pop()
        if name == f"{_P_NS} sldId" and stack[-1:] == [f"{_P_NS} sldIdLst"]:
            begin, finish, identifier, relationship = pending.pop()
            if finish < 0:
                finish = payload.index(b">", parser.CurrentByteIndex) + 1
            entries.append(_SlideEntry(identifier, relationship, begin, finish))

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(payload, True)
    except expat.ExpatError as exc:
        raise PPTX_PACKAGE.error("invalid_package") from exc
    return tuple(entries)


def _content_types(admitted: _Admitted, limits: TypedDocxLimits) -> dict[str, str]:
    root = package_validation._parse_xml(
        admitted.payloads["[Content_Types].xml"], _package_limits(limits)
    )
    defaults = {
        node.get("Extension", "").casefold(): node.get("ContentType", "")
        for node in root.iter(f"{{{_CT_NS}}}Default")
    }
    overrides = {
        node.get("PartName", "").lstrip("/"): node.get("ContentType", "")
        for node in root.iter(f"{{{_CT_NS}}}Override")
    }
    return {
        name: overrides.get(
            name, defaults.get(PurePosixPath(name).suffix.lstrip(".").casefold(), "")
        )
        for name in admitted.payloads
    }


def _images(
    admitted: _Admitted, limits: TypedDocxLimits, kind: PackageKind
) -> tuple[ImageTarget, ...]:
    sources = (
        (_DOCUMENT,)
        if kind is DOCX_PACKAGE
        else tuple(part.name for part in admitted.parts if not part.notes)
    )
    media = _content_types(admitted, limits)
    users: dict[str, list[str]] = {}
    configured = _package_limits(limits)
    for source in sources:
        folder, _, filename = source.rpartition("/")
        relationships = admitted.payloads.get(f"{folder}/_rels/{filename}.rels")
        if relationships is None:
            continue
        root = package_validation._parse_xml(relationships, configured)
        for node in root.iter(f"{{{_REL_NS}}}Relationship"):
            if (
                node.get("Type") != _IMAGE_REL
                or node.get("TargetMode", "").casefold() == "external"
            ):
                continue
            part = internal_target(source, node.get("Target", ""))
            if media.get(part) in _IMAGE_FORMATS and source not in users.get(part, []):
                users.setdefault(part, []).append(source)
    return tuple(
        ImageTarget(
            part,
            media[part],
            hashlib.sha256(admitted.payloads[part]).hexdigest(),
            len(admitted.payloads[part]),
            tuple(used_by),
        )
        for part, used_by in sorted(users.items())
    )


def _slides(admitted: _Admitted) -> tuple[SlideTarget, ...]:
    entries = _slide_entries(admitted.payloads[_PRESENTATION])
    slide_parts = [part for part in admitted.parts if not part.notes]
    if len(entries) != len(slide_parts):
        raise PPTX_PACKAGE.error("invalid_package")
    slides: list[SlideTarget] = []
    for position, (entry, part) in enumerate(
        zip(entries, slide_parts, strict=True), start=1
    ):
        texts = _paragraph_texts(admitted.payloads[part.name], PPTX_PACKAGE)
        title = next((text for _, text in sorted(texts.items()) if text.strip()), "")
        slides.append(SlideTarget(entry.slide_id, position, part.name, title))
    return tuple(slides)


def inspect_structure(
    content: bytes, limits: TypedDocxLimits, kind: PackageKind
) -> OfficeStructure:
    """Admit an untrusted Office copy and list its structural targets."""

    admitted = _admit(content, limits, kind)
    if kind is DOCX_PACKAGE:
        payload = admitted.payloads[_DOCUMENT]
        texts = _paragraph_texts(payload, kind)
        paragraphs = tuple(
            ParagraphTarget(
                f"{_DOCUMENT}{_PARAGRAPH_SEPARATOR}{item.ordinal}",
                item.ordinal,
                item.in_table,
                item.style,
                texts.get(item.ordinal, ""),
            )
            for item in _paragraphs(payload, kind.error)
            if item.eligible
        )
        return OfficeStructure(
            paragraphs,
            _paragraph_styles(admitted, limits),
            (),
            _images(admitted, limits, kind),
        )
    return OfficeStructure((), (), _slides(admitted), _images(admitted, limits, kind))


def prepare_image(
    source: bytes, filename: str, media_type: str, limits: ImageLimits
) -> bytes:
    """Validate an uploaded raster and re-encode it, metadata-free, as ``media_type``.

    The caller must have scanned ``source`` before this parse. SVG and animated
    images are rejected, so a replacement can never carry active content.
    """

    suffix = PurePosixPath(filename).suffix.casefold()
    if media_type not in _IMAGE_FORMATS or suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError("Replacement image type is unsupported")
    try:
        normalized = normalize_image(PurePosixPath(f"upload{suffix}"), source, limits)
    except ConversionError as exc:
        raise ValueError("Replacement image is invalid") from exc
    if media_type == "image/png":
        return normalized
    with Image.open(io.BytesIO(normalized)) as image:
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=_JPEG_QUALITY)
    return output.getvalue()


def _target_ordinal(target: object, part: str, separator: str) -> int | None:
    if type(target) is not str or len(target) > MAXIMUM_TARGET_CHARACTERS:
        return None
    name, found, digits = target.rpartition(separator)
    if (
        name != part
        or not found
        or not digits.isascii()
        or not digits.isdigit()
        or (len(digits) > 1 and digits.startswith("0"))
    ):
        return None
    return int(digits)


def _names(prefix: bytes | None) -> tuple[bytes, bytes]:
    """Return the prefix for new markup and any declaration it needs.

    Without a non-empty prefix bound to WordprocessingML (a default-namespace
    part), the new element declares one so that its ``val`` attribute is
    qualified; that declaration only covers the inserted markup.
    """

    if prefix is not None:
        return prefix, b""
    return b"w:", b' xmlns:w="' + _W_NS.encode("utf-8") + b'"'


def _style_declaration(scope: bytes | None, style: str) -> bytes:
    prefix, declaration = _names(scope)
    value = quoteattr(style).encode("utf-8")
    return (
        b"<"
        + prefix
        + b"pStyle"
        + declaration
        + b" "
        + prefix
        + b"val="
        + value
        + b"/>"
    )


def _style_splice(
    payload: bytes, paragraph: _Paragraph, style: str
) -> tuple[int, int, bytes]:
    """Return the span to replace and the bytes that set ``style`` exactly."""

    if paragraph.properties is not None:
        begin, close, self_closing, scope = paragraph.properties
        declaration = _style_declaration(scope, style)
        if paragraph.style_span is not None:
            return (*paragraph.style_span, declaration)
        if self_closing:
            opening = payload[begin:close].rstrip(b"/") + b">"
            closing = b"</" + _prefix(payload, begin) + b"pPr>"
            return begin, close + 1, opening + declaration + closing
        return close + 1, close + 1, declaration
    prefix, namespace = _names(paragraph.scope)
    properties = (
        b"<"
        + prefix
        + b"pPr"
        + namespace
        + b">"
        + _style_declaration(prefix, style)
        + b"</"
        + prefix
        + b"pPr>"
    )
    if paragraph.self_closing:
        opening = payload[paragraph.start : paragraph.start_close].rstrip(b"/") + b">"
        return (
            paragraph.start,
            paragraph.start_close + 1,
            opening + properties + b"</" + _prefix(payload, paragraph.start) + b"p>",
        )
    return paragraph.start_close + 1, paragraph.start_close + 1, properties


def _apply_styles(
    admitted: _Admitted,
    operations: tuple[SetParagraphStyle, ...],
    limits: TypedDocxLimits,
) -> dict[str, bytes]:
    error = DOCX_PACKAGE.error
    payload = admitted.payloads[_DOCUMENT]
    paragraphs = _paragraphs(payload, error)
    known = {item.style_id for item in _paragraph_styles(admitted, limits)}
    changes: dict[int, SetParagraphStyle] = {}
    for operation in operations:
        ordinal = _target_ordinal(operation.target, _DOCUMENT, _PARAGRAPH_SEPARATOR)
        name = str(operation.target)[:MAXIMUM_TARGET_CHARACTERS]
        if (
            ordinal is None
            or ordinal >= len(paragraphs)
            or not paragraphs[ordinal].eligible
        ):
            raise error("unsupported_target", (name,))
        if ordinal in changes:
            raise error("duplicate_target", (name,))
        if (
            type(operation.style) is not str
            or len(operation.style) > _MAXIMUM_STYLE_ID_CHARACTERS
            or operation.style not in known
        ):
            raise error("unknown_style", (name,))
        if paragraphs[ordinal].style != operation.expected_style:
            raise error("target_changed", (name,))
        changes[ordinal] = operation
    segments: list[bytes] = []
    cursor = 0
    for ordinal in sorted(changes):
        begin, finish, replacement = _style_splice(
            payload, paragraphs[ordinal], changes[ordinal].style
        )
        segments += (payload[cursor:begin], replacement)
        cursor = finish
    segments.append(payload[cursor:])
    return {_DOCUMENT: b"".join(segments)}


def _apply_slide_moves(
    admitted: _Admitted, operations: tuple[MoveSlide, ...]
) -> dict[str, bytes]:
    error = PPTX_PACKAGE.error
    payload = admitted.payloads[_PRESENTATION]
    entries = list(_slide_entries(payload))
    if not entries:
        raise error("unsupported_target")
    order = list(entries)
    moved: set[int] = set()
    for operation in operations:
        name = str(operation.slide_id)[:MAXIMUM_TARGET_CHARACTERS]
        positions = {entry.slide_id: index for index, entry in enumerate(order, 1)}
        if (
            type(operation.slide_id) is not int
            or operation.slide_id not in positions
            or operation.slide_id in moved
        ):
            raise error("unsupported_target", (name,))
        if type(operation.position) is not int or not 1 <= operation.position <= len(
            order
        ):
            raise error("invalid_position", (name,))
        if positions[operation.slide_id] != operation.expected_position:
            raise error("target_changed", (name,))
        entry = order.pop(positions[operation.slide_id] - 1)
        order.insert(operation.position - 1, entry)
        moved.add(operation.slide_id)
    segments = [payload[: entries[0].start]]
    for index, entry in enumerate(order):
        segments.append(payload[entry.start : entry.end])
        if index + 1 < len(entries):
            segments.append(payload[entries[index].end : entries[index + 1].start])
    segments.append(payload[entries[-1].end :])
    return {_PRESENTATION: b"".join(segments)}


def _apply_images(
    admitted: _Admitted,
    operations: tuple[ReplaceImage, ...],
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> dict[str, bytes]:
    images = {item.target: item for item in _images(admitted, limits, kind)}
    replacements: dict[str, bytes] = {}
    for operation in operations:
        name = str(operation.target)[:MAXIMUM_TARGET_CHARACTERS]
        image = images.get(operation.target) if type(operation.target) is str else None
        if image is None:
            raise kind.error("unsupported_target", (name,))
        if operation.target in replacements:
            raise kind.error("duplicate_target", (name,))
        if image.sha256 != operation.expected_sha256:
            raise kind.error("target_changed", (name,))
        if (
            type(operation.content) is not bytes
            or not operation.content
            or len(operation.content) > limits.max_member_bytes
        ):
            raise kind.error("invalid_image", (name,))
        try:
            with Image.open(io.BytesIO(operation.content)) as probe:
                matches = probe.format == _IMAGE_FORMATS[image.media_type]
        except OSError, SyntaxError, ValueError, Image.DecompressionBombError:
            matches = False
        if not matches:
            raise kind.error("invalid_image", (name,))
        replacements[operation.target] = operation.content
    return replacements


def apply_structure_edits(
    content: bytes,
    operations: tuple[StructureOperation, ...],
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> bytes:
    """Return a new package in which only the requested structure changed."""

    if not operations:
        raise kind.error("no_operations")
    admitted = _admit(content, limits, kind)
    styles = tuple(item for item in operations if isinstance(item, SetParagraphStyle))
    slides = tuple(item for item in operations if isinstance(item, MoveSlide))
    images = tuple(item for item in operations if isinstance(item, ReplaceImage))
    if (styles and kind is not DOCX_PACKAGE) or (slides and kind is not PPTX_PACKAGE):
        raise kind.error("unsupported_operation")
    replacements: dict[str, bytes] = {}
    if styles:
        replacements |= _apply_styles(admitted, styles, limits)
    if slides:
        replacements |= _apply_slide_moves(admitted, slides)
    if images:
        replacements |= _apply_images(admitted, images, limits, kind)
    if any(len(payload) > limits.max_member_bytes for payload in replacements.values()):
        raise kind.error("limit_exceeded")
    output = _write_package(content, replacements)
    if len(output) > limits.max_archive_bytes:
        raise kind.error("limit_exceeded")
    _verify(admitted, operations, replacements, output, limits, kind)
    return output


def _verify(  # noqa: PLR0913, PLR0917 - the complete before/after evidence
    admitted: _Admitted,
    operations: tuple[StructureOperation, ...],
    replacements: dict[str, bytes],
    output: bytes,
    limits: TypedDocxLimits,
    kind: PackageKind,
) -> None:
    """Re-admit the copy and prove that only the requested structure changed."""

    revised = _admit(output, limits, kind)
    if revised.payloads.keys() != admitted.payloads.keys() or any(
        revised.payloads[name] != payload
        for name, payload in admitted.payloads.items()
        if name not in replacements
    ):
        raise kind.error("package_changed")
    for name, payload in replacements.items():
        if revised.payloads[name] != payload:
            raise kind.error("package_changed")
    if kind is DOCX_PACKAGE and _DOCUMENT in replacements:
        _verify_styles(admitted, revised, operations)
    if kind is PPTX_PACKAGE:
        _verify_slides(admitted, revised, operations)


def _verify_styles(
    admitted: _Admitted, revised: _Admitted, operations: tuple[StructureOperation, ...]
) -> None:
    error = DOCX_PACKAGE.error
    expected = {
        _target_ordinal(item.target, _DOCUMENT, _PARAGRAPH_SEPARATOR): item.style
        for item in operations
        if isinstance(item, SetParagraphStyle)
    }
    before = _paragraphs(admitted.payloads[_DOCUMENT], error)
    after = _paragraphs(revised.payloads[_DOCUMENT], error)
    texts_before = _text_nodes(
        admitted.payloads[_DOCUMENT], DOCX_PACKAGE.grammar, error
    )
    texts_after = _text_nodes(revised.payloads[_DOCUMENT], DOCX_PACKAGE.grammar, error)
    if len(before) != len(after) or [
        (item.text, item.paragraph, item.eligible) for item in texts_before
    ] != [(item.text, item.paragraph, item.eligible) for item in texts_after]:
        raise error("package_changed")
    for old, new in zip(before, after, strict=True):
        if new.style != expected.get(old.ordinal, old.style) or (
            new.in_table,
            new.eligible,
        ) != (old.in_table, old.eligible):
            raise error("package_changed")


def _verify_slides(
    admitted: _Admitted, revised: _Admitted, operations: tuple[StructureOperation, ...]
) -> None:
    error = PPTX_PACKAGE.error
    before = list(_slide_entries(admitted.payloads[_PRESENTATION]))
    order = list(before)
    for item in operations:
        if isinstance(item, MoveSlide):
            entry = next(e for e in order if e.slide_id == item.slide_id)
            order.remove(entry)
            order.insert(item.position - 1, entry)
    after = _slide_entries(revised.payloads[_PRESENTATION])
    old_payload = admitted.payloads[_PRESENTATION]
    new_payload = revised.payloads[_PRESENTATION]
    if [
        (entry.slide_id, entry.relationship, old_payload[entry.start : entry.end])
        for entry in order
    ] != [
        (entry.slide_id, entry.relationship, new_payload[entry.start : entry.end])
        for entry in after
    ]:
        raise error("package_changed")
    slide_parts = [part.name for part in admitted.parts if not part.notes]
    part_of = {
        entry.slide_id: part for entry, part in zip(before, slide_parts, strict=True)
    }
    if [part.name for part in revised.parts if not part.notes] != [
        part_of[entry.slide_id] for entry in order
    ]:
        raise error("package_changed")
