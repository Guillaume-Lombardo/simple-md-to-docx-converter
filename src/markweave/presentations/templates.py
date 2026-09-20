"""PowerPoint layout and effective Latin-font reference contract."""

from xml.etree.ElementTree import Element

from markweave.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)

PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
REQUIRED_LAYOUTS = frozenset(
    {
        "Title Slide",
        "Title and Content",
        "Section Header",
        "Two Content",
        "Comparison",
        "Content with Caption",
        "Blank",
    }
)


def inspect_layouts(roots: dict[str, Element]) -> None:
    """Refuse silent Pandoc fallback for incomplete reference layouts."""
    names: set[str] = set()
    for path, root in roots.items():
        if (
            path.startswith("ppt/slideLayouts/")
            and root.tag == f"{{{PRESENTATION_NS}}}sldLayout"
        ):
            common = root.find(f"{{{PRESENTATION_NS}}}cSld")
            if common is not None:
                names.add(common.get("name", ""))
    if not names >= REQUIRED_LAYOUTS:
        raise TemplateValidationError(
            TemplateValidationErrorCode.REQUIRED_STYLES,
            "PowerPoint template is missing required Pandoc layouts.",
        )


def presentation_fonts(roots: dict[str, Element]) -> tuple[str, ...]:
    """Inspect concrete text fonts and Latin theme defaults, not unused script mappings."""
    fonts: set[str] = set()
    for root in roots.values():
        for node in root.iter(f"{{{DRAWING_NS}}}latin"):
            family = node.get("typeface", "")
            if family and not family.startswith(("+mj-", "+mn-")):
                fonts.add(family)
    return tuple(sorted(fonts))
