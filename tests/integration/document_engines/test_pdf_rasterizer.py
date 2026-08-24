"""Real PDFium boundary coverage for bounded PDF golden rasterization."""

from __future__ import annotations

import io
from importlib.metadata import version
from typing import cast

import pytest
from pypdf import PdfWriter

from tests.golden.limits import RasterLimits
from tests.golden.pdf import PdfRasterError, render_pdf

pytestmark = pytest.mark.integration
LIMITS = RasterLimits(4, 2_000_000, 4_000_000)


def _pdf(*, pages: int = 1, width: float = 72, height: float = 36) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    writer.write(output)
    return output.getvalue()


def test_pdfium_version_is_locked_and_rgba_render_is_deterministic() -> None:
    assert version("pypdfium2") == "5.13.0"
    first = render_pdf(_pdf(), dpi=144, max_pdf_bytes=100_000, limits=LIMITS)
    second = render_pdf(_pdf(), dpi=144, max_pdf_bytes=100_000, limits=LIMITS)
    assert first == second
    assert (first[0].width, first[0].height, first[0].dpi) == (144, 72, 144)
    assert set(first[0].pixels_rgba) == {255}


@pytest.mark.parametrize(
    ("data", "dpi", "max_pdf_bytes", "limits", "message"),
    (
        (b"invalid", 72, 100, LIMITS, "could not open"),
        (_pdf(), 0, 100_000, LIMITS, "positive integers"),
        (_pdf(), 72, cast(int, "invalid"), LIMITS, "positive integers"),
        (_pdf(), 72, 1, LIMITS, "exceed"),
        (_pdf(pages=2), 72, 100_000, RasterLimits(1, 10_000, 10_000), "page count"),
        (_pdf(width=200, height=200), 72, 100_000, RasterLimits(1, 10, 100), "page"),
        (_pdf(pages=2), 72, 100_000, RasterLimits(2, 10_000, 5_000), "total"),
    ),
)
def test_pdfium_rasterization_fails_closed_at_every_limit(
    data: bytes,
    dpi: int,
    max_pdf_bytes: int,
    limits: RasterLimits,
    message: str,
) -> None:
    with pytest.raises(PdfRasterError, match=message):
        render_pdf(data, dpi=dpi, max_pdf_bytes=max_pdf_bytes, limits=limits)
