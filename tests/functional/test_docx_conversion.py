"""Functional contract tests for assembled DOCX conversion behavior."""

import pytest

from md_converter.conversion.service import DocxConversionService
from md_converter.conversion.validation import ApprovedMarkdown


class RecordingConverter:
    def __init__(self) -> None:
        self.received: tuple[ApprovedMarkdown, bytes] | None = None

    def convert(self, markdown: ApprovedMarkdown, reference_docx: bytes) -> bytes:
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
