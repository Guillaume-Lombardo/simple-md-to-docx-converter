"""Unit tests for bounded document archive preparation."""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Iterable

import pytest
from PIL import Image

from markweave.conversion.archive import (
    ApprovedResource,
    ArchiveLimits,
    prepare_archive,
)
from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits
from markweave.conversion.service import DocxConversionService
from markweave.jobs.policy import ArchiveResourceBudget

LIMITS = ArchiveLimits(200_000, 20, 100_000, 150_000, 100.0, 50_000, 5)
IMAGE_LIMITS = ImageLimits(100_000, 1_000, 1_000, 1_000_000, 10_000, 64)


def archive_bytes(
    entries: Iterable[tuple[str | zipfile.ZipInfo, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def normalize_stub(path, content, _limits):
    return b"PNG:" + path.as_posix().encode() + b":" + content


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 6), "blue").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.unit
def test_prepares_root_document_and_normalizes_supported_images() -> None:
    data = archive_bytes(
        [
            ("notes.md", b"# Not selected"),
            ("assets/picture.jpg", b"jpeg"),
            ("document.md", b"# Selected \xe2\x9c\x93"),
        ]
    )
    document = prepare_archive(
        data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub
    )
    assert document.markdown == "# Selected ✓"
    assert document.entrypoint.as_posix() == "document.md"
    assert document.resources == (
        ApprovedResource(
            document.resources[0].path,
            b"PNG:assets/picture.jpg:jpeg",
        ),
    )
    assert document.resources[0].path.as_posix() == "assets/picture.jpg"


@pytest.mark.unit
def test_service_prepares_validates_and_delegates_real_png_archive(mocker) -> None:
    converter = mocker.Mock()
    converter.convert.return_value = b"docx"
    source = archive_bytes(
        [
            ("document.md", b"![safe](assets/image.png)"),
            ("assets/image.png", png_bytes()),
        ]
    )
    result = DocxConversionService(converter).convert_archive(
        source,
        b"reference",
        LIMITS,
        IMAGE_LIMITS,
    )
    assert result == b"docx"
    approved = converter.convert.call_args.args[0]
    assert approved.resources[0].content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.unit
def test_service_applies_shared_archive_budget_before_extraction(mocker) -> None:
    converter = mocker.Mock()
    source = archive_bytes([("document.md", b"# Safe"), ("image.png", png_bytes())])
    service = DocxConversionService(
        converter,
        ArchiveResourceBudget(
            upload_bytes=len(source),
            decompressed_bytes=100_000,
            file_count=1,
            image_count=5,
        ),
    )

    with pytest.raises(ConversionError, match="configured limits"):
        service.convert_archive(source, b"reference", LIMITS, IMAGE_LIMITS)

    converter.convert.assert_not_called()


@pytest.mark.unit
def test_selects_the_only_nested_markdown_without_root_document() -> None:
    data = archive_bytes([("package/readme.MD", b"# Selected")])
    document = prepare_archive(
        data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub
    )
    assert document.entrypoint.as_posix() == "package/readme.MD"


@pytest.mark.unit
@pytest.mark.parametrize(
    "entries",
    [
        [],
        [("image.png", b"image")],
        [("one.md", b"one"), ("two.md", b"two")],
    ],
)
def test_rejects_missing_or_ambiguous_markdown(entries) -> None:
    data = archive_bytes(entries)
    with pytest.raises(ConversionError) as captured:
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)
    assert captured.value.code is ConversionErrorCode.VALIDATION


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [
        "/absolute.md",
        "../escape.md",
        "folder/../escape.md",
        "C:/drive.md",
        "folder\\windows.md",
        "folder//double.md",
        "./dot.md",
        "nul\0.md",
    ],
)
def test_rejects_unsafe_or_noncanonical_paths(name: str) -> None:
    data = archive_bytes([(name, b"# Unsafe")])
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
@pytest.mark.parametrize(
    "entries",
    [
        [("document.md", b"one"), ("DOCUMENT.MD", b"two")],
        [("caf\xe9.md", b"one"), ("cafe\u0301.md", b"two")],
        [
            ("document.md", b"one"),
            ("asset.md", b"nested collision"),
            ("ASSET.MD/pic.png", b"x"),
        ],
        [
            ("document.md", b"one"),
            ("asset.md", b"empty directory collision"),
            ("ASSET.MD/empty/", b""),
        ],
    ],
)
def test_rejects_normalized_and_file_prefix_collisions(entries) -> None:
    data = archive_bytes(entries)
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_allows_explicit_directories_without_treating_them_as_collisions() -> None:
    directory = zipfile.ZipInfo("assets/")
    directory.external_attr = (stat.S_IFDIR | 0o755) << 16
    data = archive_bytes(
        [(directory, b""), ("assets/pic.png", b"x"), ("document.md", b"# Safe")]
    )
    document = prepare_archive(
        data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub
    )
    assert len(document.resources) == 1


