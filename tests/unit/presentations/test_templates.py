"""PowerPoint reference layouts and theme-font validation."""

from xml.etree.ElementTree import Element, SubElement

import pytest

from markweave.presentations.templates import (
    DRAWING_NS,
    PRESENTATION_NS,
    REQUIRED_LAYOUTS,
    inspect_layouts,
    presentation_fonts,
)
from markweave.templates.errors import TemplateValidationError

pytestmark = pytest.mark.unit


def test_required_layouts_must_have_the_expected_names():
    roots = {}
    for index, name in enumerate(sorted(REQUIRED_LAYOUTS)):
        root = Element(f"{{{PRESENTATION_NS}}}sldLayout")
        SubElement(root, f"{{{PRESENTATION_NS}}}cSld", name=name)
        roots[f"ppt/slideLayouts/slideLayout{index}.xml"] = root
    inspect_layouts(roots)
    roots.pop(next(iter(roots)))
    with pytest.raises(TemplateValidationError, match="missing required"):
        inspect_layouts(roots)


def test_reference_fonts_ignore_theme_placeholders_and_empty_families():
    root = Element("theme")
    for name in ("Calibri", "Calibri Light", "+mj-lt", "+mn-lt", "", "Calibri"):
        SubElement(root, f"{{{DRAWING_NS}}}latin", typeface=name)
    SubElement(root, f"{{{DRAWING_NS}}}ea", typeface="Unused script font")
    assert presentation_fonts({"ppt/theme/theme1.xml": root}) == (
        "Calibri",
        "Calibri Light",
    )
