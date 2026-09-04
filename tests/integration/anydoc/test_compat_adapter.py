from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import anydoc
import pytest

from markweave.conversion.images import ImageLimits
from markweave.reversions import _anydoc_compat as compat
from markweave.reversions.assets import ReverseAssetLimits, normalize_assets
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

CORPUS = Path(__file__).parents[3] / "spikes" / "anydoc" / "corpus"
_UNSAFE_DOCX_TARGET = b'Target="../../fixture-src/sibling.odt"'
_SAFE_LINK = b"https://example.test/safe-url"


def _source(
    relative_path: str, *, second_link: str = "https://example.test/sibling"
) -> bytes:
    source = (CORPUS / relative_path).read_bytes()
    if relative_path == "doc/text.doc":
        unsafe = b"../../fixture-src/sibling.odt"
        assert len(unsafe) == len(_SAFE_LINK)
        return source.replace(unsafe, _SAFE_LINK).replace(
            unsafe.decode().encode("utf-16le"), _SAFE_LINK.decode().encode("utf-16le")
        )
    if relative_path == "rtf/text.rtf":
        return source.replace(
            b"file:///anydoc/tests/fixture-src/sibling.odt", _SAFE_LINK
        )
    archive_member = {
        "docx/text.docx": "word/_rels/document.xml.rels",
        "odt/text.odt": "content.xml",
    }.get(relative_path)
    if archive_member is None:
        return source
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as archive,
        zipfile.ZipFile(output, "w") as rebuilt,
    ):
        for info in archive.infolist():
            content = archive.read(info)
            if info.filename == archive_member:
                if relative_path == "docx/text.docx":
                    content = content.replace(
                        _UNSAFE_DOCX_TARGET,
                        f'Target="{second_link}"'.encode(),
                    )
                else:
                    content = content.replace(
                        b"../../../fixture-src/sibling.odt", _SAFE_LINK
                    )
            rebuilt.writestr(info, content)
    return output.getvalue()


def _document_cases() -> list[tuple[str, anydoc.Format | None]]:
    manifest: dict[str, Any] = json.loads((CORPUS / "manifest.json").read_text())
    cases: list[tuple[str, anydoc.Format | None]] = []
    for record in manifest["files"]:
        relative_path = record["path"]
        if relative_path.endswith(".pdf") or "--errors" in relative_path:
            continue
        cases.append((relative_path, "csv" if relative_path.endswith(".csv") else None))
    return cases


@pytest.mark.parametrize(("relative_path", "format_hint"), _document_cases())
def test_asset_free_rendering_matches_pinned_upstream(
    relative_path: str, format_hint: anydoc.Format | None
) -> None:
    source = _source(relative_path)
    document = compat.parse_document(source, format_hint)
    occurrences = compat.extract_asset_sources(document)

    rendered = compat.render_document(document, (None,) * len(occurrences))

    assert rendered == anydoc.to_markdown_bytes(source, format_hint, ocr="reject")


def test_parse_calls_the_native_parser_once(mocker: Any) -> None:
    source = _source("docx/text.docx")
    spy = mocker.spy(anydoc, "to_document")

    document = compat.parse_document(source)
    compat.extract_asset_sources(document)
    compat.render_document(document, (None,))

    spy.assert_called_once_with(source, None)


@pytest.mark.parametrize(
    "target",
    [
        "javascript:alert(1)",
        "javascript%3Aalert(1)",
        "file:///etc/passwd",
        "//example.test/path",
        "../../escape.txt",
        "https://user@example.test/path",
        "https://user:secret@example.test/path",
        "https://example.test/%0Aheader",
        "https://example.test/a b",
        "https://exa mple.test/path",
        "https://example.test/path\u00a0x",
        "https://example.test:invalid/path",
        "https%3A//example.test/path",
    ],
)
def test_real_document_rejects_unsafe_hyperlink_destinations(target: str) -> None:
    source = _source("docx/text.docx", second_link=target)

    with pytest.raises(ReverseConversionError) as caught:
        document = compat.parse_document(source)
        compat.render_document(
            document,
            (None,) * len(compat.extract_asset_sources(document)),
        )

    assert caught.value.category is ReverseErrorCategory.MALFORMED


