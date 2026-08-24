"""Real Mermaid CLI, Chromium, Cairo, Pandoc, and OpenXML integration tests."""

from __future__ import annotations

import io
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.images import ImageLimits
from md_converter.conversion.mermaid import (
    MermaidCliRenderer,
    MermaidConfig,
    MermaidLimits,
    MermaidPreprocessingConverter,
    render_mermaid,
)
from md_converter.conversion.pandoc import PandocConfig, PandocDocxConverter
from md_converter.conversion.service import DocxConversionService
from md_converter.conversion.validation import ApprovedMarkdown

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_mermaid,
]

IMAGE_LIMITS = ImageLimits(1_000_000, 2_000, 2_000, 4_000_000, 10_000, 64)
MERMAID_LIMITS = MermaidLimits(5, 100_000, 500_000, 1_000_000, 5_000_000, 640, 480)


def _renderer(timeout: float = 30.0) -> MermaidCliRenderer:
    return MermaidCliRenderer(
        MermaidConfig(
            os.environ.get("MD_CONVERTER_TEST_MMDC", "mmdc"),
            os.environ.get(
                "MD_CONVERTER_TEST_CHROMIUM", "/usr/bin/google-chrome-stable"
            ),
            timeout,
            2.0,
            800,
            600,
        ),
        os.environ,
    )


def _default_reference_docx() -> bytes:
    return subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        capture_output=True,
    ).stdout


def test_real_mermaid_chromium_output_is_normalized_and_deterministic() -> None:
    source = "flowchart LR\n    Input[Markdown] --> Output[DOCX]\n"
    renderer = _renderer()

    first = render_mermaid(
        ApprovedMarkdown(f"```mermaid\n{source}```\n"),
        renderer,
        MERMAID_LIMITS,
        IMAGE_LIMITS,
    )
    second = render_mermaid(
        ApprovedMarkdown(f"```mermaid\n{source}```\n"),
        renderer,
        MERMAID_LIMITS,
        IMAGE_LIMITS,
    )

    first_png = first.resources[0].content
    assert first_png == second.resources[0].content
    with Image.open(io.BytesIO(first_png)) as image:
        assert image.format == "PNG"
        assert image.info == {}
        assert image.width <= IMAGE_LIMITS.max_width_pixels
        assert image.height <= IMAGE_LIMITS.max_height_pixels
        assert image.width * image.height <= IMAGE_LIMITS.max_pixels


def test_real_invalid_mermaid_maps_to_stable_failure() -> None:
    with pytest.raises(ConversionError) as captured:
        _renderer().render("flowchart LR\nA-->", MERMAID_LIMITS.max_output_bytes)
    assert captured.value.code is ConversionErrorCode.MERMAID_FAILURE
    assert str(captured.value) == "Mermaid rendering failed."


@pytest.mark.requires_pandoc
def test_real_mermaid_to_pandoc_pipeline_embeds_only_internal_png(
    tmp_path: Path,
) -> None:
    converter = MermaidPreprocessingConverter(
        PandocDocxConverter(PandocConfig("pandoc", 30.0, 2.0, tmp_path), os.environ),
        _renderer(),
        MERMAID_LIMITS,
        IMAGE_LIMITS,
    )
    service = DocxConversionService(converter)
    markdown = Path("tests/corpus/mermaid/diagram.md").read_text(encoding="utf-8")

    result = service.convert(markdown, _default_reference_docx())

    with zipfile.ZipFile(io.BytesIO(result)) as document:
        media = [name for name in document.namelist() if name.startswith("word/media/")]
        relationships = document.read("word/_rels/document.xml.rels")
        document_xml = document.read("word/document.xml")
        assert len(media) == 1
        assert document.read(media[0]).startswith(b"\x89PNG\r\n\x1a\n")
        assert b'TargetMode="External"' not in relationships
        assert b"flowchart LR" not in document_xml
        assert b"Mermaid fixture" in document_xml


def test_real_mermaid_versions_match_t00_artifacts() -> None:
    mmdc = subprocess.run(
        [os.environ.get("MD_CONVERTER_TEST_MMDC", "mmdc"), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    chrome = subprocess.run(
        [
            os.environ.get(
                "MD_CONVERTER_TEST_CHROMIUM", "/usr/bin/google-chrome-stable"
            ),
            "--version",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert mmdc == "11.16.0"
    assert chrome == "Google Chrome 151.0.7922.173"
