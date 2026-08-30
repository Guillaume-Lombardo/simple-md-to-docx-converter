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
    JobPage,
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

    def list_owner(self, owner_id: UUID, *, offset: int, limit: int) -> JobPage:
        try:
            with DatabaseSession(self._engine) as database:
                owner = str(owner_id)
                total = database.scalar(
                    select(func.count())
                    .select_from(ConversionJobRow)
                    .where(ConversionJobRow.owner_id == owner)
                )
                rows = database.scalars(
                    select(ConversionJobRow)
                    .where(ConversionJobRow.owner_id == owner)
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
