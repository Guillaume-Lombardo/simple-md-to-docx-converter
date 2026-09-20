"""Configuration mapping for presentation preparation and reference downloads."""

import os
import tempfile
from pathlib import Path

from markweave.config import Settings
from markweave.conversion.archive import ArchiveLimits
from markweave.conversion.images import ImageLimits
from markweave.templates.engines import (
    TemplateEngineConfig,
    _bounded_regular_file,
    _environment,
    _run,
)


def preparation_limits(settings: Settings) -> tuple[ArchiveLimits, ImageLimits]:
    return (
        ArchiveLimits(
            settings.conversion_upload_max_bytes,
            settings.conversion_max_files,
            settings.conversion_max_decompressed_bytes,
            settings.conversion_max_decompressed_bytes,
            settings.conversion_max_compression_ratio,
            settings.conversion_upload_max_bytes,
            settings.conversion_max_images,
            settings.conversion_max_files,
        ),
        ImageLimits(
            settings.conversion_image_max_source_bytes,
            settings.conversion_image_max_width_pixels,
            settings.conversion_image_max_height_pixels,
            settings.conversion_image_max_pixels,
            settings.conversion_image_max_svg_elements,
            settings.conversion_image_max_svg_depth,
        ),
    )


def default_reference(settings: Settings) -> bytes:
    """Download the installed Pandoc default, without needing any catalog template."""
    config = TemplateEngineConfig(
        settings.template_pandoc_executable,
        settings.template_libreoffice_executable,
        settings.template_engine_timeout_seconds,
        settings.template_engine_termination_grace_seconds,
        settings.template_engine_workspace_root,
    )
    with tempfile.TemporaryDirectory(
        prefix="markweave-pptx-reference-", dir=config.workspace_root
    ) as directory:
        workspace = Path(directory)
        for name in ("home", "tmp", "cache", "config", "data"):
            (workspace / name).mkdir(mode=0o700)
        output = workspace / "reference.pptx"
        with output.open("xb") as destination:
            _run(
                (config.pandoc_executable, "--print-default-data-file=reference.pptx"),
                workspace,
                _environment(workspace, os.environ),
                config,
                stdout=destination.fileno(),
            )
        return _bounded_regular_file(output, settings.template_max_archive_bytes)
