"""Generate the locked T11 PDF raster golden inside the approved toolchain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

from PIL import Image

from md_converter.conversion.libreoffice import (
    LibreOfficeConfig,
    LibreOfficePdfConverter,
    PdfLimits,
    PdfTraceabilityContext,
)
from md_converter.conversion.pandoc import PandocConfig, PandocDocxConverter
from md_converter.conversion.validation import ApprovedMarkdown
from md_converter.version import VERSION
from tests.golden.limits import RasterLimits
from tests.golden.pdf import render_pdf
from tests.golden.reference import normalize_reference_docx

LIBREOFFICE_VERSION = "LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb"
PANDOC_VERSION = "3.10.2"
PDFIUM_VERSION = "5.13.0"
DPI = 96
PDF_LIMITS = PdfLimits(
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
RASTER_LIMITS = RasterLimits(20, 5_000_000, 20_000_000)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def generate(output: Path, workspace: Path) -> None:
    """Generate deterministic PNG pages and their provenance manifest."""

    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("The golden output directory must be empty")
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    markdown_path = Path("tests/corpus/pdf/document.md")
    markdown = markdown_path.read_bytes()
    pandoc = shutil.which("pandoc")
    soffice = shutil.which("soffice")
    if pandoc is None or soffice is None:
        raise RuntimeError("The approved Pandoc and LibreOffice engines are required")
    detected_pandoc = subprocess.run(  # noqa: S603 - approved engine from PATH
        [pandoc, "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.splitlines()[0]
    detected_libreoffice = subprocess.run(  # noqa: S603 - approved engine from PATH
        [soffice, "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.strip()
    detected_pdfium = version("pypdfium2")
    if detected_pandoc != f"pandoc {PANDOC_VERSION}":
        raise RuntimeError("The approved Pandoc version is required")
    if detected_libreoffice != LIBREOFFICE_VERSION:
        raise RuntimeError("The approved LibreOffice version is required")
    if detected_pdfium != PDFIUM_VERSION:
        raise RuntimeError("The approved PDFium version is required")
    reference = normalize_reference_docx(
        subprocess.run(  # noqa: S603 - approved engine resolved from PATH
            [pandoc, "--print-default-data-file", "reference.docx"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
    )
    docx = PandocDocxConverter(
        PandocConfig(pandoc, 30.0, 2.0, workspace), os.environ
    ).convert(ApprovedMarkdown(markdown.decode("utf-8")), reference)
    font_manifest = Path("spikes/toolchain/fonts/manifest.json").read_bytes()
    trace = PdfTraceabilityContext(
        application_version=VERSION,
        conversion_contract_version="1",
        template_id="t11-golden-template",
        template_version="1",
        template_sha256=_sha256(reference),
        pandoc_version=PANDOC_VERSION,
        pandoc_reader=(
            "commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html"
        ),
        mermaid_version="11.16.0",
        chromium_version="151.0.7922.173",
        font_manifest_sha256=_sha256(font_manifest),
    )
    artifact = LibreOfficePdfConverter(
        LibreOfficeConfig(soffice, LIBREOFFICE_VERSION, 30.0, 2.0, 0.05, workspace),
        PDF_LIMITS,
        os.environ,
    ).convert(docx, trace)
    pages = render_pdf(
        artifact.pdf,
        dpi=DPI,
        max_pdf_bytes=PDF_LIMITS.max_pdf_bytes,
        limits=RASTER_LIMITS,
    )
    page_metadata = []
    for index, page in enumerate(pages, start=1):
        filename = f"page-{index}.png"
        image = Image.frombytes("RGBA", (page.width, page.height), page.pixels_rgba)
        image.save(output / filename, format="PNG", compress_level=9, optimize=False)
        payload = (output / filename).read_bytes()
        page_metadata.append(
            {
                "filename": filename,
                "height": page.height,
                "sha256": _sha256(payload),
                "width": page.width,
            }
        )
    manifest = {
        "dpi": DPI,
        "font_manifest_sha256": _sha256(font_manifest),
        "libreoffice_version": LIBREOFFICE_VERSION,
        "pages": page_metadata,
        "pandoc_version": PANDOC_VERSION,
        "pdfium_version": PDFIUM_VERSION,
        "reference_docx_sha256": _sha256(reference),
        "schema_version": 1,
        "source_markdown_sha256": _sha256(markdown),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("workspace", type=Path)
    arguments = parser.parse_args()
    generate(arguments.output, arguments.workspace)


if __name__ == "__main__":
    main()