@pytest.mark.parametrize(
    ("relative_path", "expected_asset_id", "expected_media_type"),
    [
        ("docx/text.docx", "anydoc:0", "image/png"),
        ("epub/book.epub", "anydoc:0", "image/png"),
        ("odt/text.odt", "anydoc:0", "image/png"),
    ],
)
def test_extracts_embedded_bytes_at_source_positions(
    relative_path: str, expected_asset_id: str, expected_media_type: str
) -> None:
    document = compat.parse_document(_source(relative_path))

    occurrences = compat.extract_asset_sources(document)

    assert len(occurrences) == 1
    assert occurrences[0].asset_id == expected_asset_id
    assert occurrences[0].declared_media_type == expected_media_type
    assert occurrences[0].source is not None
    assert occurrences[0].source.startswith(b"\x89PNG\r\n\x1a\n")


def test_injects_a_normalized_path_at_the_exact_image_inline() -> None:
    document = compat.parse_document(_source("docx/text.docx"))

    rendered = compat.render_document(
        document, (PurePosixPath("assets/image-0001.png"),)
    )

    assert "Inline image: ![tiny red dot](assets/image-0001.png) done." in rendered
    assert rendered.count("assets/image-0001.png") == 1


def test_real_document_model_interoperates_with_asset_normalization() -> None:
    document = compat.parse_document(_source("docx/text.docx"))
    normalized = normalize_assets(
        compat.extract_asset_sources(document),
        ReverseAssetLimits(
            ImageLimits(20_000, 100, 100, 10_000, 100, 16),
            max_asset_count=2,
            max_total_source_bytes=20_000,
            max_total_output_bytes=20_000,
        ),
    )

    markdown = compat.render_document(
        document, tuple(reference.path for reference in normalized.references)
    )

    assert [asset.path.as_posix() for asset in normalized.assets] == [
        "assets/image-0001.png"
    ]
    assert markdown.count("![tiny red dot](assets/image-0001.png)") == 1


def test_rejects_unpinned_version_before_native_parse(mocker: Any) -> None:
    native = mocker.patch.object(anydoc, "to_document")
    mocker.patch.object(compat.importlib.metadata, "version", return_value="0.2.5")

    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_document(b"source", "docx")

    assert caught.value.category is ReverseErrorCategory.MALFORMED
    native.assert_not_called()


def test_rejects_an_unknown_model_surface(mocker: Any) -> None:
    model_type, fields = compat._MODEL_FIELDS[0]
    mocker.patch.object(
        compat,
        "_MODEL_FIELDS",
        ((model_type, fields | {"future_field"}), *compat._MODEL_FIELDS[1:]),
    )

    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_document(b"source", "docx")

    assert caught.value.category is ReverseErrorCategory.MALFORMED


@pytest.mark.parametrize(
    ("upstream_error", "category"),
    [
        (anydoc.UnsupportedError("private"), ReverseErrorCategory.UNSUPPORTED),
        (anydoc.MalformedError("private"), ReverseErrorCategory.MALFORMED),
        (anydoc.MissingPartError("private"), ReverseErrorCategory.MALFORMED),
        (anydoc.EncryptedError("private"), ReverseErrorCategory.ENCRYPTED),
        (anydoc.ResourceLimitError("private"), ReverseErrorCategory.RESOURCE_LIMIT),
        (anydoc.NeedsOcrError("private"), ReverseErrorCategory.NEEDS_OCR),
    ],
)
def test_maps_native_failures_without_upstream_details(
    mocker: Any, upstream_error: Exception, category: ReverseErrorCategory
) -> None:
    mocker.patch.object(anydoc, "to_document", side_effect=upstream_error)

    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_document(b"source", "docx")

    assert caught.value.category is category
    assert "private" not in str(caught.value)


