"""Application service for synchronous Markdown-to-DOCX component execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from markweave.conversion.archive import (
    ApprovedDocument,
    ArchiveLimits,
    prepare_archive,
)
from markweave.conversion.images import ImageLimits
from markweave.conversion.validation import (
    ApprovedMarkdown,
    validate_document,
    validate_markdown,
)
from markweave.jobs.policy import ArchiveResourceBudget


class DocxConverter(Protocol):
    """Document-engine port used by the future asynchronous worker."""

    def convert(
        self,
        markdown: ApprovedMarkdown,
        reference_docx: bytes | None,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> bytes: ...


class DocxConversionService:
    """Validate Markdown before delegating to the configured DOCX engine."""

    def __init__(
        self,
        converter: DocxConverter,
        archive_budget: ArchiveResourceBudget | None = None,
    ) -> None:
        self._converter = converter
        self._archive_budget = archive_budget

    def convert(
        self,
        markdown: str,
        reference_docx: bytes | None,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> bytes:
        approved = validate_markdown(markdown)
        if deadline_monotonic is None and cancellation_requested is None:
            return self._converter.convert(approved, reference_docx)
        return self._converter.convert(
            approved,
            reference_docx,
            deadline_monotonic=deadline_monotonic,
            cancellation_requested=cancellation_requested,
        )

    def convert_document(
        self,
        document: ApprovedDocument,
        reference_docx: bytes | None,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> bytes:
        """Convert an already bounded package after binding every local image."""

        approved = validate_document(document)
        if deadline_monotonic is None and cancellation_requested is None:
            return self._converter.convert(approved, reference_docx)
        return self._converter.convert(
            approved,
            reference_docx,
            deadline_monotonic=deadline_monotonic,
            cancellation_requested=cancellation_requested,
        )

    def convert_archive(  # noqa: PLR0913 - explicit archive and runtime policy
        self,
        archive: bytes,
        reference_docx: bytes | None,
        archive_limits: ArchiveLimits,
        image_limits: ImageLimits,
        *,
        deadline_monotonic: float | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> bytes:
        """Prepare and convert one untrusted Markdown resource archive."""

        if self._archive_budget is not None:
            archive_limits = self._archive_budget.constrain(archive_limits)
        document = prepare_archive(archive, archive_limits, image_limits)
        return self.convert_document(
            document,
            reference_docx,
            deadline_monotonic=deadline_monotonic,
            cancellation_requested=cancellation_requested,
        )
