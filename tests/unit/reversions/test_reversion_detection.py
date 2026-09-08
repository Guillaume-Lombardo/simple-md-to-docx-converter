"""Unit coverage for bounded reverse-upload content detection."""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from markweave.reversions.detection import admit_reverse_source
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.formats import FormatFamily

pytestmark = pytest.mark.unit


def _zip(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def _ole(stream_name: str) -> bytes:
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    header[24:26] = (0x003E).to_bytes(2, "little")
    header[26:28] = (3).to_bytes(2, "little")
    header[28:30] = b"\xfe\xff"
    header[30:32] = (9).to_bytes(2, "little")
    header[32:34] = (6).to_bytes(2, "little")
    header[44:48] = (1).to_bytes(4, "little")
    header[48:52] = (0).to_bytes(4, "little")
    header[56:60] = (4096).to_bytes(4, "little")
    header[68:72] = (0xFFFFFFFE).to_bytes(4, "little")
    header[72:76] = (0).to_bytes(4, "little")
    header[76:] = struct.pack("<109I", 1, *([0xFFFFFFFF] * 108))

    directory = bytearray(512)
    encoded = stream_name.encode("utf-16le") + b"\0\0"
    directory[: len(encoded)] = encoded
    directory[64:66] = len(encoded).to_bytes(2, "little")
    directory[66] = 2
    fat = bytearray(b"\xff" * 512)
    fat[:8] = struct.pack("<2I", 0xFFFFFFFE, 0xFFFFFFFD)
    return bytes(header + directory + fat)


def _replace(source: bytes, offset: int, value: bytes) -> bytes:
    changed = bytearray(source)
    changed[offset : offset + len(value)] = value
    return bytes(changed)


@pytest.mark.parametrize(
    ("filename", "content", "family", "parser"),
    [
        ("report.rtf", b"{\\rtf1 document}", FormatFamily.RTF, "rtf"),
        ("report.pdf", b"prefix%PDF-1.7\n", FormatFamily.PDF, "pdf"),
        (
            "report.doc",
            _ole("WordDocument"),
            FormatFamily.WORD,
            "doc",
        ),
        (
            "slides.ppt",
            _ole("PowerPoint Document"),
            FormatFamily.POWERPOINT,
            "ppt",
        ),
        (
            "sheet.xls",
            _ole("Workbook"),
            FormatFamily.EXCEL,
            "xlsx",
        ),
        ("table.csv", b"name,value\nalice,1\n", FormatFamily.CSV, "csv"),
    ],
)
def test_admits_non_zip_families_from_content_and_safe_hint(
    filename: str, content: bytes, family: FormatFamily, parser: str
) -> None:
    admitted = admit_reverse_source(filename, content)

    assert admitted.source_stem == filename.rsplit(".", 1)[0]
    assert admitted.admission.family is family
    assert admitted.admission.parser_format == parser


@pytest.mark.parametrize(
    ("filename", "entries", "family", "parser"),
    [
        (
            "report.docx",
            {
                "_rels/.rels": b"officeDocument",
                "[Content_Types].xml": b"wordprocessingml",
                "word/document.xml": b"document",
            },
            FormatFamily.WORD,
            "docx",
        ),
        (
            "slides.pptx",
            {
                "_rels/.rels": b"officeDocument",
                "[Content_Types].xml": b"presentationml",
                "ppt/presentation.xml": b"presentation",
            },
            FormatFamily.POWERPOINT,
            "pptx",
        ),
        (
            "sheet.xlsb",
            {
                "_rels/.rels": b"officeDocument",
                "xl/workbook.bin": b"workbook",
            },
            FormatFamily.EXCEL,
            "xlsx",
        ),
        (
            "book.epub",
            {
                "mimetype": b"application/epub+zip",
                "META-INF/container.xml": b"container",
            },
            FormatFamily.EPUB,
            "epub",
        ),
        (
            "document.odt",
            {"mimetype": b"application/vnd.oasis.opendocument.text"},
            FormatFamily.OPENDOCUMENT,
            "odt",
        ),
        (
            "slides.odp",
            {
                "META-INF/manifest.xml": (
                    b"application/vnd.oasis.opendocument.presentation-template"
                )
            },
            FormatFamily.OPENDOCUMENT,
            "odp",
        ),
    ],
)
def test_admits_zip_families_from_bounded_metadata(
    filename: str,
    entries: dict[str, bytes],
    family: FormatFamily,
    parser: str,
) -> None:
    admitted = admit_reverse_source(filename, _zip(entries))

    assert admitted.admission.family is family
    assert admitted.admission.parser_format == parser


@pytest.mark.parametrize(
    "filename",
    [
        "",
        "report",
        ".docx",
        "report.",
        "../report.docx",
        "folder/report.docx",
        "bad\n.docx",
    ],
)
def test_rejects_unsafe_or_incomplete_filename_hints(filename: str) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        admit_reverse_source(filename, b"{\\rtf1 document}")
    assert captured.value.category is ReverseErrorCategory.UNSUPPORTED


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("report.docx", b"{\\rtf1 mismatch}"),
        ("table.csv", b"\xff"),
        ("table.csv", b"a\x00b"),
        ("table.csv", b""),
        ("report.docx", b"PK\x03\x04malformed"),
        ("report.pdf", b"x" * 1024 + b"%PDF-1.7"),
    ],
)
def test_rejects_mismatch_undetected_and_invalid_csv(
    filename: str, content: bytes
) -> None:
    with pytest.raises(ReverseConversionError) as captured:
        admit_reverse_source(filename, content)
    assert captured.value.category is ReverseErrorCategory.UNSUPPORTED


