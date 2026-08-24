"""Tests for deterministic reference DOCX ZIP normalization."""

from __future__ import annotations

import io
import zipfile

import pytest

from tests.golden.reference import normalize_reference_docx

pytestmark = pytest.mark.unit


def _archive(timestamp: tuple[int, int, int, int, int, int]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in ("word/document.xml", "[Content_Types].xml"):
            info = zipfile.ZipInfo(name, timestamp)
            archive.writestr(info, name.encode())
    return output.getvalue()


def test_reference_normalization_removes_zip_time_and_order_variance() -> None:
    first = normalize_reference_docx(_archive((2025, 1, 1, 0, 0, 0)))
    second = normalize_reference_docx(_archive((2026, 2, 2, 2, 2, 2)))
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ["[Content_Types].xml", "word/document.xml"]
        assert {member.date_time for member in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }
