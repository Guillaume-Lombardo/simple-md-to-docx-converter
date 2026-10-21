"""Real-file qualification of the bounded DOCX text edit family."""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from markweave.composer.docx_edits import (
    ReplaceText,
    apply_docx_text_edits,
    inspect_docx_text,
)
from markweave.composer.typed_templates import TypedDocxLimits
from tests.integration.document_engines.test_libreoffice_pdf import (
    _converter,
    _trace,
    _workspace,
)

_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = _ROOT / "spikes/anydoc/corpus/docx/text.docx"
_MAIN = "word/document.xml"


def _limits() -> TypedDocxLimits:
    return TypedDocxLimits(
        max_archive_bytes=1_000_000,
        max_entries=200,
        max_member_bytes=1_000_000,
        max_total_bytes=4_000_000,
        max_compression_ratio=200,
        max_xml_elements=250_000,
        max_xml_depth=100,
        max_xml_attributes=500_000,
        max_schema_bytes=10_000,
        max_values_bytes=10_000,
        max_repeat_items=4,
        max_text_characters=1_000,
    )


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _edit_every_target(source: bytes) -> tuple[bytes, dict[str, str]]:
    targets = inspect_docx_text(source, _limits())
    replacements = {
        item.target: f"Edited {index}"
        for index, item in enumerate(targets)
        if item.text.strip()
    }
    edited = apply_docx_text_edits(
        source,
        tuple(
            ReplaceText(item.target, item.text, replacements[item.target])
            for item in targets
            if item.target in replacements
        ),
        _limits(),
    )
    return edited, replacements


def _assert_only_main_part_changed(source: bytes, edited: bytes) -> None:
    before, after = _members(source), _members(edited)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == {_MAIN}
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(io.BytesIO(edited)) as result,
    ):
        assert [item.filename for item in original.infolist()] == [
            item.filename for item in result.infolist()
        ]


@pytest.mark.integration
@pytest.mark.light_coverage
def test_real_word_document_keeps_every_non_target_byte() -> None:
    source = _SOURCE.read_bytes()

    edited, replacements = _edit_every_target(source)

    _assert_only_main_part_changed(source, edited)
    reread = {item.target: item.text for item in inspect_docx_text(edited, _limits())}
    for target, text in replacements.items():
        assert reread[target] == text
    assert any(item.in_table for item in inspect_docx_text(edited, _limits()))


@pytest.mark.integration
@pytest.mark.requires_libreoffice
def test_edited_real_document_opens_and_renders_in_libreoffice(
    tmp_path: Path,
) -> None:
    source = _SOURCE.read_bytes()
    target = inspect_docx_text(source, _limits())[0]
    edited = apply_docx_text_edits(
        source,
        (ReplaceText(target.target, target.text, "Qualified <edited> title"),),
        _limits(),
    )

    rendered = _converter(_workspace(tmp_path)).convert(edited, _trace(source))

    text = "\n".join(
        page.extract_text() for page in PdfReader(io.BytesIO(rendered.pdf)).pages
    )
    assert "Qualified <edited> title" in text
    assert "Plain paragraph with" in text


@pytest.mark.integration
@pytest.mark.requires_pandoc
def test_pandoc_generated_document_edits_preserve_the_package() -> None:
    markdown = (
        "# Report\n\nA [link](https://example.invalid/) and **bold** text.\n\n"
        "| Name | Value |\n|---|---|\n| alpha | 1 |\n\nFinal paragraph.\n"
    )
    source = subprocess.run(
        ["pandoc", "--from", "markdown", "--to", "docx", "--output", "-"],
        input=markdown.encode(),
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout

    edited, replacements = _edit_every_target(source)

    _assert_only_main_part_changed(source, edited)
    assert replacements
    texts = [item.text for item in inspect_docx_text(edited, _limits())]
    assert all(text in texts for text in replacements.values())
