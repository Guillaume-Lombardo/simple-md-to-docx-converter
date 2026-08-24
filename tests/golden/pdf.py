"""Bounded PDFium rasterization for deterministic PDF golden tests."""

from __future__ import annotations

import math
from typing import NoReturn

import pypdfium2 as pdfium

from tests.golden.limits import RasterLimits
from tests.golden.raster import RasterPage


class PdfRasterError(ValueError):
    """A PDF could not be rasterized safely within caller-supplied limits."""


def _invalid(message: str) -> NoReturn:
    raise PdfRasterError(message)


def _dimensions(page: pdfium.PdfPage, dpi: int) -> tuple[int, int]:
    width_points, height_points = page.get_size()
    if not all(
        math.isfinite(value) and value > 0 for value in (width_points, height_points)
    ):
        _invalid("PDF page dimensions are invalid")
    scale = dpi / 72
    return math.ceil(width_points * scale), math.ceil(height_points * scale)


def _validate_input(data: bytes, dpi: int, max_pdf_bytes: int) -> None:
    if type(dpi) is not int or dpi <= 0 or type(max_pdf_bytes) is not int:
        _invalid("PDF raster DPI and byte limit must be positive integers")
    if max_pdf_bytes <= 0:
        _invalid("PDF raster DPI and byte limit must be positive integers")
    if type(data) is not bytes or not data or len(data) > max_pdf_bytes:
        _invalid("PDF bytes are empty, invalid, or exceed the configured limit")


def render_pdf(
    data: bytes, *, dpi: int, max_pdf_bytes: int, limits: RasterLimits
) -> tuple[RasterPage, ...]:
    """Render every page as RGBA after byte, page, and pixel preflight checks."""

    _validate_input(data, dpi, max_pdf_bytes)
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise PdfRasterError("PDFium could not open the PDF") from error
    with document:
        page_count = len(document)
        if not 0 < page_count <= limits.max_pages:
            _invalid("PDF page count exceeds the raster limit")
        pages: list[RasterPage] = []
        total_pixels = 0
        for index in range(page_count):
            try:
                page = document[index]
            except pdfium.PdfiumError as error:
                raise PdfRasterError("PDFium could not load a PDF page") from error
            try:
                width, height = _dimensions(page, dpi)
                page_pixels = width * height
                total_pixels += page_pixels
                if page_pixels > limits.max_pixels_per_page:
                    _invalid("PDF page exceeds the raster pixel limit")
                if total_pixels > limits.max_total_pixels:
                    _invalid("PDF exceeds the total raster pixel limit")
                bitmap = page.render(scale=dpi / 72)
                try:
                    image = bitmap.to_pil().convert("RGBA")
                    if image.size != (width, height):
                        _invalid("PDFium returned unexpected raster dimensions")
                    pages.append(RasterPage(width, height, dpi, image.tobytes()))
                finally:
                    bitmap.close()
            except PdfRasterError:
                raise
            except (OSError, ValueError, pdfium.PdfiumError) as error:
                raise PdfRasterError("PDFium could not render the PDF") from error
            finally:
                page.close()
        return tuple(pages)
