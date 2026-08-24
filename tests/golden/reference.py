"""Deterministic normalization for engine-provided DOCX reference archives."""

from __future__ import annotations

import io
import zipfile


def normalize_reference_docx(data: bytes) -> bytes:
    """Rewrite a trusted Pandoc reference with stable ZIP metadata and ordering."""

    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data), "r") as source,
        zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target,
    ):
        for source_info in sorted(source.infolist(), key=lambda item: item.filename):
            target_info = zipfile.ZipInfo(source_info.filename, (1980, 1, 1, 0, 0, 0))
            target_info.compress_type = zipfile.ZIP_STORED
            target_info.create_system = 3
            target_info.external_attr = 0o100644 << 16
            target.writestr(target_info, source.read(source_info))
    return output.getvalue()