def test_detection_priority_does_not_accept_embedded_lower_priority_magic() -> None:
    ole_with_pdf = _ole("WordDocument") + b"%PDF-1.7"

    with pytest.raises(ReverseConversionError):
        admit_reverse_source("spoof.pdf", ole_with_pdf)


def test_rejects_ole_signature_with_only_embedded_stream_name_bytes() -> None:
    spoof = bytes.fromhex("d0cf11e0a1b11ae1") + "WordDocument".encode("utf-16le")

    with pytest.raises(ReverseConversionError):
        admit_reverse_source("spoof.doc", spoof)


@pytest.mark.parametrize(
    "content",
    [
        _ole("Unknown"),
        _replace(_ole("WordDocument"), 28, b"\x00\x00"),
        _replace(_ole("WordDocument"), 30, (8).to_bytes(2, "little")),
        _replace(_ole("WordDocument"), 30, (12).to_bytes(2, "little")),
        _replace(_ole("WordDocument"), 44, (0).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 44, (2).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 76, (3).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 48, (300).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 48, (2).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 1024, (0).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 1024, (0xFFFFFFFF).to_bytes(4, "little")),
        _replace(_ole("WordDocument"), 512, b"\x00\xd8"),
        _replace(_ole("WordDocument"), 576, (2).to_bytes(2, "little")),
    ],
)
def test_rejects_malformed_or_unrecognized_ole_directories(content: bytes) -> None:
    with pytest.raises(ReverseConversionError):
        admit_reverse_source("report.doc", content)


@pytest.mark.parametrize(
    ("filename", "entries"),
    [
        (
            "report.docx",
            {"_rels/.rels": b"officeDocument", "word/document.xml": b"body"},
        ),
        (
            "slides.pptx",
            {
                "_rels/.rels": b"officeDocument",
                "ppt/presentation.xml": b"slides",
            },
        ),
    ],
)
def test_zip_root_parts_are_bounded_detection_fallbacks(
    filename: str, entries: dict[str, bytes]
) -> None:
    admit_reverse_source(filename, _zip(entries))


def test_rejects_zip_without_an_office_relationship() -> None:
    with pytest.raises(ReverseConversionError):
        admit_reverse_source("report.docx", _zip({"word/document.xml": b"body"}))


@pytest.mark.parametrize(
    "entries",
    [
        {"META-INF/manifest.xml": b"unknown media type"},
        {"_rels/.rels": b"officeDocument"},
    ],
)
def test_rejects_zip_with_unrecognized_structural_metadata(
    entries: dict[str, bytes],
) -> None:
    with pytest.raises(ReverseConversionError):
        admit_reverse_source("report.docx", _zip(entries))


def test_rejects_encrypted_zip_metadata_before_inspection() -> None:
    content = bytearray(_zip({"word/document.xml": b"body"}))
    content[6:8] = (1).to_bytes(2, "little")
    central = content.index(b"PK\x01\x02")
    content[central + 8 : central + 10] = (1).to_bytes(2, "little")

    with pytest.raises(ReverseConversionError):
        admit_reverse_source("report.docx", bytes(content))


def test_rejects_oversized_zip_detection_metadata() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", b"x" * 10_000)
    content = output.getvalue()
    assert len(content) < 10_000

    with pytest.raises(ReverseConversionError):
        admit_reverse_source("report.odt", content)


def test_rejects_csv_with_unclosed_quoted_field() -> None:
    with pytest.raises(ReverseConversionError):
        admit_reverse_source("table.csv", b'"unclosed')
