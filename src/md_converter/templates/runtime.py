"""Runtime assembly for bounded static template activation validation."""

from collections.abc import Callable

from md_converter.config import Settings
from md_converter.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    TemplateLimits,
    validate_template,
)


def build_template_validator(settings: Settings) -> Callable[[bytes], str]:
    """Build a validator whose safety ceilings remain configurable for T18."""
    limits = TemplateLimits(
        settings.template_max_archive_bytes,
        settings.template_max_entries,
        settings.template_max_member_bytes,
        settings.template_max_total_bytes,
        settings.template_max_compression_ratio,
        settings.template_max_xml_elements,
        settings.template_max_xml_depth,
        settings.template_max_xml_attributes,
        settings.template_max_declared_fonts,
        settings.template_max_font_name_characters,
    )
    known_families = tuple(
        dict.fromkeys(
            (
                *APPROVED_FONT_POLICY.approved_families,
                *(source for source, _ in APPROVED_FONT_POLICY.substitutions),
            )
        )
    )
    declaration = TemplateFontDeclaration(known_families)

    def validate(content: bytes) -> str:
        return validate_template(
            content, declaration, limits, APPROVED_FONT_POLICY
        ).sha256

    return validate