@pytest.mark.unit
@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_rejects_symbolic_links_and_special_unix_members(file_type: int) -> None:
    special = zipfile.ZipInfo("asset.png")
    special.create_system = 3
    special.external_attr = (file_type | 0o644) << 16
    data = archive_bytes([(special, b"target"), ("document.md", b"# Safe")])
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_special_unix_member_disguised_as_directory() -> None:
    disguised = zipfile.ZipInfo("assets/")
    disguised.create_system = 3
    disguised.external_attr = (stat.S_IFLNK | 0o755) << 16
    data = archive_bytes([(disguised, b""), ("document.md", b"# Safe")])
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_disallowed_member_type() -> None:
    data = archive_bytes([("document.md", b"# Safe"), ("secret.txt", b"secret")])
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_unsupported_compression() -> None:
    data = archive_bytes([("document.md", b"# Safe")], compression=zipfile.ZIP_BZIP2)
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_encrypted_member_metadata(mocker) -> None:
    data = archive_bytes([("document.md", b"# Safe")])
    original = zipfile.ZipFile.infolist

    def encrypted_members(archive):
        members = original(archive)
        members[0].flag_bits |= 1
        return members

    mocker.patch.object(zipfile.ZipFile, "infolist", encrypted_members)
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_abnormal_compression_ratio() -> None:
    data = archive_bytes(
        [("document.md", b"x" * 20_000)], compression=zipfile.ZIP_DEFLATED
    )
    strict = ArchiveLimits(200_000, 20, 100_000, 150_000, 2.0, 50_000, 5)
    with pytest.raises(ConversionError, match="exceeds configured limits"):
        prepare_archive(data, strict, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
@pytest.mark.parametrize(
    "limits",
    [
        ArchiveLimits(100, 1, 20, 20, 100.0, 20, 1),
        ArchiveLimits(10_000, 1, 20, 20, 100.0, 20, 1),
        ArchiveLimits(10_000, 2, 3, 20, 100.0, 20, 1),
        ArchiveLimits(10_000, 2, 20, 3, 100.0, 20, 1),
        ArchiveLimits(10_000, 2, 20, 20, 100.0, 3, 1),
    ],
)
def test_enforces_each_archive_size_limit(limits: ArchiveLimits) -> None:
    data = archive_bytes([("document.md", b"1234"), ("image.png", b"1")])
    with pytest.raises(ConversionError, match="exceeds configured limits"):
        prepare_archive(data, limits, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_enforces_image_count_limit() -> None:
    data = archive_bytes(
        [("document.md", b"safe"), ("one.png", b"1"), ("two.png", b"2")]
    )
    strict = ArchiveLimits(10_000, 3, 100, 300, 100.0, 100, 1)
    with pytest.raises(ConversionError, match="exceeds configured limits"):
        prepare_archive(data, strict, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
def test_rejects_invalid_utf8_markdown() -> None:
    data = archive_bytes([("document.md", b"\xff")])
    with pytest.raises(ConversionError, match="not valid UTF-8"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid",
    [
        (0, 1, 1, 1, 1.0, 1, 1),
        (1, True, 1, 1, 1.0, 1, 1),
        (1, 1, 1, 1, float("nan"), 1, 1),
        (1, 1, 1, 1, float("inf"), 1, 1),
        (1, 1, 1, 1, 0.9, 1, 1),
        (1, 1, 1, 1, 1.0, 1, 1, 0),
    ],
)
def test_limits_fail_closed(invalid) -> None:
    with pytest.raises(ValueError):
        ArchiveLimits(*invalid)


@pytest.mark.unit
def test_file_limit_does_not_count_directory_entries() -> None:
    directory = zipfile.ZipInfo("docs/")
    directory.external_attr = stat.S_IFDIR << 16
    data = archive_bytes([(directory, b""), ("docs/document.md", b"# Safe")])
    limits = ArchiveLimits(10_000, 2, 100, 100, 10.0, 100, 1, max_files=1)

    document = prepare_archive(
        data, limits, IMAGE_LIMITS, image_normalizer=normalize_stub
    )

    assert document.entrypoint.as_posix() == "docs/document.md"


@pytest.mark.unit
def test_corrupt_or_truncated_archives_have_stable_content_free_errors() -> None:
    valid = archive_bytes([("document.md", b"sensitive")])
    for data in (b"not zip and sensitive", valid[:-8]):
        with pytest.raises(ConversionError) as captured:
            prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)
        assert captured.value.code is ConversionErrorCode.VALIDATION
        assert str(captured.value) == "Document archive is invalid."


@pytest.mark.unit
def test_member_read_failure_has_stable_content_free_error(mocker) -> None:
    data = archive_bytes([("document.md", b"sensitive Markdown")])
    mocker.patch.object(
        zipfile.ZipExtFile,
        "read",
        side_effect=OSError("sensitive local path"),
    )
    with pytest.raises(ConversionError) as captured:
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)
    assert captured.value.code is ConversionErrorCode.VALIDATION
    assert str(captured.value) == "Document archive is invalid."


@pytest.mark.unit
def test_declared_member_size_mismatch_is_rejected(mocker) -> None:
    data = archive_bytes([("document.md", b"safe")])
    original = zipfile.ZipFile.infolist

    def forged_members(archive):
        members = original(archive)
        members[0].file_size += 1
        return members

    mocker.patch.object(zipfile.ZipFile, "infolist", forged_members)
    with pytest.raises(ConversionError, match="archive is invalid"):
        prepare_archive(data, LIMITS, IMAGE_LIMITS, image_normalizer=normalize_stub)
