"""Focused coverage for the final-image reverse corpus driver."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from tests.e2e import reverse_corpus_workflow as workflow
from tests.e2e.service_workflow import HttpResult, WorkflowFailure

pytestmark = pytest.mark.unit


def _case(*, assets: bool = False) -> workflow.CorpusCase:
    return workflow.CorpusCase(
        "word",
        "docx/text.docx",
        "word",
        "docx",
        minimum_headings=1,
        minimum_tables=1,
        minimum_bullets=1,
        minimum_links=1,
        expects_assets=assets,
    )


def _package(*, orphan: bool = False, signature: bytes | None = None) -> bytes:
    case = _case(assets=True)
    markdown = b"# Heading\n\n| A |\n| - |\n\n- Item\n\n![image](assets/image-0001.png)\n\n[link](https://example.test)\n"
    asset = signature if signature is not None else workflow._PNG_SIGNATURE + b"image"
    asset_names = ["assets/image-0001.png"]
    if orphan:
        asset_names.append("assets/image-0002.png")
    manifest = workflow._expected_manifest(
        case,
        mode="markdown_with_assets",
        asset_count=len(asset_names),
        asset_bytes=len(asset) * len(asset_names),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        entries = [
            ("document.md", markdown),
            *((name, asset) for name in asset_names),
            (
                "manifest.json",
                (json.dumps(manifest, separators=(",", ":")) + "\n").encode(),
            ),
        ]
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def test_reviewed_manifest_covers_exactly_eight_success_families() -> None:
    sources = workflow.load_reviewed_sources()

    assert {case.family for case in workflow.SUCCESS_CASES} == {
        "word",
        "powerpoint",
        "excel",
        "opendocument",
        "rtf",
        "epub",
        "csv",
        "pdf",
    }
    assert {case.relative_path for case in workflow.SUCCESS_CASES} <= set(sources)
    assert {case.label for case in workflow.SUCCESS_CASES if case.repeat} == {
        "word",
        "csv",
    }
    formats = {case.label: case.api_detected_format for case in workflow.SUCCESS_CASES}
    assert formats["csv"] is None
    assert formats["word"] == "docx"


def test_reviewed_manifest_rejects_changed_fixture_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "fixture.csv"
    source.write_bytes(b"changed")
    manifest = corpus / "manifest.json"
    manifest.write_text(
        json.dumps({"files": [{"path": "fixture.csv", "size": 1, "sha256": "0" * 64}]}),
        encoding="utf-8",
    )
    case = workflow.CorpusCase("csv", "fixture.csv", "csv", "csv")
    monkeypatch.setattr(workflow, "CORPUS", corpus)
    monkeypatch.setattr(workflow, "MANIFEST", manifest)
    monkeypatch.setattr(workflow, "SUCCESS_CASES", (case,))
    monkeypatch.setattr(workflow, "FAILURE_CASES", ())

    with pytest.raises(WorkflowFailure, match="identity differs"):
        workflow.load_reviewed_sources()


@pytest.mark.parametrize(
    "relative_path",
    ("docx/text.docx", "odt/text.odt", "rtf/text.rtf"),
)
def test_safe_source_removes_only_reviewed_local_link(relative_path: str) -> None:
    original = (workflow.CORPUS / relative_path).read_bytes()
    normalized = workflow.safe_source(relative_path, original)

    assert normalized != original
    if relative_path.endswith((".docx", ".odt")):
        member = (
            "word/_rels/document.xml.rels"
            if relative_path.endswith(".docx")
            else "content.xml"
        )
        with zipfile.ZipFile(io.BytesIO(normalized)) as archive:
            inspected = archive.read(member)
    else:
        inspected = normalized
    assert workflow._SAFE_LINK in inspected
    assert b"fixture-src/sibling.odt" not in inspected
    assert (workflow.CORPUS / relative_path).read_bytes() == original


def test_asset_boundary_cases_cover_rejections_and_sanitized_svg() -> None:
    cases = workflow.asset_boundary_cases(workflow._PNG_SIGNATURE + b"source")

    assert {case.label: case.expected_code for case in cases} == {
        "asset-non-image": "asset_invalid",
        "asset-mismatched": "asset_invalid",
        "asset-polyglot": "asset_invalid",
        "asset-animated": "asset_invalid",
        "asset-hostile-svg": None,
    }
    animated = next(case for case in cases if case.label == "asset-animated")
    assert animated.extension == "gif"
    assert animated.media_type == "image/gif"
    with Image.open(io.BytesIO(animated.payload)) as image:
        assert getattr(image, "n_frames", 1) == 2


def test_replace_docx_image_updates_payload_and_declarations() -> None:
    source = workflow.safe_source(
        "docx/text.docx", (workflow.CORPUS / "docx/text.docx").read_bytes()
    )
    case = workflow.AssetBoundaryCase(
        "asset-hostile-svg",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
        "svg",
        "image/svg+xml",
        None,
    )

    mutated = workflow.replace_docx_image(source, case)

    with zipfile.ZipFile(io.BytesIO(mutated)) as archive:
        assert "word/media/image1.png" not in archive.namelist()
        assert archive.read("word/media/image1.svg") == case.payload
        relationships = archive.read("word/_rels/document.xml.rels")
        content_types = archive.read("[Content_Types].xml")
    assert b"media/image1.svg" in relationships
    assert b"media/image1.png" not in relationships
    assert b'Extension="svg" ContentType="image/svg+xml"' in content_types
    assert (
        b'PartName="/word/media/image1.svg" ContentType="image/svg+xml"'
        in content_types
    )


def test_markdown_and_package_validation_inspect_structure_and_assets() -> None:
    workflow.validate_package(_case(assets=True), _package())
    markdown = b"# Heading\n\n| A |\n| - |\n\n- Item\n\n[link](https://example.test)\n"
    assert workflow._validate_markdown(_case(), markdown).startswith("# Heading")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (_package(orphan=True), "asset references differ"),
        (_package(signature=b"not-png"), "asset signature differs"),
    ],
)
def test_package_validation_rejects_orphans_and_wrong_signatures(
    content: bytes, message: str
) -> None:
    with pytest.raises(WorkflowFailure, match=message):
        workflow.validate_package(_case(assets=True), content)


def test_success_result_requires_private_headers_and_exact_size(mocker) -> None:
    client = mocker.Mock()
    package = _package()
    client.request.return_value = HttpResult(
        200,
        {
            "cache-control": "private, no-store",
            "x-content-type-options": "nosniff",
            "content-type": "application/zip",
            "content-disposition": 'attachment; filename="word.zip"',
        },
        package,
        (),
    )
    job = {
        "id": "job",
        "state": "succeeded",
        "source_family": "word",
        "source_extension": ".docx",
        "detected_format": "docx",
        "result_mode": "markdown_with_assets",
        "result_size": len(package),
        "error_code": None,
        "error_message": None,
    }

    assert workflow.validate_success_result(client, _case(assets=True), job) == package
    client.request.return_value = HttpResult(
        200,
        {**client.request.return_value.headers, "cache-control": "public"},
        package,
        (),
    )
    with pytest.raises(WorkflowFailure, match="result header cache-control differs"):
        workflow.validate_success_result(client, _case(assets=True), job)


def test_csv_success_keeps_admission_detection_optional(mocker) -> None:
    client = mocker.Mock()
    markdown = b"| A |\n| - |\n| value |\n"
    client.request.return_value = HttpResult(
        200,
        {
            "cache-control": "private, no-store",
            "x-content-type-options": "nosniff",
            "content-type": "text/markdown; charset=utf-8",
            "content-disposition": 'attachment; filename="csv.md"',
        },
        markdown,
        (),
    )
    case = next(case for case in workflow.SUCCESS_CASES if case.label == "csv")
    job = {
        "id": "job",
        "state": "succeeded",
        "source_family": "csv",
        "source_extension": ".csv",
        "detected_format": None,
        "result_mode": "markdown",
        "result_size": len(markdown),
        "error_code": None,
        "error_message": None,
    }

    assert workflow.validate_success_result(client, case, job) == markdown


def test_failure_validation_is_category_specific_and_result_free(mocker) -> None:
    client = mocker.Mock()
    client.request.return_value = HttpResult(
        409,
        {},
        b'{"error":{"code":"REVERSION_CONFLICT","message":"safe"}}',
        (),
    )
    job = {
        "id": "job",
        "state": "failed",
        "error_code": "needs_ocr",
        "error_message": "OCR is unavailable.",
        "result_mode": None,
        "result_size": None,
    }

    workflow.validate_failure(
        client, label="scanned-pdf", job=job, expected_code="needs_ocr"
    )
    with pytest.raises(WorkflowFailure, match="terminal field error_code differs"):
        workflow.validate_failure(
            client, label="scanned-pdf", job=job, expected_code="malformed"
        )
    client.request.return_value = HttpResult(
        409,
        {},
        b'{"error":{"code":"OTHER_CONFLICT","message":"safe"}}',
        (),
    )
    with pytest.raises(WorkflowFailure, match="unavailable result code differs"):
        workflow.validate_failure(
            client, label="scanned-pdf", job=job, expected_code="needs_ocr"
        )
