"""Application service for synchronous Markdown-to-DOCX component execution."""

from __future__ import annotations

from typing import Protocol

from md_converter.conversion.validation import ApprovedMarkdown, validate_markdown


class DocxConverter(Protocol):
    """Document-engine port used by the future asynchronous worker."""

    def convert(self, markdown: ApprovedMarkdown, reference_docx: bytes) -> bytes: ...


class DocxConversionService:
    """Validate Markdown before delegating to the configured DOCX engine."""

    def __init__(self, converter: DocxConverter) -> None:
        self._converter = converter

    def convert(self, markdown: str, reference_docx: bytes) -> bytes:
        approved = validate_markdown(markdown)
        return self._converter.convert(approved, reference_docx)
