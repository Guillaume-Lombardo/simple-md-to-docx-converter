"""Bounded, non-extracting OPC archive access for structured PPTX reading."""

from __future__ import annotations

import io
import posixpath
import stat
import zipfile
import zlib
from dataclasses import dataclass
from xml.etree import ElementTree

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.hyperlinks import _is_safe_hyperlink
from markweave.reversions.models import MAX_PPTX_XML_DEPTH, ReverseContentLimits

PACKAGE_RELATIONSHIPS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_RELATIONSHIPS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_MIN_ZIP_DIRECTORY_ENTRY_BYTES = 46
_FIRST_PRINTABLE = 32
_DELETE = 127


@dataclass(frozen=True, slots=True)
class PptxReadLimits:
    """Explicit reader bounds, derived from reverse budgets unless overridden."""

    entries: int
    member_bytes: int
    total_bytes: int
    xml_elements: int
    xml_depth: int
    xml_attributes: int

    @classmethod
    def from_content_limits(cls, limits: ReverseContentLimits) -> PptxReadLimits:
        """Keep byte defaults tied to the existing reverse operating envelope."""

        return cls(
            limits.max_pptx_archive_entries
            or max(1, limits.max_input_bytes // _MIN_ZIP_DIRECTORY_ENTRY_BYTES),
            limits.max_pptx_member_bytes or limits.max_input_bytes,
            limits.max_pptx_uncompressed_bytes
            or (
                limits.max_input_bytes
                + limits.max_total_asset_source_bytes
                + limits.max_markdown_bytes
            ),
            limits.max_pptx_xml_elements or limits.max_markdown_bytes,
            limits.max_pptx_xml_depth or MAX_PPTX_XML_DEPTH,
            limits.max_pptx_xml_attributes or limits.max_markdown_bytes,
        )


@dataclass(frozen=True, slots=True)
class Relationship:
    """One validated package-local target or safe external hyperlink."""

    kind: str
    target: str
    external: bool = False


def _safe_part(name: str) -> bool:
    return (
        bool(name)
        and not name.startswith("/")
        and not any(character in name for character in ("\\", ":", "%", "?", "#"))
        and not any(
            ord(character) < _FIRST_PRINTABLE or ord(character) == _DELETE
            for character in name
        )
        and all(part not in {"", ".", ".."} for part in name.split("/"))
    )


def _target(source: str, target: str) -> str:
    if (
        not target
        or target.startswith("/")
        or any(character in target for character in ("\\", ":", "%", "?", "#"))
        or any(
            ord(character) < _FIRST_PRINTABLE or ord(character) == _DELETE
            for character in target
        )
    ):
        reject(ReverseErrorCategory.MALFORMED)
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
    if not _safe_part(resolved):
        reject(ReverseErrorCategory.MALFORMED)
    return resolved


class PptxArchive:
    """Read each bounded part once; never follow a filesystem or network path."""

    def __init__(self, source: bytes, limits: PptxReadLimits) -> None:
        self.limits = limits
        self._bytes: dict[str, bytes] = {}
        self._xml: dict[str, ElementTree.Element] = {}
        self._elements = 0
        self._attributes = 0
        try:
            self._archive = zipfile.ZipFile(io.BytesIO(source))
            infos = self._archive.infolist()
            if len(infos) > limits.entries:
                reject(ReverseErrorCategory.RESOURCE_LIMIT)
            self._parts: dict[str, zipfile.ZipInfo] = {}
            total = 0
            for info in infos:
                name = info.filename.removesuffix("/")
                mode = info.external_attr >> 16
                if (
                    not _safe_part(name)
                    or info.orig_filename != info.filename
                    or name in self._parts
                    or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                ):
                    reject(ReverseErrorCategory.MALFORMED)
                if info.flag_bits & 1:
                    reject(ReverseErrorCategory.ENCRYPTED)
                total += info.file_size
                if info.file_size > limits.member_bytes or total > limits.total_bytes:
                    reject(ReverseErrorCategory.RESOURCE_LIMIT)
                self._parts[name] = info
        except zipfile.BadZipFile, OSError, ValueError:
            reject(ReverseErrorCategory.MALFORMED)

    def close(self) -> None:
        self._archive.close()

    def read(self, name: str) -> bytes:
        """Require a regular existing member and enforce its actual byte length."""

        if name in self._bytes:
            return self._bytes[name]
        info = self._parts.get(name)
        if info is None or info.is_dir():
            reject(ReverseErrorCategory.MALFORMED)
        try:
            with self._archive.open(info) as stream:
                data = stream.read(self.limits.member_bytes + 1)
        except (
            zipfile.BadZipFile,
            OSError,
            RuntimeError,
            NotImplementedError,
            zlib.error,
        ):
            reject(ReverseErrorCategory.MALFORMED)
        if len(data) > self.limits.member_bytes:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        if len(data) != info.file_size:
            reject(ReverseErrorCategory.MALFORMED)
        self._bytes[name] = data
        return data

    def xml(self, name: str) -> ElementTree.Element:
        """Reject entities and enforce document-wide counters during parsing."""

        if name in self._xml:
            return self._xml[name]
        depth = 0
        try:
            parser = DefusedElementTree.iterparse(
                io.BytesIO(self.read(name)),
                events=("start", "end"),
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
            for event, element in parser:
                if event == "start":
                    depth += 1
                    self._elements += 1
                    self._attributes += len(element.attrib)
                    if (
                        depth > self.limits.xml_depth
                        or self._elements > self.limits.xml_elements
                        or self._attributes > self.limits.xml_attributes
                    ):
                        reject(ReverseErrorCategory.RESOURCE_LIMIT)
                else:
                    depth -= 1
            root = parser.root
        except DefusedXmlException, ElementTree.ParseError, UnicodeError, ValueError:
            reject(ReverseErrorCategory.MALFORMED)
        self._xml[name] = root
        return root

    def relationships(self, source: str) -> dict[str, Relationship]:
        """Resolve unique relationship identifiers without fetching resources."""

        folder, filename = posixpath.split(source)
        name = f"{folder}/_rels/{filename}.rels" if folder else f"_rels/{filename}.rels"
        if name not in self._parts:
            return {}
        root = self.xml(name)
        if root.tag != f"{{{PACKAGE_RELATIONSHIPS}}}Relationships":
            reject(ReverseErrorCategory.MALFORMED)
        result: dict[str, Relationship] = {}
        for element in root:
            identity = element.get("Id", "")
            kind = element.get("Type", "")
            target = element.get("Target", "")
            mode = element.get("TargetMode", "Internal")
            if (
                element.tag != f"{{{PACKAGE_RELATIONSHIPS}}}Relationship"
                or not identity
                or identity in result
                or not kind
                or mode not in {"Internal", "External"}
            ):
                reject(ReverseErrorCategory.MALFORMED)
            kind = kind.removeprefix(OFFICE_RELATIONSHIPS + "/")
            if mode == "External":
                if kind != "hyperlink" or not _is_safe_hyperlink(target):
                    reject(ReverseErrorCategory.ASSET_INVALID)
                result[identity] = Relationship(kind, target, True)
            else:
                result[identity] = Relationship(kind, _target(source, target))
        return result
