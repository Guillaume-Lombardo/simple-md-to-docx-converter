"""Reusable reference-corpus and golden-comparison test infrastructure."""

from tests.golden.corpus import (
    ArchiveInspectionLimits,
    CorpusCase,
    CorpusManifest,
    CorpusManifestError,
    build_case_bytes,
    inspect_archive_fixture,
    materialize_case,
    read_manifest,
)
from tests.golden.openxml import (
    DocxSnapshot,
    OpenXmlError,
    compare_docx,
    inspect_docx,
)
from tests.golden.raster import (
    RasterComparison,
    RasterPage,
    RasterTolerance,
    compare_pdf_rasters,
)

__all__ = [
    "ArchiveInspectionLimits",
    "CorpusCase",
    "CorpusManifest",
    "CorpusManifestError",
    "DocxSnapshot",
    "OpenXmlError",
    "RasterComparison",
    "RasterPage",
    "RasterTolerance",
    "build_case_bytes",
    "compare_docx",
    "compare_pdf_rasters",
    "inspect_archive_fixture",
    "inspect_docx",
    "materialize_case",
    "read_manifest",
]
