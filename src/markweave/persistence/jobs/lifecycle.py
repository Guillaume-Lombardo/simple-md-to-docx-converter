"""Atomic cancellation and terminal conversion job lifecycle persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import case, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import (
    JobLeaseLostError,
    JobRepositoryError,
)
from markweave.jobs.models import (
    ConversionJob,
    JobFailure,
    JobState,
    JobStep,
)
from markweave.persistence.jobs.common import _job, _SqlJobStore
from markweave.persistence.schema import (
    ConversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write


class _JobTerminalRepository(_SqlJobStore):
    """Owner cancellation and fenced success, failure, or cancellation."""

    def request_cancel(
        self,
        job_id: UUID,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._update_job_row(
                    database,
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.id == str(job_id),
                        ConversionJobRow.owner_id == str(owner_id),
                        ConversionJobRow.state.in_(
                            (JobState.QUEUED.value, JobState.RUNNING.value)
                        ),
                    )
                    .values(
                        state=case(
                            (
                                ConversionJobRow.state == JobState.QUEUED.value,
                                JobState.CANCELLED.value,
                            ),
                            else_=ConversionJobRow.state,
                        ),
                        updated_at=now,
                        expires_at=case(
                            (
                                ConversionJobRow.state == JobState.QUEUED.value,
                                expires_at,
                            ),
                            else_=ConversionJobRow.expires_at,
                        ),
                        cancel_requested=case(
                            (
                                ConversionJobRow.state == JobState.RUNNING.value,
                                True,
                            ),
                            else_=False,
                        ),
                    ),
                    str(job_id),
                )
                if row is not None:
                    return _job(row)
                existing = database.scalar(
                    select(ConversionJobRow).where(
                        ConversionJobRow.id == str(job_id),
                        ConversionJobRow.owner_id == str(owner_id),
                    )
                )
                return _job(existing) if existing is not None else None
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def succeed(  # noqa: PLR0913, PLR0917 - protocol implementation
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        now: datetime,
        expires_at: datetime,
        result_manifest_object_id: UUID | None = None,
    ) -> ConversionJob:
        values = {
            "state": JobState.SUCCEEDED.value,
            "step": JobStep.COMPLETE.value,
            "progress": 100,
            "result_object_id": str(result_object_id),
            "result_manifest_object_id": (
                str(result_manifest_object_id)
                if result_manifest_object_id is not None
                else None
            ),
            "updated_at": now,
            "expires_at": expires_at,
            **self._cleared_lease(),
        }
        return self._finish_owned(
            job_id,
            worker_id,
            lease_token,
            now,
            expires_at,
            values,
            cancellation_wins=True,
        )

    def fail(self, failure: JobFailure) -> ConversionJob:
        values = {
            "state": JobState.FAILED.value,
            "error_code": failure.code,
            "error_message": failure.message,
            "updated_at": failure.now,
            "expires_at": failure.expires_at,
            **self._cleared_lease(),
        }
        return self._finish_owned(
            failure.job_id,
            failure.worker_id,
            failure.lease_token,
            failure.now,
            failure.expires_at,
            values,
            cancellation_wins=True,
        )

    def finish_cancelled(
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob:
        values = {
            "state": JobState.CANCELLED.value,
            "updated_at": now,
            "expires_at": expires_at,
            **self._cleared_lease(),
        }
        return self._finish_owned(
            job_id,
            worker_id,
            lease_token,
            now,
            expires_at,
            values,
            cancellation_wins=False,
        )

    def _finish_owned(  # noqa: PLR0913, PLR0917 - shared atomic transition
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
        values: dict[str, object],
        *,
        cancellation_wins: bool,
    ) -> ConversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                conditions = (
                    *self._owned_lease(job_id, worker_id, lease_token),
                    ConversionJobRow.lease_expires_at >= now,
                )
                desired_conditions = conditions
                if cancellation_wins:
                    desired_conditions = (
                        *conditions,
                        ConversionJobRow.cancel_requested.is_(False),
                    )
                row = self._update_job_row(
                    database,
                    update(ConversionJobRow)
                    .where(*desired_conditions)
                    .values(**values),
                    str(job_id),
                )
                if row is None and cancellation_wins:
                    row = self._update_job_row(
                        database,
                        update(ConversionJobRow)
                        .where(
                            *conditions,
                            ConversionJobRow.cancel_requested.is_(True),
                        )
                        .values(
                            state=JobState.CANCELLED.value,
                            updated_at=now,
                            expires_at=expires_at,
                            **self._cleared_lease(),
                        ),
                        str(job_id),
                    )
                if row is None:
                    raise JobLeaseLostError("Conversion job lease was lost")
                return _job(row)
        except JobLeaseLostError:
            raise
        except SQLAlchemyError:
            raise JobRepositoryError from None
