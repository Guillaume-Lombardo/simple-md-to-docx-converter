"""Real pinned-anydoc qualification and structured PPTX attempt boundaries."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import anydoc
import pytest
from PIL import Image

from markweave.reversions.attempt_main import _execute, convert_request
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseOutputMode,
)
from markweave.reversions.options import (
    PPTX_EXTRACTOR,
    ReverseExtraction,
    ReversionOptions,
)
from tests.pptx_fixtures import LIMITS, archive, parts, picture, relations, text_shape

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]
CORPUS = Path(__file__).parents[3] / "spikes/anydoc/corpus/pptx/pres.pptx"


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (32, 64, 128)).save(output, format="PNG")
    return output.getvalue()


def test_pinned_anydoc_qualification_boundaries_notes_and_embedded_assets() -> None:
    document = anydoc.to_document(CORPUS.read_bytes(), "pptx")
    assert document.notes == []
    assert sum(block.kind == "block_quote" for block in document.blocks) == 2
    assert all(block.kind != "rule" for block in document.blocks)
    source = archive(parts((text_shape("Image slide") + picture(),), image=_png()))
    image_document = anydoc.to_document(source, "pptx")
    assert len(image_document.assets) == 1
    assert image_document.assets[0].data == _png()
    assert image_document.assets[0].origin_part == "ppt/media/image.png"
    assert "Picture" in anydoc.to_markdown_bytes(source, "pptx")
    assert "![" not in anydoc.to_markdown_bytes(source, "pptx")


@pytest.mark.parametrize(
    "extraction", [ReverseExtraction.SLIDES, ReverseExtraction.MARP]
)
def test_actual_attempt_preserves_slide_order_notes_and_deterministic_safe_image_package(
    extraction: ReverseExtraction,
) -> None:
    source = archive(
        parts(
            (text_shape("First") + picture() + picture(), text_shape("Second")),
            notes=text_shape("Presenter note"),
            image=_png(),
        )
    )
    options = ReversionOptions(extraction)
    request = ReverseAttemptRequest(uuid4(), ".pptx", LIMITS, source, options)
    result = convert_request(request)
    assert result == convert_request(request)
    assert result.mode is ReverseOutputMode.MARKDOWN_WITH_ASSETS
    with zipfile.ZipFile(io.BytesIO(result.result)) as package:
        assert package.namelist() == [
            "document.md",
            "assets/image-0001.png",
            "manifest.json",
        ]
        markdown = package.read("document.md").decode()
        assert (
            markdown.index("First")
            < markdown.index("Presenter note")
            < markdown.index("Second")
        )
        assert markdown.count("](assets/image-0001.png)") == 2
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["schema_version"] == 1
        assert manifest["extractor"] == PPTX_EXTRACTOR
        assert manifest["engine"] == {"name": "firecrawl-anydoc", "version": "0.2.4"}
        assert manifest["result"]["asset_count"] == 1
        with Image.open(io.BytesIO(package.read("assets/image-0001.png"))) as image:
            assert image.size == (3, 2)


def test_actual_corpus_default_is_unchanged_and_structured_notes_can_be_disabled() -> (
    None
):
    source = CORPUS.read_bytes()
    default = convert_request(ReverseAttemptRequest(uuid4(), ".pptx", LIMITS, source))
    assert b"\n---\n" not in default.result
    assert b"> Speaker note" in default.result
    assert b"extractor" not in default.result
    structured = convert_request(
        ReverseAttemptRequest(
            uuid4(),
            ".pptx",
            LIMITS,
            source,
            ReversionOptions(ReverseExtraction.SLIDES, include_notes=False),
        )
    )
    assert structured.mode is ReverseOutputMode.MARKDOWN
    assert structured.result.count(b"\n---\n") == 1
    assert b"Speaker note" not in structured.result
    assert b"Inside a group shape" in structured.result


def test_unsupported_image_still_packages_unavailable_position() -> None:
    source = archive(
        parts((picture(),), image=b"unsupported metafile", image_type="image/x-emf")
    )
    result = convert_request(
        ReverseAttemptRequest(
            uuid4(),
            ".pptx",
            LIMITS,
            source,
            ReversionOptions(ReverseExtraction.SLIDES),
        )
    )
    assert result.mode is ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS
    with zipfile.ZipFile(io.BytesIO(result.result)) as package:
        assert package.namelist() == ["document.md", "manifest.json"]
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["result"]["unavailable_asset_count"] == 1
        assert manifest["extractor"] == PPTX_EXTRACTOR


@pytest.mark.parametrize("failure", ["xml", "external-image", "image-bytes", "budget"])
def test_actual_attempt_failure_is_stable_content_free_and_unpublished(
    failure: str,
) -> None:
    members = parts((text_shape("Private document body") + picture(),), image=_png())
    content_limits = LIMITS
    expected = ReverseErrorCategory.MALFORMED
    if failure == "xml":
        members["ppt/slides/s1.xml"] = b'<!DOCTYPE x [<!ENTITY e "private">]><x>&e;</x>'
    elif failure == "external-image":
        members["ppt/slides/_rels/s1.xml.rels"] = relations(
            (("image", "image", "https://example.test/private.png"),)
        ).replace(b"Target=", b'TargetMode="External" Target=')
        expected = ReverseErrorCategory.ASSET_INVALID
    elif failure == "image-bytes":
        members["ppt/media/image.png"] = b"malformed PNG"
        expected = ReverseErrorCategory.ASSET_INVALID
    else:
        content_limits = replace(LIMITS, max_pptx_xml_depth=1)
        expected = ReverseErrorCategory.RESOURCE_LIMIT
    attempt_id = uuid4()
    response = _execute(
        ReverseAttemptRequest(
            attempt_id,
            ".pptx",
            content_limits,
            archive(members),
            ReversionOptions(ReverseExtraction.SLIDES),
        )
    )
    assert response == ReverseAttemptFailure(attempt_id, expected)
    assert "Private" not in repr(response)
