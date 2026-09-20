"""Actual pinned Pandoc PPTX output, default reference, notes and reference layouts."""

import io
import os
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

import pytest

from markweave.conversion.archive import ApprovedDocument
from markweave.conversion.pandoc import PandocConfig
from markweave.conversion.service import DocxConversionService
from markweave.presentations.models import PresentationOptions
from markweave.presentations.pandoc import PandocPptxConverter
from markweave.presentations.preparation import plan_presentation
from markweave.templates.errors import TemplateValidationError
from markweave.templates.models import TemplateKind
from markweave.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    TemplateLimits,
    validate_template,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_pandoc]
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"


def convert(tmp_path: Path, markdown: str, reference: bytes | None = None) -> bytes:
    plan = plan_presentation(
        ApprovedDocument(markdown, PurePosixPath("document.md"), ()),
        PresentationOptions(),
    )
    converter = PandocPptxConverter(
        PandocConfig("pandoc", 20, 1, tmp_path),
        os.environ,
        slide_level=0 if plan.explicit_breaks else plan.options.slide_level,
    )
    return DocxConversionService(converter).convert_document(plan.document, reference)


def texts(archive, path):
    root = ElementTree.fromstring(archive.read(path))  # noqa: S314 - trusted generated fixture
    return [node.text for node in root.iter(f"{{{DRAWING}}}t")]


def test_default_reference_generates_editable_slides_without_any_template(tmp_path):
    result = convert(tmp_path, "## First\n\nEditable text\n\n## Second\n\n- Bullet\n")
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert "Editable text" in texts(archive, "ppt/slides/slide1.xml")
        assert "Bullet" in texts(archive, "ppt/slides/slide2.xml")
        assert "ppt/slides/slide3.xml" not in archive.namelist()
        assert "ppt/theme/theme1.xml" in archive.namelist()
    assert list(tmp_path.iterdir()) == []


def test_marp_separator_and_presenter_notes_remain_native(tmp_path):
    result = convert(
        tmp_path,
        "---\nmarp: true\n---\n# First\n\nVisible\n\n<!-- Private note -->\n\n---\n\n# Second\n\nOther\n",
    )
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert "Visible" in texts(archive, "ppt/slides/slide1.xml")
        assert "Other" in texts(archive, "ppt/slides/slide2.xml")
        assert "Private note" in texts(archive, "ppt/notesSlides/notesSlide1.xml")
        assert "Private note" not in texts(archive, "ppt/slides/slide1.xml")


def test_reference_template_is_validated_and_its_theme_is_applied(tmp_path):
    reference = subprocess.run(
        ["pandoc", "--print-default-data-file=reference.pptx"],
        check=True,
        capture_output=True,
    ).stdout
    limits = TemplateLimits(
        5_000_000, 2000, 2_000_000, 10_000_000, 200, 250_000, 100, 500_000, 64, 128
    )
    validated = validate_template(
        reference, TemplateFontDeclaration(()), limits, APPROVED_FONT_POLICY
    )
    assert validated.kind is TemplateKind.PPTX
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(reference)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "ppt/theme/theme1.xml":
                content = content.replace(b'val="4F81BD"', b'val="13579B"')
            target.writestr(info, content)
    result = convert(tmp_path, "## Template\n\nEditable\n", output.getvalue())
    with (
        zipfile.ZipFile(io.BytesIO(result)) as archive,
        zipfile.ZipFile(output) as reference_zip,
    ):
        assert b'val="13579B"' in archive.read("ppt/theme/theme1.xml")
        assert archive.read("ppt/theme/theme1.xml") == reference_zip.read(
            "ppt/theme/theme1.xml"
        )


@pytest.mark.parametrize("attack", ["macro", "external", "layout", "font"])
def test_powerpoint_reference_rejects_unsafe_or_incompatible_packages(attack):
    reference = subprocess.run(
        ["pandoc", "--print-default-data-file=reference.pptx"],
        check=True,
        capture_output=True,
    ).stdout
    modified = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(reference)) as source,
        zipfile.ZipFile(modified, "w") as target,
    ):
        for member in source.infolist():
            content = source.read(member)
            if attack == "external" and member.filename == "_rels/.rels":
                content = content.replace(
                    b'Target="ppt/presentation.xml"',
                    b'Target="https://example.com/deck.pptx" TargetMode="External"',
                )
            if attack == "layout" and member.filename.startswith("ppt/slideLayouts/"):
                content = content.replace(b'name="Title Slide"', b'name="Unrecognized"')
            if attack == "font" and member.filename == "ppt/theme/theme1.xml":
                content = content.replace(
                    b'typeface="Calibri"', b'typeface="Unavailable Font"'
                )
            target.writestr(member, content)
        if attack == "macro":
            target.writestr("ppt/vbaProject.bin", b"active content")

    with pytest.raises(TemplateValidationError):
        validate_template(
            modified.getvalue(),
            TemplateFontDeclaration(()),
            TemplateLimits(
                5_000_000,
                2000,
                2_000_000,
                10_000_000,
                200,
                250_000,
                100,
                500_000,
                64,
                128,
            ),
            APPROVED_FONT_POLICY,
        )
