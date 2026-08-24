"""Deterministic DOCX/OpenXML inspection helpers for golden tests."""

from __future__ import annotations

import hashlib
import io
import stat
import unicodedata
import zipfile
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree

from tests.golden.limits import ArchiveLimits

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RELATIONSHIP_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_PART_SUFFIXES = (".xml", ".rels")
READ_CHUNK_BYTES = 64 * 1024


class OpenXmlError(ValueError):
    """Raised when an input is not a deterministic, inspectable DOCX archive."""


@dataclass(frozen=True)
class PageSize:
    width_twips: int
    height_twips: int


@dataclass(frozen=True, order=True)
class Relationship:
    part: str
    relationship_id: str
    relationship_type: str
    target: str
    target_mode: str | None


@dataclass(frozen=True)
class DocxSnapshot:
    parts: tuple[str, ...]
    xml_parts: Mapping[str, str]
    binary_sha256: Mapping[str, str]
    document_text: tuple[str, ...]
    relationships: tuple[Relationship, ...]
    style_ids: tuple[str, ...]
    page_sizes: tuple[PageSize, ...]


@dataclass(frozen=True)
class DocxComparison:
    missing_parts: tuple[str, ...]
    unexpected_parts: tuple[str, ...]
    changed_xml_parts: tuple[str, ...]
    changed_binary_parts: tuple[str, ...]
    text_matches: bool
    relationships_match: bool
    style_ids_match: bool
    page_sizes_match: bool

    @property
    def matches(self) -> bool:
        return not any(
            (
                self.missing_parts,
                self.unexpected_parts,
                self.changed_xml_parts,
                self.changed_binary_parts,
            )
        ) and all(
            (
                self.text_matches,
                self.relationships_match,
                self.style_ids_match,
                self.page_sizes_match,
            )
        )


def _part_key(name: str) -> str:
    if not name or "\0" in name or "\\" in name or name.startswith(("/", "//")):
        raise OpenXmlError("DOCX contains an unsafe part name")
    path = PurePosixPath(name)
    if ".." in path.parts or ":" in path.parts[0] or path.as_posix() != name:
        raise OpenXmlError("DOCX contains an unsafe or non-normalized part name")
    return unicodedata.normalize("NFC", name).casefold()


def _canonical_xml(data: bytes, part_name: str) -> tuple[str, ElementTree.Element]:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise OpenXmlError(f"DTD and entity declarations are forbidden: {part_name}")
    try:
        text = data.decode("utf-8")
        # ElementTree does not dereference external entities; declarations are rejected above.
        root = ElementTree.fromstring(text)  # noqa: S314
        canonical = ElementTree.canonicalize(
            text, strip_text=False, rewrite_prefixes=True
        )
    except (UnicodeDecodeError, ElementTree.ParseError, ValueError) as error:
        raise OpenXmlError(f"Invalid XML part: {part_name}") from error
    return canonical, root


def _preflight_members(
    members: list[zipfile.ZipInfo], limits: ArchiveLimits
) -> list[str]:
    if len(members) > limits.max_entries:
        raise OpenXmlError("DOCX exceeds the archive entry cap")
    keys: set[str] = set()
    total_uncompressed = 0
    for member in members:
        inspected_name = (
            member.filename.removesuffix("/") if member.is_dir() else member.filename
        )
        key = _part_key(inspected_name)
        if key in keys:
            raise OpenXmlError("DOCX contains duplicate normalized part names")
        keys.add(key)
        if member.flag_bits & 1:
            raise OpenXmlError("DOCX contains an encrypted part")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise OpenXmlError("DOCX contains a symbolic-link part")
        if member.file_size > limits.max_member_uncompressed_bytes:
            raise OpenXmlError("DOCX part exceeds the uncompressed-size cap")
        total_uncompressed += member.file_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            raise OpenXmlError("DOCX exceeds the total uncompressed-size cap")
        compressed_size = max(member.compress_size, 1)
        if member.file_size / compressed_size > limits.max_compression_ratio:
            raise OpenXmlError("DOCX exceeds the compression-ratio cap")
    return [member.filename for member in members if not member.is_dir()]


