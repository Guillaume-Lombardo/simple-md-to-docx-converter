"""Explicit test-only values for T18-owned template configuration."""

from typing import Any


def template_settings(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "job_active_limit_per_user": 2,
        "job_global_queue_capacity": 10,
        "job_max_duration_seconds": 60.0,
        "worker_memory_budget_bytes": 536_870_912,
        "worker_ephemeral_storage_budget_bytes": 1_073_741_824,
        "worker_lease_seconds": 30.0,
        "worker_heartbeat_seconds": 5.0,
        "worker_incomplete_submission_seconds": 60.0,
        "worker_idle_poll_seconds": 0.25,
        "worker_error_backoff_seconds": 1.0,
        "worker_cleanup_interval_seconds": 60.0,
        "worker_cleanup_batch_size": 100,
        "conversion_max_decompressed_bytes": 10_000_000,
        "conversion_max_files": 100,
        "conversion_max_images": 50,
        "conversion_max_diagrams": 20,
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
