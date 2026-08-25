"""Runtime assembly for complete T10 template activation validation."""

from __future__ import annotations

import os
from collections.abc import Callable

from markweave.config import Settings
from markweave.templates.engines import (
    TemplateActivationContext,
    TemplateEngineConfig,
    validate_template_for_activation,
)
from markweave.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    TemplateLimits,
    ValidatedTemplate,
)


def build_template_validator(
    settings: Settings,
) -> Callable[[bytes, TemplateFontDeclaration], ValidatedTemplate]:
    """Build static, Pandoc, and LibreOffice validation with caller-set bounds."""
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
    context = TemplateActivationContext(
        limits=limits,
        policy=APPROVED_FONT_POLICY,
        engines=TemplateEngineConfig(
            pandoc_executable=settings.template_pandoc_executable,
            libreoffice_executable=settings.template_libreoffice_executable,
            timeout_seconds=settings.template_engine_timeout_seconds,
            termination_grace_seconds=settings.template_engine_termination_grace_seconds,
            workspace_root=settings.template_engine_workspace_root,
        ),
        host_environment=os.environ,
    )

    def validate(
        content: bytes, declaration: TemplateFontDeclaration
    ) -> ValidatedTemplate:
        return validate_template_for_activation(content, declaration, context)

    return validate
