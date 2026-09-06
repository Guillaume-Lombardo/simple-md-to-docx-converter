"""Bounded content detection for reverse-upload admission."""

from __future__ import annotations

import csv
import io
import struct
import zipfile
from dataclasses import dataclass

from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.formats import FormatAdmission, admit_format

_OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_OLE_WORD = "WordDocument"
_OLE_POWERPOINT = "PowerPoint Document"
_OLE_WORKBOOKS = ("Workbook", "Book")
_OLE_FREE_SECTOR = 0xFFFFFFFF
_OLE_END_OF_CHAIN = 0xFFFFFFFE
_OLE_FAT_SECTOR = 0xFFFFFFFD
_OLE_HEADER_BYTES = 512
_OLE_HEADER_DIFAT_ENTRIES = 109
_OLE_DIRECTORY_ENTRY_BYTES = 128
_OLE_STREAM_OBJECT_TYPE = 2
_OLE_NAME_MINIMUM_BYTES = 2
_OLE_NAME_MAXIMUM_BYTES = 64
_OFFICE_RELATIONSHIP = b"officeDocument"
_WORD_MARKERS = (b"wordprocessingml", b"/word/document.xml")
_POWERPOINT_MARKERS = (b"presentationml", b"/ppt/presentation.xml")
_EXCEL_MARKERS = (b"spreadsheetml", b"/xl/workbook.xml")
_ODF_MIME_TYPES = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
    b"application/vnd.oasis.opendocument.text-template": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet-template": "ods",
    b"application/vnd.oasis.opendocument.presentation-template": "odp",
}
_FIRST_CONTROL_CODEPOINT = 32
_DELETE_CODEPOINT = 127


@dataclass(frozen=True, slots=True)
class ReverseSourceAdmission:
    """Safe owner-visible stem and content-bound parser admission."""

    source_stem: str
    admission: FormatAdmission


def admit_reverse_source(filename: str, content: bytes) -> ReverseSourceAdmission:
    """Bind a safe filename hint to independently detected bounded content."""

    stem, extension = _filename_parts(filename)
    if type(content) is not bytes or not content:
        reject(ReverseErrorCategory.UNSUPPORTED)
    detected = _detect(content)
    csv_valid = extension.casefold() == ".csv" and _valid_csv_text(content)
    return ReverseSourceAdmission(
        stem,
        admit_format(extension, detected, csv_text_validated=csv_valid),
    )


def _filename_parts(filename: str) -> tuple[str, str]:
    if (
        type(filename) is not str
        or not filename
        or "/" in filename
        or "\\" in filename
        or any(
            ord(character) < _FIRST_CONTROL_CODEPOINT
            or ord(character) == _DELETE_CODEPOINT
            for character in filename
        )
    ):
        reject(ReverseErrorCategory.UNSUPPORTED)
    stem, separator, suffix = filename.rpartition(".")
    if not separator or not stem or not suffix:
        reject(ReverseErrorCategory.UNSUPPORTED)
    return stem, f".{suffix}"


def _detect(content: bytes) -> str | None:
    if content.startswith(b"{\\rtf"):
        return "rtf"
    if content.startswith(_OLE_SIGNATURE):
        return _detect_ole(content)
    if content.startswith(b"PK\x03\x04"):
        detected = _detect_zip(content)
        if detected is not None:
            return detected
    if b"%PDF-" in content[:1024]:
        return "pdf"
    return None


def _detect_ole(content: bytes) -> str | None:
    names = _ole_stream_names(content)
    if names is None:
        return None
    if _OLE_WORD in names:
        return "doc"
    if _OLE_POWERPOINT in names:
        return "ppt"
    if any(marker in names for marker in _OLE_WORKBOOKS):
        return "xlsx"
    return None


