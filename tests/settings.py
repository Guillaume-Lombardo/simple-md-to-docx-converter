"""Explicit test-only values for T18-owned template configuration."""

from typing import Any


def template_settings(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "template_max_archive_bytes": 1_000_000,
        "template_request_max_bytes": 1_100_000,
        "template_metadata_request_max_bytes": 4_096,
        "template_max_name_characters": 100,
        "template_max_description_characters": 1_000,
        "template_max_entries": 2_000,
        "template_max_member_bytes": 1_000_000,
        "template_max_total_bytes": 2_000_000,
        "template_max_compression_ratio": 200.0,
        "template_max_xml_elements": 250_000,
        "template_max_xml_depth": 100,
        "template_max_xml_attributes": 500_000,
        "template_max_declared_fonts": 64,
        "template_max_font_name_characters": 128,
        "template_pandoc_executable": "/bin/true",
        "template_libreoffice_executable": "/bin/true",
        "template_engine_timeout_seconds": 10.0,
        "template_engine_termination_grace_seconds": 1.0,
        "template_pending_publication_stale_seconds": 60.0,
    }
    values.update(overrides)
    return values
