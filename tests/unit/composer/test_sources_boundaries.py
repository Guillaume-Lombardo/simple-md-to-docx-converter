"""Reject unsafe or ambiguous Composer source packages at bounded byte limits."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from markweave.composer.sources import (
    markdown_from_archive,
    markdown_from_reversion_result,
    repack_approved_markdown,
)
from markweave.config import Settings
from markweave.http.composer_errors import ComposerRequestError
from tests.settings import template_settings

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    return Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="test-password",  # noqa: S106 - isolated fixture
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=10_000,
        conversion_request_max_bytes=11_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=10_000,
        composer_http_request_max_bytes=11_000,
    )


def _archive(*members: tuple[str, bytes], compressed: bool = False) -> bytes:
    output = BytesIO()
    with ZipFile(
        output, "w", compression=ZIP_DEFLATED if compressed else 0
    ) as zip_file:
        for name, content in members:
            zip_file.writestr(name, content)
    return output.getvalue()


def test_upload_archive_requires_configured_limit_and_nonempty_markdown() -> None:
    settings = _settings()
    valid = _archive(("document.md", b"# Valid\n"))
    assert markdown_from_archive(valid, settings) == "# Valid\n"
    with pytest.raises(ComposerRequestError, match="limit is unavailable"):
        markdown_from_archive(
            valid, settings.model_copy(update={"composer_upload_max_bytes": None})
        )
    with pytest.raises(ComposerRequestError, match="Markdown is empty"):
        markdown_from_archive(_archive(("document.md", b"")), settings)


def test_repack_rejects_empty_approval_and_expanded_asset_package() -> None:
    settings = _settings()
    source = _archive(("document.md", b"# Original\n"))
    with pytest.raises(ComposerRequestError, match="conversion limit"):
        repack_approved_markdown(b"", source, settings)
    # The incoming DEFLATE archive fits, but the immutable STORED export does not.
    text = b"A" * 170
    compact = _archive(("document.md", text), compressed=True)
    assert len(compact) < 200
    with pytest.raises(ComposerRequestError, match="asset package exceeds"):
        repack_approved_markdown(
            text,
            compact,
            settings.model_copy(update={"conversion_upload_max_bytes": 200}),
        )


def test_reversion_repack_rejects_wrong_manifest_order_and_expansion() -> None:
    settings = _settings()
    malformed = _archive(("manifest.json", b"{}"), ("document.md", b"# Valid\n"))
    with pytest.raises(ComposerRequestError, match="package is invalid"):
        repack_approved_markdown(
            b"# Approved\n", malformed, settings, reversion_result=True
        )
    expanded = _archive(
        ("document.md", b"A" * 300), ("manifest.json", b"{}"), compressed=True
    )
    with pytest.raises(ComposerRequestError, match="package exceeds the limit"):
        repack_approved_markdown(
            b"# Approved\n",
            expanded,
            settings.model_copy(update={"conversion_max_decompressed_bytes": 100}),
            reversion_result=True,
        )


def test_reversion_entrypoint_rejects_oversize_layout_and_invalid_text() -> None:
    settings = _settings()
    valid = _archive(("document.md", b"# Valid\n"), ("manifest.json", b"{}"))
    assert markdown_from_reversion_result(valid, settings) == "# Valid\n"
    with pytest.raises(ComposerRequestError, match="exceeds the limit"):
        markdown_from_reversion_result(
            valid, settings.model_copy(update={"composer_upload_max_bytes": 50})
        )
    with pytest.raises(ComposerRequestError, match="package is invalid"):
        markdown_from_reversion_result(
            _archive(("manifest.json", b"{}"), ("document.md", b"# Valid\n")),
            settings,
        )
    compressed = _archive(
        ("document.md", b"A" * 400), ("manifest.json", b"{}"), compressed=True
    )
    assert len(compressed) < 300
    with pytest.raises(ComposerRequestError, match="Markdown exceeds the limit"):
        markdown_from_reversion_result(
            compressed, settings.model_copy(update={"composer_upload_max_bytes": 300})
        )
    for content in (b"", b"# Valid\x00invalid"):
        with pytest.raises(ComposerRequestError, match="Markdown is invalid"):
            markdown_from_reversion_result(
                _archive(("document.md", content), ("manifest.json", b"{}")),
                settings,
            )