def _read_member_bounded(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    limits: ArchiveLimits,
    total_uncompressed: int,
) -> tuple[bytes, int]:
    """Read one part in bounded chunks and verify actual size and archive integrity."""

    payload = bytearray()
    actual_size = 0
    try:
        with archive.open(member, "r") as source:
            while True:
                read_size = min(
                    READ_CHUNK_BYTES,
                    limits.max_member_uncompressed_bytes - actual_size + 1,
                    limits.max_total_uncompressed_bytes
                    - total_uncompressed
                    - actual_size
                    + 1,
                    member.file_size - actual_size + 1,
                )
                chunk = source.read(max(read_size, 1))
                if not chunk:
                    break
                actual_size += len(chunk)
                if actual_size > member.file_size:
                    raise OpenXmlError(
                        f"DOCX part exceeds its declared size: {member.filename}"
                    )
                if actual_size > limits.max_member_uncompressed_bytes:
                    raise OpenXmlError(
                        f"DOCX part exceeds the uncompressed-size cap: {member.filename}"
                    )
                if (
                    total_uncompressed + actual_size
                    > limits.max_total_uncompressed_bytes
                ):
                    raise OpenXmlError("DOCX exceeds the total uncompressed-size cap")
                payload.extend(chunk)
    except OpenXmlError:
        raise
    except (
        EOFError,
        NotImplementedError,
        RuntimeError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        raise OpenXmlError(
            f"DOCX part failed integrity validation: {member.filename}"
        ) from error
    if actual_size != member.file_size:
        raise OpenXmlError(
            f"DOCX part size does not match its declaration: {member.filename}"
        )
    return bytes(payload), total_uncompressed + actual_size


def inspect_docx(data: bytes, limits: ArchiveLimits) -> DocxSnapshot:
    """Inspect DOCX bytes without extraction or relationship dereferencing."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise OpenXmlError("DOCX is not a valid ZIP archive") from error
    with archive:
        members = archive.infolist()
        names = _preflight_members(members, limits)
        required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
        missing = sorted(required - set(names))
        if missing:
            raise OpenXmlError(f"DOCX is missing required parts: {', '.join(missing)}")
        xml_parts: dict[str, str] = {}
        roots: dict[str, ElementTree.Element] = {}
        binary_sha256: dict[str, str] = {}
        total_uncompressed = 0
        for member in sorted(
            (item for item in members if not item.is_dir()),
            key=lambda item: item.filename,
        ):
            name = member.filename
            payload, total_uncompressed = _read_member_bounded(
                archive, member, limits, total_uncompressed
            )
            if name.endswith(XML_PART_SUFFIXES) or name == "[Content_Types].xml":
                xml_parts[name], roots[name] = _canonical_xml(payload, name)
            elif not name.endswith("/"):
                binary_sha256[name] = hashlib.sha256(payload).hexdigest()

    document = roots["word/document.xml"]
    try:
        page_sizes = tuple(
            PageSize(
                int(node.attrib[f"{{{WORD_NAMESPACE}}}w"]),
                int(node.attrib[f"{{{WORD_NAMESPACE}}}h"]),
            )
            for node in document.iter(f"{{{WORD_NAMESPACE}}}pgSz")
        )
    except (KeyError, ValueError) as error:
        raise OpenXmlError("word/document.xml contains an invalid page size") from error
    styles = roots.get("word/styles.xml")
    style_ids = (
        ()
        if styles is None
        else tuple(
            sorted(
                node.attrib[f"{{{WORD_NAMESPACE}}}styleId"]
                for node in styles.iter(f"{{{WORD_NAMESPACE}}}style")
                if f"{{{WORD_NAMESPACE}}}styleId" in node.attrib
            )
        )
    )
    relationships: list[Relationship] = []
    for part, root in roots.items():
        if not part.endswith(".rels"):
            continue
        for node in root.iter(f"{{{RELATIONSHIP_NAMESPACE}}}Relationship"):
            try:
                relationships.append(
                    Relationship(
                        part,
                        node.attrib["Id"],
                        node.attrib["Type"],
                        node.attrib["Target"],
                        node.attrib.get("TargetMode"),
                    )
                )
            except KeyError as error:
                raise OpenXmlError(
                    f"Relationship is missing required metadata: {part}"
                ) from error
    return DocxSnapshot(
        parts=tuple(sorted(names)),
        xml_parts=xml_parts,
        binary_sha256=binary_sha256,
        document_text=tuple(
            node.text or "" for node in document.iter(f"{{{WORD_NAMESPACE}}}t")
        ),
        relationships=tuple(sorted(relationships)),
        style_ids=style_ids,
        page_sizes=page_sizes,
    )


def compare_docx(
    expected: DocxSnapshot,
    actual: DocxSnapshot,
    *,
    ignored_parts: frozenset[str],
) -> DocxComparison:
    """Compare normalized OpenXML observations with an explicit volatile-part allowlist."""

    for part in ignored_parts:
        _part_key(part)
    expected_parts, actual_parts = (
        set(expected.parts) - ignored_parts,
        set(actual.parts) - ignored_parts,
    )
    shared_xml = (set(expected.xml_parts) & set(actual.xml_parts)) - ignored_parts
    shared_binary = (
        set(expected.binary_sha256) & set(actual.binary_sha256)
    ) - ignored_parts
    relationships_expected = tuple(
        item for item in expected.relationships if item.part not in ignored_parts
    )
    relationships_actual = tuple(
        item for item in actual.relationships if item.part not in ignored_parts
    )
    return DocxComparison(
        missing_parts=tuple(sorted(expected_parts - actual_parts)),
        unexpected_parts=tuple(sorted(actual_parts - expected_parts)),
        changed_xml_parts=tuple(
            sorted(
                part
                for part in shared_xml
                if expected.xml_parts[part] != actual.xml_parts[part]
            )
        ),
        changed_binary_parts=tuple(
            sorted(
                part
                for part in shared_binary
                if expected.binary_sha256[part] != actual.binary_sha256[part]
            )
        ),
        text_matches=expected.document_text == actual.document_text,
        relationships_match=relationships_expected == relationships_actual,
        style_ids_match=expected.style_ids == actual.style_ids,
        page_sizes_match=expected.page_sizes == actual.page_sizes,
    )