def _ole_stream_names(  # noqa: PLR0911, PLR0912 - fail-closed bounded parser
    content: bytes,
) -> frozenset[str] | None:
    """Read bounded CFB directory stream names without parsing document payloads."""

    if len(content) < _OLE_HEADER_BYTES or content[28:30] != b"\xfe\xff":
        return None
    sector_shift = int.from_bytes(content[30:32], "little")
    if sector_shift not in {9, 12}:
        return None
    sector_size = 1 << sector_shift
    maximum_sector = (len(content) - sector_size) // sector_size
    if maximum_sector <= 0:
        return None
    fat_count = int.from_bytes(content[44:48], "little")
    directory_sector = int.from_bytes(content[48:52], "little")
    if fat_count <= 0 or fat_count > _OLE_HEADER_DIFAT_ENTRIES:
        return None
    difat = struct.unpack_from(f"<{_OLE_HEADER_DIFAT_ENTRIES}I", content, 76)
    fat_sectors = tuple(
        sector for sector in difat[:fat_count] if sector != _OLE_FREE_SECTOR
    )
    if len(fat_sectors) != fat_count:
        return None
    fat: list[int] = []
    for sector in fat_sectors:
        block = _ole_sector(content, sector, sector_size, maximum_sector)
        if block is None:
            return None
        fat.extend(struct.unpack(f"<{sector_size // 4}I", block))

    names: set[str] = set()
    visited: set[int] = set()
    sector = directory_sector
    while sector != _OLE_END_OF_CHAIN:
        if sector in visited or sector >= len(fat) or len(visited) >= maximum_sector:
            return None
        visited.add(sector)
        block = _ole_sector(content, sector, sector_size, maximum_sector)
        if block is None:
            return None
        for offset in range(0, sector_size, _OLE_DIRECTORY_ENTRY_BYTES):
            entry = block[offset : offset + _OLE_DIRECTORY_ENTRY_BYTES]
            name_length = int.from_bytes(entry[64:66], "little")
            if (
                entry[66] != _OLE_STREAM_OBJECT_TYPE
                or name_length < _OLE_NAME_MINIMUM_BYTES
                or name_length > _OLE_NAME_MAXIMUM_BYTES
            ):
                continue
            try:
                name = entry[: name_length - 2].decode("utf-16le", errors="strict")
            except UnicodeDecodeError:
                return None
            if name:
                names.add(name)
        sector = fat[sector]
        if sector in {_OLE_FREE_SECTOR, _OLE_FAT_SECTOR}:
            return None
    return frozenset(names)


def _ole_sector(
    content: bytes, sector: int, sector_size: int, maximum_sector: int
) -> bytes | None:
    if sector >= maximum_sector:
        return None
    start = sector_size + sector * sector_size
    block = content[start : start + sector_size]
    return block if len(block) == sector_size else None


def _detect_zip(content: bytes) -> str | None:  # noqa: PLR0911, PLR0912
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if any(info.flag_bits & 1 for info in infos):
                return None
            names = {info.filename for info in infos}
            mimetype = _read_member(archive, "mimetype", len(content))
            if mimetype == b"application/epub+zip" or (
                "META-INF/container.xml" in names and "mimetype" in names
            ):
                return "epub"
            odf = _ODF_MIME_TYPES.get(mimetype or b"")
            if odf is not None:
                return odf
            manifest = _read_member(archive, "META-INF/manifest.xml", len(content))
            if manifest is not None:
                for media_type, detected in _ODF_MIME_TYPES.items():
                    if media_type in manifest:
                        return detected
            relationships = _read_member(archive, "_rels/.rels", len(content))
            content_types = _read_member(archive, "[Content_Types].xml", len(content))
            office = (relationships or b"") + (content_types or b"")
            if _OFFICE_RELATIONSHIP not in office:
                return None
            for markers, detected in (
                (_WORD_MARKERS, "docx"),
                (_POWERPOINT_MARKERS, "pptx"),
                (_EXCEL_MARKERS, "xlsx"),
            ):
                if any(marker in office for marker in markers):
                    return detected
            if "word/document.xml" in names:
                return "docx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            if "xl/workbook.xml" in names or "xl/workbook.bin" in names:
                return "xlsx"
    except zipfile.BadZipFile, OSError, RuntimeError:
        return None
    return None


def _read_member(archive: zipfile.ZipFile, name: str, maximum: int) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.is_dir() or info.file_size > maximum:
        return None
    with archive.open(info) as member:
        value = member.read(maximum + 1)
    return value if len(value) <= maximum else None


def _valid_csv_text(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    if "\x00" in text:
        return False
    try:
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        return any(any(cell for cell in row) for row in rows)
    except csv.Error:
        return False
