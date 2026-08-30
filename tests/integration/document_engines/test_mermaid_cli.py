"""Real Mermaid CLI, Chromium, Cairo, Pandoc, and OpenXML integration tests."""

from __future__ import annotations

import io
import os
import subprocess
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image

from markweave.conversion.errors import ConversionError, ConversionErrorCode
from markweave.conversion.images import ImageLimits
from markweave.conversion.mermaid import (
    MermaidCliRenderer,
    MermaidConfig,
    MermaidLimits,
    MermaidPreprocessingConverter,
    render_mermaid,
)
from markweave.conversion.pandoc import PandocConfig, PandocDocxConverter
from markweave.conversion.service import DocxConversionService
from markweave.conversion.validation import ApprovedMarkdown

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_mermaid,
]

IMAGE_LIMITS = ImageLimits(1_000_000, 2_000, 2_000, 4_000_000, 10_000, 64)
MERMAID_LIMITS = MermaidLimits(5, 100_000, 500_000, 1_000_000, 5_000_000, 640, 480)
WORDPROCESSING_DRAWING_NAMESPACE = (
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
)


def _renderer(timeout: float = 30.0) -> MermaidCliRenderer:
    return MermaidCliRenderer(
        MermaidConfig(
            os.environ.get("MARKWEAVE_TEST_MMDC", "mmdc"),
            os.environ.get("MARKWEAVE_TEST_CHROMIUM", "/usr/bin/google-chrome-stable"),
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


def _fixture_renderer(
    tmp_path: Path, program: str, *, timeout: float = 1.0
) -> MermaidCliRenderer:
    executable = tmp_path / "mmdc-fixture"
    executable.write_text(f"#!/usr/bin/env python3\n{program}\n", encoding="utf-8")
    executable.chmod(0o700)
    return MermaidCliRenderer(
        MermaidConfig(
            str(executable),
            "/unused/chrome",
            timeout,
            0.2,
            800,
            600,
            tmp_path,
        ),
        os.environ,
    )


def _assert_process_gone(process_id: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = Path(f"/proc/{process_id}/stat")
        try:
            state = status.read_text(encoding="utf-8").split()[2]
        except FileNotFoundError:
            return
        if state == "Z":
            return
        time.sleep(0.02)
    pytest.fail(f"fixture descendant {process_id} survived Mermaid cleanup")


def _png(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(output, format="PNG")
    return output.getvalue()


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


@pytest.mark.parametrize(
    ("program", "output_limit", "expected_code"),
    [
        ("raise SystemExit(7)", 100, ConversionErrorCode.MERMAID_FAILURE),
        ("pass", 100, ConversionErrorCode.INVALID_MERMAID_OUTPUT),
        (
            "from pathlib import Path; import sys; "
            "output = Path(sys.argv[sys.argv.index('--output') + 1]); "
            "output.symlink_to('/dev/null')",
            100,
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
        ),
        (
            "from pathlib import Path; import sys; "
            "output = Path(sys.argv[sys.argv.index('--output') + 1]); "
            "output.write_bytes(b'x' * 101)",
            100,
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
        ),
    ],
)
def test_real_process_boundary_normalizes_failures(
    tmp_path: Path,
    program: str,
    output_limit: int,
    expected_code: ConversionErrorCode,
) -> None:
    with pytest.raises(ConversionError) as captured:
        _fixture_renderer(tmp_path, program).render("secret", output_limit)
    assert captured.value.code is expected_code
    assert "secret" not in str(captured.value)


def test_real_process_boundary_reports_unavailable_engine(tmp_path: Path) -> None:
    renderer = MermaidCliRenderer(
        MermaidConfig(
            str(tmp_path / "absent-mmdc"),
            "/unused/chrome",
            1.0,
            0.2,
            800,
            600,
            tmp_path,
        ),
        os.environ,
    )
    with pytest.raises(ConversionError) as captured:
        renderer.render("secret", 100)
    assert captured.value.code is ConversionErrorCode.MERMAID_UNAVAILABLE


def test_real_timeout_terminates_process_group_descendants(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    program = (
        "from pathlib import Path; import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)']); "
        f"Path({str(child_pid)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )
    with pytest.raises(ConversionError) as captured:
        _fixture_renderer(tmp_path, program, timeout=0.2).render("secret", 100)
    assert captured.value.code is ConversionErrorCode.MERMAID_TIMEOUT
    _assert_process_gone(int(child_pid.read_text(encoding="utf-8")))


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


@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    ("image_size", "expected_extent"),
    [
        ((800, 400), (120 * 9_525, 60 * 9_525)),
        ((400, 800), (40 * 9_525, 80 * 9_525)),
    ],
)
def test_pandoc_preserves_ratio_and_physical_dimension_caps(
    tmp_path: Path,
    image_size: tuple[int, int],
    expected_extent: tuple[int, int],
) -> None:
    class StaticRenderer:
        def render(
            self,
            source: str,
            max_output_bytes: int,
            *,
            deadline_monotonic: float | None = None,
            cancellation_requested: Callable[[], bool] | None = None,
        ) -> bytes:
            assert source == "flowchart LR\n"
            assert deadline_monotonic is None
            assert cancellation_requested is None
            output = _png(*image_size)
            assert len(output) <= max_output_bytes
            return output

    limits = MermaidLimits(1, 100, 100, 1_000_000, 1_000_000, 120, 80)
    converter = MermaidPreprocessingConverter(
        PandocDocxConverter(PandocConfig("pandoc", 30.0, 2.0, tmp_path), os.environ),
        StaticRenderer(),
        limits,
        IMAGE_LIMITS,
    )
    result = DocxConversionService(converter).convert(
        "```mermaid\nflowchart LR\n```", _default_reference_docx()
    )

    with zipfile.ZipFile(io.BytesIO(result)) as document:
        root = ElementTree.fromstring(  # noqa: S314 - generated OpenXML
            document.read("word/document.xml")
        )
    extent = next(root.iter(f"{{{WORDPROCESSING_DRAWING_NAMESPACE}}}extent"), None)
    assert extent is not None
    assert (int(extent.attrib["cx"]), int(extent.attrib["cy"])) == expected_extent


def test_real_mermaid_versions_match_t00_artifacts() -> None:
    mmdc = subprocess.run(
        [os.environ.get("MARKWEAVE_TEST_MMDC", "mmdc"), "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    chrome = subprocess.run(
        [
            os.environ.get("MARKWEAVE_TEST_CHROMIUM", "/usr/bin/google-chrome-stable"),
            "--version",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert mmdc == "11.16.0"
    assert chrome == "Google Chrome 151.0.7922.173"
