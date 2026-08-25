"""Functional contract tests for assembled DOCX conversion behavior."""

from collections.abc import Callable

import pytest

from markweave.conversion.service import DocxConversionService
from markweave.conversion.validation import ApprovedMarkdown


class RecordingConverter:
    def __init__(self) -> None:
        self.received: tuple[ApprovedMarkdown, bytes] | None = None
        self.deadline_monotonic: float | None = None

    def convert(
        self,
        markdown: ApprovedMarkdown,
        reference_docx: bytes,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> bytes:
        assert cancellation_requested is None
        self.deadline_monotonic = deadline_monotonic
        self.received = (markdown, reference_docx)
        return b"converted-docx"


@pytest.mark.functional
def test_service_passes_only_approved_markdown_to_engine() -> None:
    converter = RecordingConverter()
    service = DocxConversionService(converter)
    result = service.convert("# Approved\n\nFootnote.[^1]\n\n[^1]: Note.", b"reference")
    assert result == b"converted-docx"
    assert converter.received == (
        ApprovedMarkdown("# Approved\n\nFootnote.[^1]\n\n[^1]: Note."),
        b"reference",
    )


@pytest.mark.functional
def test_service_propagates_worker_deadline_to_docx_engine() -> None:
    converter = RecordingConverter()

    assert (
        DocxConversionService(converter).convert(
            "# Approved",
            b"reference",
            deadline_monotonic=123.5,
        )
        == b"converted-docx"
    )
    assert converter.deadline_monotonic == 123.5