def test_text_pdf_uses_local_renderer_without_document_or_hosted_fallback(
    mocker: Any,
) -> None:
    native = mocker.patch.object(anydoc, "to_document")
    hosted = mocker.patch.object(anydoc, "_parse_hosted")
    source = (CORPUS / "pdf/text.pdf").read_bytes()

    parsed = compat.parse_source(source, ".pdf")

    assert parsed.admission.detected_format == "pdf"
    assert parsed.document is None
    assert parsed.markdown == anydoc.to_markdown_bytes(source, "pdf", ocr="reject")
    native.assert_not_called()
    hosted.assert_not_called()


def test_scanned_pdf_fails_as_needs_ocr_without_hosted_fallback(mocker: Any) -> None:
    hosted = mocker.patch.object(anydoc, "_parse_hosted")
    source = (CORPUS / "pdf/handmade-scanned.pdf").read_bytes()

    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_source(source, ".pdf")

    assert caught.value.category is ReverseErrorCategory.NEEDS_OCR
    hosted.assert_not_called()


def test_child_detection_rejects_extension_mismatch_before_parse(mocker: Any) -> None:
    native = mocker.spy(anydoc, "to_document")
    source = _source("docx/text.docx")

    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_source(source, ".pdf")

    assert caught.value.category is ReverseErrorCategory.UNSUPPORTED
    native.assert_not_called()


def test_child_detection_selects_parser_without_caller_authority(mocker: Any) -> None:
    native = mocker.spy(anydoc, "to_document")
    source = (CORPUS / "docm/generated.docm").read_bytes()

    parsed = compat.parse_source(source, ".docm")

    assert parsed.admission.detected_format == "docx"
    assert parsed.admission.parser_format == "docx"
    assert parsed.document is not None
    native.assert_called_once_with(source, "docx")


def test_csv_requires_child_local_strict_text_validation(mocker: Any) -> None:
    native = mocker.spy(anydoc, "to_document")
    source = (CORPUS / "csv/sheet.csv").read_bytes()

    parsed = compat.parse_source(source, ".CSV")

    assert parsed.admission.parser_format == "csv"
    assert parsed.document is not None
    native.assert_called_once_with(source, "csv")
    with pytest.raises(ReverseConversionError) as caught:
        compat.parse_source(b"one,two\n\xff", ".csv")
    assert caught.value.category is ReverseErrorCategory.UNSUPPORTED


@pytest.mark.parametrize(
    "paths",
    [
        (),
        (PurePosixPath("../escape.png"),),
        (PurePosixPath("assets/not-normalized.jpg"),),
        (PurePosixPath("assets/image-invalid.png"),),
        (
            PurePosixPath("assets/image-0001.png"),
            PurePosixPath("assets/image-0002.png"),
        ),
    ],
)
def test_rejects_missing_extra_or_unsafe_image_hooks(
    paths: tuple[PurePosixPath, ...],
) -> None:
    document = compat.parse_document(_source("docx/text.docx"))

    with pytest.raises(ReverseConversionError) as caught:
        compat.render_document(document, paths)

    assert caught.value.category is ReverseErrorCategory.MALFORMED


def test_inventory_and_license_bind_the_exact_upstream_surface() -> None:
    license_path = Path(compat.__file__).with_name("ANYDOC_COMPAT_LICENSE.txt")

    assert compat.PINNED_ANYDOC_VERSION == "0.2.4"
    assert compat.UPSTREAM_ANYDOC_COMMIT == "42bf1c5ecdde9eb0d96d6bd75a9e6698cf93b14c"
    assert len(compat.UPSTREAM_RENDERER_SURFACES) == 21
    assert len(set(compat.UPSTREAM_RENDERER_SURFACES)) == 21
    assert all(
        surface.startswith("src/render/markdown/")
        for surface in compat.UPSTREAM_RENDERER_SURFACES
    )
    assert "Copyright (c) 2026 Sideguide Technologies Inc." in license_path.read_text()
