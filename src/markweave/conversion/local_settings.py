"""Conversion-only settings for the serverless local command (D1).

Local conversion reuses the service's ``MARKWEAVE_*`` conversion, engine, and
template limits with identical names and constraints. It deliberately has no
storage, account, scanner, or job settings and no implicit production values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from markweave.config import (
    ConfigurationError,
    ConflictingConfigurationError,
    validated_environment_model,
)


class LocalConfigurationError(ConfigurationError):
    """Local configuration failure naming variables but never their values."""

    def __init__(self, variables: tuple[str, ...]) -> None:
        super().__init__("Invalid local conversion configuration")
        self.variables = variables


class LocalConversionSettings(BaseSettings):
    """The service's document-pipeline limits, without any runtime profile."""

    model_config = SettingsConfigDict(
        env_prefix="MARKWEAVE_",
        case_sensitive=False,
        extra="ignore",
    )

    conversion_upload_max_bytes: int = Field(gt=0)
    conversion_max_decompressed_bytes: int = Field(gt=0)
    conversion_max_files: int = Field(gt=0)
    conversion_max_images: int = Field(gt=0)
    conversion_max_diagrams: int = Field(gt=0)
    conversion_max_compression_ratio: float = Field(ge=1.0, allow_inf_nan=False)
    conversion_image_max_source_bytes: int = Field(gt=0)
    conversion_image_max_width_pixels: int = Field(gt=0)
    conversion_image_max_height_pixels: int = Field(gt=0)
    conversion_image_max_pixels: int = Field(gt=0)
    conversion_image_max_svg_elements: int = Field(gt=0)
    conversion_image_max_svg_depth: int = Field(gt=0, le=64)
    conversion_mermaid_max_source_bytes: int = Field(gt=0)
    conversion_mermaid_max_total_source_bytes: int = Field(gt=0)
    conversion_mermaid_max_output_bytes: int = Field(gt=0)
    conversion_mermaid_max_total_output_bytes: int = Field(gt=0)
    conversion_mermaid_max_width_pixels: int = Field(gt=0)
    conversion_mermaid_max_height_pixels: int = Field(gt=0)
    conversion_mermaid_executable: str = Field(min_length=1)
    conversion_chromium_executable: str = Field(min_length=1)
    conversion_pdf_cancellation_poll_seconds: float = Field(gt=0, allow_inf_nan=False)
    conversion_pdf_max_bytes: int = Field(gt=0)
    conversion_pdf_max_decoded_stream_bytes: int = Field(gt=0)
    conversion_pdf_max_pages: int = Field(gt=0)
    conversion_pdf_max_objects: int = Field(gt=0)
    conversion_pdf_max_object_depth: int = Field(gt=0)
    # Required by the service; optional here because only PDF traceability uses it.
    conversion_font_manifest_path: Path | None = None
    template_max_archive_bytes: int = Field(gt=0)
    template_max_entries: int = Field(gt=0)
    template_max_member_bytes: int = Field(gt=0)
    template_max_total_bytes: int = Field(gt=0)
    template_max_compression_ratio: float = Field(ge=1.0, allow_inf_nan=False)
    template_max_xml_elements: int = Field(gt=0)
    template_max_xml_depth: int = Field(gt=0)
    template_max_xml_attributes: int = Field(gt=0)
    template_max_declared_fonts: int = Field(gt=0)
    template_max_font_name_characters: int = Field(gt=0)
    template_pandoc_executable: str = Field(min_length=1)
    template_libreoffice_executable: str = Field(min_length=1)
    template_engine_timeout_seconds: float = Field(gt=0)
    template_engine_termination_grace_seconds: float = Field(gt=0)
    template_engine_workspace_root: Path | None = None

    @model_validator(mode="after")
    def validate_mermaid_aggregates(self) -> Self:
        """Apply the service's cross-field Mermaid invariant."""
        if (
            self.conversion_mermaid_max_total_source_bytes
            < self.conversion_mermaid_max_source_bytes
            or self.conversion_mermaid_max_total_output_bytes
            < self.conversion_mermaid_max_output_bytes
        ):
            raise ValueError("aggregate Mermaid limits must cover one diagram")
        return self

    @classmethod
    def load(cls) -> LocalConversionSettings:
        """Load canonical and legacy variables, reporting only invalid names."""
        try:
            settings, _, _ = validated_environment_model(cls)
        except ConflictingConfigurationError as error:
            raise LocalConfigurationError(_variables(error.fields)) from None
        except ValidationError as error:
            fields = tuple(
                location[0]
                for item in error.errors(include_input=False, include_url=False)
                if (location := item.get("loc")) and isinstance(location[0], str)
            )
            raise LocalConfigurationError(
                _variables(fields)
                or _variables(
                    (
                        "conversion_mermaid_max_total_source_bytes",
                        "conversion_mermaid_max_total_output_bytes",
                    )
                )
            ) from None
        return settings


def _variables(fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({f"MARKWEAVE_{field.upper()}" for field in fields}))
