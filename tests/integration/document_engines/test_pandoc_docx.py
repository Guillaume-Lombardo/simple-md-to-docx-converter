"""Real subprocess and Pandoc integration tests for T07 DOCX conversion."""

from __future__ import annotations

import io
import os
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.pandoc import PandocConfig, PandocDocxConverter
from md_converter.conversion.service import DocxConversionService
from md_converter.conversion.validation import ApprovedMarkdown
from tests.golden.limits import ArchiveLimits
from tests.golden.openxml import WORD_NAMESPACE, inspect_docx

pytestmark = pytest.mark.integration
DOCX_LIMITS = ArchiveLimits(200, 5_000_000, 20_000_000, 200.0)


def default_reference_docx() -> bytes:
    completed = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout


@pytest.mark.requires_pandoc
def test_real_pandoc_version_is_exactly_approved() -> None:
    completed = subprocess.run(
        ["pandoc", "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert completed.stdout.splitlines()[0] == "pandoc 3.10.2"


def with_sentinel_style(reference: bytes) -> bytes:
    output = io.BytesIO()
    sentinel = b"""<w:style w:type="paragraph" w:styleId="T07Sentinel"><w:name w:val="T07 Sentinel"/></w:style>"""
    with (
        zipfile.ZipFile(io.BytesIO(reference)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for member in source.infolist():
            payload = source.read(member)
            if member.filename == "word/styles.xml":
                payload = payload.replace(b"</w:styles>", sentinel + b"</w:styles>")
            target.writestr(member, payload)
    return output.getvalue()


@pytest.mark.requires_pandoc
def test_real_pandoc_converts_complete_corpus_and_applies_reference(
    tmp_path: Path,
) -> None:
    markdown = Path("tests/corpus/markdown-structures/document.md").read_text(
        encoding="utf-8"
    )
    reference = with_sentinel_style(default_reference_docx())
    service = DocxConversionService(
        PandocDocxConverter(PandocConfig("pandoc", 30.0, 2.0, tmp_path), os.environ)
    )
    snapshot = inspect_docx(service.convert(markdown, reference), DOCX_LIMITS)
    assert "T07Sentinel" in snapshot.style_ids
    assert "word/footnotes.xml" in snapshot.parts
    assert any(
        relationship.relationship_type.endswith("/footnotes")
        for relationship in snapshot.relationships
    )
    document = ElementTree.fromstring(  # noqa: S314 - T04 rejected declarations
        snapshot.xml_parts["word/document.xml"]
    )
    assert next(document.iter(f"{{{WORD_NAMESPACE}}}tbl"), None) is not None
    assert next(document.iter(f"{{{WORD_NAMESPACE}}}hyperlink"), None) is not None
    assert next(document.iter(f"{{{WORD_NAMESPACE}}}numPr"), None) is not None
    applied_styles = {
        node.attrib[f"{{{WORD_NAMESPACE}}}val"]
        for node in document.iter(f"{{{WORD_NAMESPACE}}}pStyle")
    }
    assert {"Heading1", "Heading2", "SourceCode"} <= applied_styles
    bookmarks = {
        node.attrib[f"{{{WORD_NAMESPACE}}}name"]
        for node in document.iter(f"{{{WORD_NAMESPACE}}}bookmarkStart")
    }
    assert "t07-custom-heading" in bookmarks
    footnotes = ElementTree.fromstring(  # noqa: S314 - T04 rejected declarations
        snapshot.xml_parts["word/footnotes.xml"]
    )
    footnote_text = "".join(
        node.text or "" for node in footnotes.iter(f"{{{WORD_NAMESPACE}}}t")
    )
    assert "Footnote text" in footnote_text
    text = "".join(snapshot.document_text)
    for expected in (
        "Héading level 1",
        "First list item",
        "Local section link",
        "A quoted paragraph",
        "café",
        "deterministic",
    ):
        assert expected in text


@pytest.mark.parametrize(
    ("program", "expected_code"),
    [
        ("raise SystemExit(7)", ConversionErrorCode.PANDOC_FAILURE),
        (
            "from pathlib import Path; Path('output.docx').write_bytes(b'invalid')",
            ConversionErrorCode.INVALID_DOCX,
        ),
        ("import time; time.sleep(30)", ConversionErrorCode.PANDOC_TIMEOUT),
    ],
)
def test_real_process_boundary_normalizes_failures(
    tmp_path: Path, program: str, expected_code: ConversionErrorCode
) -> None:
    executable = tmp_path / "pandoc-fixture"
    executable.write_text(f"#!/usr/bin/env python3\n{program}\n", encoding="utf-8")
    executable.chmod(0o700)
    converter = PandocDocxConverter(
        PandocConfig(str(executable), 0.1, 0.2, tmp_path), os.environ
    )
    with pytest.raises(ConversionError) as captured:
        converter.convert(ApprovedMarkdown("# Safe"), b"opaque-reference")
    assert captured.value.code is expected_code


def test_real_process_boundary_reports_unavailable_engine(tmp_path: Path) -> None:
    converter = PandocDocxConverter(
        PandocConfig(str(tmp_path / "absent-pandoc"), 1.0, 0.2, tmp_path), os.environ
    )
    with pytest.raises(ConversionError) as captured:
        converter.convert(ApprovedMarkdown("# Safe"), b"opaque-reference")
    assert captured.value.code is ConversionErrorCode.PANDOC_UNAVAILABLE
