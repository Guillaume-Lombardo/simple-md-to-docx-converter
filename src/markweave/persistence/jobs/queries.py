"""Owner-scoped conversion job queries."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import (
    JobRepositoryError,
)
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobOutputFamily,
    JobPage,
    JobState,
)
from markweave.persistence.jobs.common import _job, _SqlJobStore
from markweave.persistence.schema import (
    ConversionJobRow,
)


class _JobQueryRepository(_SqlJobStore):
    """Read-only job identity and pagination operations."""

    def get(self, job_id: UUID) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(ConversionJobRow, str(job_id))
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def list_owner(
        self,
        owner_id: UUID,
        *,
        offset: int,
        limit: int,
        output_family: JobOutputFamily | None = None,
        expired: bool | None = None,
    ) -> JobPage:
        try:
            with DatabaseSession(self._engine) as database:
                owner = str(owner_id)
                conditions = [ConversionJobRow.owner_id == owner]
                if output_family is not None:
                    outputs = (
                        (JobOutput.PPTX.value, JobOutput.PPTX_BUNDLE.value)
                        if output_family is JobOutputFamily.PRESENTATION
                        else (
                            JobOutput.DOCX.value,
                            JobOutput.PDF.value,
                            JobOutput.BOTH.value,
                        )
                    )
                    conditions.append(ConversionJobRow.output.in_(outputs))
                if expired is not None:
                    expiration_condition = (
                        ConversionJobRow.state == JobState.EXPIRED.value
                    )
                    conditions.append(
                        expiration_condition if expired else ~expiration_condition
                    )
                total = database.scalar(
                    select(func.count())
                    .select_from(ConversionJobRow)
                    .where(*conditions)
                )
                rows = database.scalars(
                    select(ConversionJobRow)
                    .where(*conditions)
                    .order_by(
                        ConversionJobRow.created_at.desc(),
                        ConversionJobRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
                return JobPage(
                    tuple(_job(row) for row in rows),
                    int(total or 0),
                    offset,
                    limit,
                )
        except SQLAlchemyError:
            raise JobRepositoryError from None
