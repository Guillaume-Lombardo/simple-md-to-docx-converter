"""Application service for synchronous Markdown-to-DOCX component execution."""

from __future__ import annotations

from typing import Protocol

from md_converter.conversion.archive import (
    ApprovedDocument,
    ArchiveLimits,
    prepare_archive,
)
from md_converter.conversion.images import ImageLimits
from md_converter.conversion.validation import (
    ApprovedMarkdown,
    validate_document,
    validate_markdown,
)


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

    def convert_document(
        self, document: ApprovedDocument, reference_docx: bytes
    ) -> bytes:
        """Convert an already bounded package after binding every local image."""

        approved = validate_document(document)
        return self._converter.convert(approved, reference_docx)

    def convert_archive(
        self,
        archive: bytes,
        reference_docx: bytes,
        archive_limits: ArchiveLimits,
        image_limits: ImageLimits,
    ) -> bytes:
        """Prepare and convert one untrusted Markdown resource archive."""

        document = prepare_archive(archive, archive_limits, image_limits)
        return self.convert_document(document, reference_docx)
