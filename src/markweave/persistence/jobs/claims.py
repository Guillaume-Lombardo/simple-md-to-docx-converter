"""Fenced conversion job claim, lease, heartbeat, and recovery persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import case, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import (
    JobRepositoryError,
)
from markweave.jobs.models import (
    ConversionJob,
    JobState,
    JobStep,
    LeaseHeartbeat,
)
from markweave.persistence.jobs.common import _job, _SqlJobStore
from markweave.persistence.schema import (
    ConversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write


class _JobClaimRepository(_SqlJobStore):
    """Worker claims, renewable leases, cancellation probes, and recovery."""

    def claim(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    select(ConversionJobRow)
                    .where(
                        ConversionJobRow.state == JobState.QUEUED.value,
                        ConversionJobRow.source_ready.is_(True),
                    )
                    .order_by(ConversionJobRow.created_at, ConversionJobRow.id)
                    .limit(1)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                row = database.scalar(statement)
                if row is None:
                    return None
                lease_token = uuid4()
                claim_update = (
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.id == row.id,
                        ConversionJobRow.state == JobState.QUEUED.value,
                        ConversionJobRow.source_ready.is_(True),
                    )
                    .values(
                        state=JobState.RUNNING.value,
                        step=JobStep.VALIDATING.value,
                        updated_at=now,
                        attempt=ConversionJobRow.attempt + 1,
                        lease_owner=worker_id,
                        lease_token=str(lease_token),
                        lease_expires_at=lease_expires_at,
                        heartbeat_at=now,
                        cancel_requested=False,
                    )
                )
                if self._engine.dialect.name == "sqlite":
                    claimed = (
                        getattr(database.execute(claim_update), "rowcount", 0) == 1
                    )
                else:
                    claimed = (
                        database.scalar(claim_update.returning(ConversionJobRow.id))
                        is not None
                    )
                if not claimed:
                    return None
                database.flush()
                database.refresh(row)
                return _job(row)
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def heartbeat(self, heartbeat: LeaseHeartbeat) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    update(ConversionJobRow)
                    .where(
                        *self._owned_lease(
                            heartbeat.job_id,
                            heartbeat.worker_id,
                            heartbeat.lease_token,
                        )
                    )
                    .where(ConversionJobRow.lease_expires_at >= heartbeat.now)
                    .where(
                        or_(
                            ConversionJobRow.heartbeat_at.is_(None),
                            ConversionJobRow.heartbeat_at <= heartbeat.now,
                        )
                    )
                    .values(
                        heartbeat_at=heartbeat.now,
                        lease_expires_at=heartbeat.lease_expires_at,
                        updated_at=heartbeat.now,
                        step=heartbeat.step.value,
                        progress=case(
                            (
                                ConversionJobRow.progress > heartbeat.progress,
                                ConversionJobRow.progress,
                            ),
                            else_=heartbeat.progress,
                        ),
                    )
                )
                if self._engine.dialect.name == "sqlite":
                    return getattr(database.execute(statement), "rowcount", 0) == 1
                return (
                    database.scalar(statement.returning(ConversionJobRow.id))
                    is not None
                )
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def cancellation_requested(
        self, job_id: UUID, worker_id: str, lease_token: UUID
    ) -> bool:
        try:
            with DatabaseSession(self._engine) as database:
                value = database.scalar(
                    select(ConversionJobRow.cancel_requested).where(
                        *self._owned_lease(job_id, worker_id, lease_token)
                    )
                )
                return bool(value)
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
    ) -> int:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                recover_statement = (
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.state == JobState.RUNNING.value,
                        ConversionJobRow.lease_expires_at < now,
                    )
                    .values(
                        state=case(
                            (
                                ConversionJobRow.cancel_requested.is_(True),
                                JobState.CANCELLED.value,
                            ),
                            else_=JobState.QUEUED.value,
                        ),
                        step=case(
                            (
                                ConversionJobRow.cancel_requested.is_(True),
                                ConversionJobRow.step,
                            ),
                            else_=JobStep.QUEUED.value,
                        ),
                        progress=case(
                            (
                                ConversionJobRow.cancel_requested.is_(True),
                                ConversionJobRow.progress,
                            ),
                            else_=0,
                        ),
                        updated_at=now,
                        expires_at=case(
                            (
                                ConversionJobRow.cancel_requested.is_(True),
                                expires_at,
                            ),
                            else_=ConversionJobRow.expires_at,
                        ),
                        **self._cleared_lease(),
                    )
                )
                incomplete_statement = (
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.state == JobState.QUEUED.value,
                        ConversionJobRow.source_ready.is_(False),
                        ConversionJobRow.created_at <= incomplete_before,
                    )
                    .values(
                        state=JobState.FAILED.value,
                        error_code="source_upload_incomplete",
                        error_message="Source upload did not complete.",
                        updated_at=now,
                        expires_at=expires_at,
                    )
                )
                if self._engine.dialect.name == "sqlite":
                    recovered_count = int(
                        getattr(database.execute(recover_statement), "rowcount", 0)
                    )
                    incomplete_count = int(
                        getattr(database.execute(incomplete_statement), "rowcount", 0)
                    )
                    return recovered_count + incomplete_count
                recovered = tuple(
                    database.scalars(recover_statement.returning(ConversionJobRow.id))
                )
                incomplete = tuple(
                    database.scalars(
                        incomplete_statement.returning(ConversionJobRow.id)
                    )
                )
                return len(recovered) + len(incomplete)
        except SQLAlchemyError:
            raise JobRepositoryError from None
