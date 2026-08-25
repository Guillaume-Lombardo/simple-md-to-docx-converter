"""Real ZIP, image-engine, Pandoc, and OpenXML integration for T08."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from markweave.conversion.archive import ArchiveLimits, prepare_archive
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits
from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
from markweave.conversion.service import DocxConversionService
from tests.golden.limits import ArchiveLimits as InspectionLimits
from tests.golden.openxml import inspect_docx

pytestmark = pytest.mark.integration

INPUT_LIMITS = ArchiveLimits(
    max_archive_bytes=1_000_000,
    max_entries=20,
    max_member_uncompressed_bytes=500_000,
    max_total_uncompressed_bytes=800_000,
    max_compression_ratio=200.0,
    max_markdown_bytes=100_000,
    max_images=10,
)
IMAGE_LIMITS = ImageLimits(500_000, 2_000, 2_000, 4_000_000, 10_000, 64)
DOCX_LIMITS = InspectionLimits(300, 10_000_000, 30_000_000, 200.0)


def _zip_bytes(
    entries: list[tuple[str | zipfile.ZipInfo, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def _default_reference_docx() -> bytes:
    return subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def _raster_bytes(format_name: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (24, 16), "#336699").save(output, format=format_name)
    return output.getvalue()


@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("safe.png", _raster_bytes("PNG")),
        ("safe.jpg", _raster_bytes("JPEG")),
        ("safe.gif", _raster_bytes("GIF")),
        ("safe.webp", _raster_bytes("WEBP")),
        (
            "safe.svg",
            Path("tests/corpus/local-images/assets/safe-local.svg").read_bytes(),
        ),
    ],
    ids=["png", "jpeg", "static-gif", "webp", "sanitized-svg"],
)
def test_real_archive_image_pipeline_embeds_only_normalized_png(
    tmp_path: Path, filename: str, source: bytes
) -> None:
    archive = _zip_bytes(
        [
            (
                "document.md",
                f"# Local image\n\n![safe](assets/{filename})\n".encode(),
            ),
            (f"assets/{filename}", source),
        ]
    )
    service = DocxConversionService(
        PandocDocxConverter(PandocConfig("pandoc", 30.0, 2.0, tmp_path), os.environ)
    )
    result = service.convert_archive(
        archive,
        _default_reference_docx(),
        INPUT_LIMITS,
        IMAGE_LIMITS,
    )
    snapshot = inspect_docx(result, DOCX_LIMITS)
    media_names = tuple(
        name for name in snapshot.binary_sha256 if name.startswith("word/media/")
    )
    assert len(media_names) == 1
    with zipfile.ZipFile(io.BytesIO(result)) as docx:
        assert docx.read(media_names[0]).startswith(b"\x89PNG\r\n\x1a\n")
    assert "Local image" in " ".join(snapshot.document_text)


def test_real_zip_crc_failure_is_content_free() -> None:
    archive = bytearray(
        _zip_bytes(
            [("document.md", b"sensitive markdown")],
            compression=zipfile.ZIP_STORED,
        )
    )
    offset = archive.find(b"sensitive markdown")
    assert offset >= 0
    archive[offset] ^= 0x01
    with pytest.raises(ConversionError) as captured:
        prepare_archive(bytes(archive), INPUT_LIMITS, IMAGE_LIMITS)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Document archive is invalid."
    assert "sensitive" not in str(captured.value)


def test_real_encrypted_flag_is_rejected_before_member_read() -> None:
    archive = bytearray(_zip_bytes([("document.md", b"safe")]))
    local = archive.find(b"PK\x03\x04")
    central = archive.find(b"PK\x01\x02")
    assert local >= 0 and central >= 0
    local_flags = struct.unpack_from("<H", archive, local + 6)[0]
    central_flags = struct.unpack_from("<H", archive, central + 8)[0]
    struct.pack_into("<H", archive, local + 6, local_flags | 1)
    struct.pack_into("<H", archive, central + 8, central_flags | 1)
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(bytes(archive), INPUT_LIMITS, IMAGE_LIMITS)


def test_real_archive_never_materializes_traversal_member(tmp_path: Path) -> None:
    outside = tmp_path / "escape.png"
    archive = _zip_bytes([("document.md", b"# Safe"), ("../escape.png", b"hostile")])
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(archive, INPUT_LIMITS, IMAGE_LIMITS)
    assert not outside.exists()
