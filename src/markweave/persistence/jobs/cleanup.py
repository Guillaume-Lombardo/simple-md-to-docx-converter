"""Fenced terminal job cleanup persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import (
    JobRepositoryError,
)
from markweave.jobs.models import (
    TERMINAL_JOB_STATES,
    ExpiredJobObjects,
    JobOutput,
    JobState,
    result_manifest_object_id,
    result_object_id,
)
from markweave.persistence.jobs.common import _SqlJobStore
from markweave.persistence.schema import (
    ConversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write


class _JobCleanupRepository(_SqlJobStore):
    """Retryable object-cleanup claims and fenced acknowledgement."""

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredJobObjects, ...]:
        terminal_values = tuple(
            state.value
            for state in TERMINAL_JOB_STATES
            if state is not JobState.EXPIRED
        )
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                claimable = or_(
                    and_(
                        ConversionJobRow.state.in_(terminal_values),
                        ConversionJobRow.expires_at <= now,
                    ),
                    and_(
                        ConversionJobRow.state == JobState.EXPIRED.value,
                        ConversionJobRow.cleanup_completed.is_(False),
                        or_(
                            ConversionJobRow.cleanup_token.is_(None),
                            ConversionJobRow.cleanup_expires_at <= now,
                        ),
                    ),
                )
                statement = (
                    select(ConversionJobRow.id)
                    .where(claimable)
                    .order_by(ConversionJobRow.expires_at, ConversionJobRow.id)
                    .limit(limit)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                candidate_ids = tuple(database.scalars(statement))
                expired: list[ExpiredJobObjects] = []
                for candidate_id in candidate_ids:
                    cleanup_token = uuid4()
                    row = self._update_job_row(
                        database,
                        update(ConversionJobRow)
                        .where(ConversionJobRow.id == candidate_id, claimable)
                        .values(
                            state=JobState.EXPIRED.value,
                            error_code=None,
                            error_message=None,
                            updated_at=now,
                            cleanup_owner=worker_id,
                            cleanup_token=str(cleanup_token),
                            cleanup_expires_at=cleanup_lease_expires_at,
                        ),
                        candidate_id,
                    )
                    if row is None:
                        continue
                    derived_results = tuple(
                        result_object_id(UUID(row.id), attempt)
                        for attempt in range(1, row.attempt + 1)
                    )
                    derived_manifests = (
                        tuple(
                            result_manifest_object_id(UUID(row.id), attempt)
                            for attempt in range(1, row.attempt + 1)
                        )
                        if row.output in {JobOutput.PDF.value, JobOutput.BOTH.value}
                        else ()
                    )
                    stored_result = (
                        UUID(row.result_object_id)
                        if row.result_object_id is not None
                        else None
                    )
                    result_ids = derived_results
                    if stored_result is not None and stored_result not in result_ids:
                        result_ids = (*result_ids, stored_result)
                    stored_manifest = (
                        UUID(row.result_manifest_object_id)
                        if row.result_manifest_object_id is not None
                        else None
                    )
                    manifest_ids = derived_manifests
                    if (
                        stored_manifest is not None
                        and stored_manifest not in manifest_ids
                    ):
                        manifest_ids = (*manifest_ids, stored_manifest)
                    expired.append(
                        ExpiredJobObjects(
                            UUID(row.id),
                            cleanup_token,
                            UUID(row.owner_id),
                            UUID(row.source_object_id),
                            result_ids,
                            manifest_ids,
                        )
                    )
                return tuple(expired)
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.id == str(job_id),
                        ConversionJobRow.state == JobState.EXPIRED.value,
                        ConversionJobRow.cleanup_completed.is_(False),
                        ConversionJobRow.cleanup_token == str(cleanup_token),
                    )
                    .values(
                        cleanup_completed=True,
                        result_object_id=None,
                        result_manifest_object_id=None,
                        cleanup_owner=None,
                        cleanup_token=None,
                        cleanup_expires_at=None,
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
