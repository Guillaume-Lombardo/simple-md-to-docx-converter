"""Real LibreOffice and process-boundary integration coverage for T11."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path

import pytest
from PIL import Image

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.libreoffice import (
    LibreOfficeConfig,
    LibreOfficePdfConverter,
    PdfArtifact,
    PdfLimits,
    PdfTraceabilityContext,
)
from md_converter.conversion.pandoc import PandocConfig, PandocDocxConverter
from md_converter.conversion.validation import ApprovedMarkdown
from tests.golden.limits import RasterLimits
from tests.golden.pdf import render_pdf
from tests.golden.raster import RasterPage, RasterTolerance, compare_pdf_rasters
from tests.golden.reference import normalize_reference_docx

pytestmark = pytest.mark.integration
LIBREOFFICE_VERSION = "LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb"
LIMITS = PdfLimits(
    max_docx_bytes=20_000_000,
    max_docx_entries=2_000,
    max_docx_member_uncompressed_bytes=10_000_000,
    max_docx_total_uncompressed_bytes=50_000_000,
    max_docx_compression_ratio=200.0,
    max_pdf_bytes=20_000_000,
    max_pdf_decoded_stream_bytes=20_000_000,
    max_pages=20,
    max_pdf_objects=100_000,
    max_pdf_object_depth=100,
)
GOLDEN_RASTER_LIMITS = RasterLimits(20, 5_000_000, 20_000_000)


def _workspace(tmp_path: Path) -> Path:
    root = Path(os.environ.get("ENGINE_FIXTURE_ROOT", str(tmp_path.parent)))
    workspace = root / tmp_path.name
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    return workspace


def _trace(reference: bytes) -> PdfTraceabilityContext:
    font_manifest = Path("spikes/toolchain/fonts/manifest.json").read_bytes()
    return PdfTraceabilityContext(
        application_version="0.1.0",
        conversion_contract_version="1",
        template_id="t11-integration-template",
        template_version="1",
        template_sha256=hashlib.sha256(reference).hexdigest(),
        pandoc_version="3.10.2",
        pandoc_reader=(
            "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
        ),
        mermaid_version="11.16.0",
        chromium_version="151.0.7922.173",
        font_manifest_sha256=hashlib.sha256(font_manifest).hexdigest(),
    )


def _reference_docx() -> bytes:
    return normalize_reference_docx(
        subprocess.run(
            ["pandoc", "--print-default-data-file", "reference.docx"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
    )


def _docx(
    workspace: Path, reference: bytes, markdown: str = "# PDF\n\nSafe text."
) -> bytes:
    converter = PandocDocxConverter(
        PandocConfig("pandoc", 30.0, 2.0, workspace), os.environ
    )
    return converter.convert(ApprovedMarkdown(markdown), reference)


def _converter(
    workspace: Path,
    *,
    executable: str = "soffice",
    timeout: float = 30.0,
    limits: PdfLimits = LIMITS,
) -> LibreOfficePdfConverter:
    return LibreOfficePdfConverter(
        LibreOfficeConfig(
            executable, LIBREOFFICE_VERSION, timeout, 0.3, 0.02, workspace
        ),
        limits,
        os.environ,
    )


def _executable(workspace: Path, name: str, program: str) -> Path:
    path = workspace / name
    path.write_text(f"#!/usr/bin/env python3\n{program}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.mark.requires_libreoffice
def test_real_libreoffice_version_is_exactly_approved() -> None:
    completed = subprocess.run(
        ["soffice", "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert completed.stdout.strip() == LIBREOFFICE_VERSION


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_real_libreoffice_converts_docx_with_isolated_traceability(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    reference = _reference_docx()
    artifact = _converter(workspace).convert(
        _docx(workspace, reference), _trace(reference)
    )
    assert artifact.pdf.startswith(b"%PDF-")
    assert artifact.pdf.rstrip().endswith(b"%%EOF")
    assert artifact.manifest.libreoffice_version == LIBREOFFICE_VERSION
    assert artifact.manifest.template_sha256 == hashlib.sha256(reference).hexdigest()
    assert artifact.manifest.pages
    assert not tuple(workspace.glob("md-converter-pdf-*"))


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_concurrent_real_conversions_use_unique_profiles(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    reference = _reference_docx()
    docx = _docx(workspace, reference)
    converter = _converter(workspace)

    def convert_once(_: int) -> PdfArtifact:
        return converter.convert(docx, _trace(reference))

    with ThreadPoolExecutor(max_workers=2) as executor:
        artifacts = tuple(executor.map(convert_once, range(2)))
    assert all(artifact.manifest.pages for artifact in artifacts)
    assert not tuple(workspace.glob("md-converter-pdf-*"))


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_real_pdf_matches_locked_structural_raster_golden(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    reference = _reference_docx()
    markdown_path = Path("tests/corpus/pdf/document.md")
    markdown = markdown_path.read_bytes()
    artifact = _converter(workspace).convert(
        _docx(workspace, reference, markdown.decode("utf-8")), _trace(reference)
    )
    golden_root = Path("tests/corpus/pdf/golden")
    manifest = json.loads((golden_root / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {
        "dpi",
        "font_manifest_sha256",
        "libreoffice_version",
        "pages",
        "pandoc_version",
        "pdfium_version",
        "reference_docx_sha256",
        "schema_version",
        "source_markdown_sha256",
    }
    assert manifest["schema_version"] == 1
    assert manifest["source_markdown_sha256"] == hashlib.sha256(markdown).hexdigest()
    assert manifest["reference_docx_sha256"] == hashlib.sha256(reference).hexdigest()
    assert (
        manifest["font_manifest_sha256"]
        == hashlib.sha256(
            Path("spikes/toolchain/fonts/manifest.json").read_bytes()
        ).hexdigest()
    )
    assert manifest["libreoffice_version"] == LIBREOFFICE_VERSION
    assert manifest["pandoc_version"] == "3.10.2"
    assert manifest["pdfium_version"] == "5.13.0"
    dpi = manifest["dpi"]
    expected = []
    for page_metadata in manifest["pages"]:
        payload = (golden_root / page_metadata["filename"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == page_metadata["sha256"]
        with Image.open(io.BytesIO(payload)) as image:
            rgba = image.convert("RGBA")
            assert (rgba.width, rgba.height) == (
                page_metadata["width"],
                page_metadata["height"],
            )
            expected.append(RasterPage(rgba.width, rgba.height, dpi, rgba.tobytes()))
    actual = render_pdf(
        artifact.pdf,
        dpi=dpi,
        max_pdf_bytes=LIMITS.max_pdf_bytes,
        limits=GOLDEN_RASTER_LIMITS,
    )
    comparison = compare_pdf_rasters(
        tuple(expected),
        actual,
        RasterTolerance(0, 0.0, 0.0),
        GOLDEN_RASTER_LIMITS,
    )
    assert comparison.matches, comparison


@pytest.mark.parametrize(
    ("program", "limits", "expected"),
    (
        ("raise SystemExit(7)", LIMITS, ConversionErrorCode.PDF_FAILURE),
        ("raise SystemExit(0)", LIMITS, ConversionErrorCode.INVALID_PDF),
        (
            "from pathlib import Path; import sys; "
            "out=Path(sys.argv[sys.argv.index('--outdir')+1]); "
            "(out/'source.pdf').write_bytes(b'%PDF-1.7\\ninvalid\\n%%EOF\\n')",
            LIMITS,
            ConversionErrorCode.INVALID_PDF,
        ),
        (
            "from pathlib import Path; import sys; "
            "out=Path(sys.argv[sys.argv.index('--outdir')+1]); "
            "(out/'source.pdf').write_bytes(b'x'*1000)",
            PdfLimits(
                max_docx_bytes=20_000_000,
                max_docx_entries=2_000,
                max_docx_member_uncompressed_bytes=10_000_000,
                max_docx_total_uncompressed_bytes=50_000_000,
                max_docx_compression_ratio=200.0,
                max_pdf_bytes=100,
                max_pdf_decoded_stream_bytes=20_000_000,
                max_pages=20,
                max_pdf_objects=100_000,
                max_pdf_object_depth=100,
            ),
            ConversionErrorCode.PDF_LIMIT_EXCEEDED,
        ),
        (
            "from pathlib import Path; import sys; "
            "out=Path(sys.argv[sys.argv.index('--outdir')+1]); "
            "(out/'source.pdf').symlink_to('/etc/passwd')",
            LIMITS,
            ConversionErrorCode.INVALID_PDF,
        ),
    ),
)
def test_real_process_boundary_normalizes_output_and_exit_failures(
    tmp_path: Path,
    program: str,
    limits: PdfLimits,
    expected: ConversionErrorCode,
) -> None:
    workspace = _workspace(tmp_path)
    executable = _executable(workspace, "libreoffice-fixture", program)
    with pytest.raises(ConversionError) as captured:
        _converter(workspace, executable=str(executable), limits=limits).convert(
            _minimal_docx(), _trace(_minimal_docx())
        )
    assert captured.value.code is expected


def _minimal_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
            archive.writestr(name, b"safe")
    return output.getvalue()


@pytest.mark.parametrize("mode", ("timeout", "cancel", "probe-failure"))
def test_timeout_and_cancellation_kill_process_group_descendants(
    tmp_path: Path, mode: str
) -> None:
    workspace = _workspace(tmp_path)
    pid_file = workspace / f"descendant-{mode}.pid"
    ready_file = workspace / f"descendant-{mode}.ready"
    child_program = (
        "import signal,time,pathlib; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready_file)!r}).write_text('ready'); "
        "time.sleep(30)"
    )
    executable = _executable(
        workspace,
        f"libreoffice-descendant-{mode}",
        "import pathlib,subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_program!r}])\n"
        f"ready=pathlib.Path({str(ready_file)!r})\n"
        "while not ready.exists(): time.sleep(0.005)\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(30)",
    )

    def probe_failure() -> bool:
        if pid_file.exists():
            raise RuntimeError("sensitive")
        return False

    probes: dict[str, Callable[[], bool] | None] = {
        "timeout": None,
        "cancel": pid_file.exists,
        "probe-failure": probe_failure,
    }
    expected_codes = {
        "timeout": ConversionErrorCode.PDF_TIMEOUT,
        "cancel": ConversionErrorCode.PDF_CANCELLED,
        "probe-failure": ConversionErrorCode.PDF_FAILURE,
    }
    with pytest.raises(ConversionError) as captured:
        _converter(workspace, executable=str(executable), timeout=0.3).convert(
            _minimal_docx(), _trace(_minimal_docx()), probes[mode]
        )
    assert captured.value.code is expected_codes[mode]
    assert "sensitive" not in str(captured.value)
    descendant_pid = int(pid_file.read_text(encoding="utf-8"))

    def descendant_is_running() -> bool:
        try:
            state = Path(f"/proc/{descendant_pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            return False
        return state != "Z"

    deadline = time.monotonic() + 2.0
    try:
        while descendant_is_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not descendant_is_running()
    finally:
        if descendant_is_running():
            with suppress(ProcessLookupError):
                os.kill(descendant_pid, signal.SIGKILL)
