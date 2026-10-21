"""Real-file qualification of the bounded PPTX slide and notes text edit family."""

from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader

from markweave.composer.office_edits import ReplaceText
from markweave.composer.pptx_edits import apply_pptx_text_edits, inspect_pptx_text
from markweave.composer.typed_templates import TypedDocxLimits

_ROOT = Path(__file__).resolve().parents[3]
_SOURCE = _ROOT / "spikes/anydoc/corpus/pptx/pres.pptx"


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


def _edit_every_target(source: bytes) -> tuple[bytes, set[str]]:
    targets = inspect_pptx_text(source, _limits())
    edited = apply_pptx_text_edits(
        source,
        tuple(
            ReplaceText(item.target, item.text, f"Edited {index}")
            for index, item in enumerate(targets)
        ),
        _limits(),
    )
    return edited, {item.target.split("#", 1)[0] for item in targets}


@pytest.mark.integration
@pytest.mark.light_coverage
def test_real_presentation_keeps_every_non_target_byte() -> None:
    source = _SOURCE.read_bytes()
    targets = inspect_pptx_text(source, _limits())

    edited, parts = _edit_every_target(source)

    assert {(item.slide, item.notes) for item in targets} == {
        (1, False),
        (1, True),
        (2, False),
        (2, True),
    }
    assert any(item.in_table for item in targets)
    before, after = _members(source), _members(edited)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == parts
    assert [item.text for item in inspect_pptx_text(edited, _limits())] == [
        f"Edited {index}" for index in range(len(targets))
    ]


@pytest.mark.integration
@pytest.mark.requires_libreoffice
def test_edited_real_presentation_opens_and_renders_in_libreoffice(
    tmp_path: Path,
) -> None:
    source = _SOURCE.read_bytes()
    title = inspect_pptx_text(source, _limits())[0]
    edited = apply_pptx_text_edits(
        source,
        (ReplaceText(title.target, title.text, "Qualified edited deck"),),
        _limits(),
    )

    deck = tmp_path / "edited.pptx"
    deck.write_bytes(edited)
    subprocess.run(
        [
            "soffice",
            f"-env:UserInstallation={(tmp_path / 'profile').as_uri()}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(tmp_path),
            str(deck),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )

    text = "\n".join(
        page.extract_text() for page in PdfReader(tmp_path / "edited.pdf").pages
    )
    assert "Qualified edited deck" in text
    assert "Numbers Slide" in text


@pytest.mark.integration
@pytest.mark.requires_pandoc
def test_pandoc_generated_presentation_edits_preserve_the_package() -> None:
    markdown = (
        "# Report\n\nA **bold** point.\n\n::: notes\nSpeaker detail.\n:::\n\n"
        "# Table\n\n| Name | Value |\n|---|---|\n| alpha | 1 |\n"
    )
    source = subprocess.run(
        ["pandoc", "--from", "markdown", "--to", "pptx", "--output", "-"],
        input=markdown.encode(),
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout

    edited, parts = _edit_every_target(source)

    before, after = _members(source), _members(edited)
    assert before.keys() == after.keys()
    assert {name for name in before if before[name] != after[name]} == parts
    texts = {item.text for item in inspect_pptx_text(source, _limits())}
    assert {"Report", "Speaker detail.", "alpha"} <= texts
