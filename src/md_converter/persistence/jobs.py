"""Transactional durable queue shared by SQLite and PostgreSQL profiles."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.elements import ColumnElement

from md_converter.jobs.errors import (
    JobLeaseLostError,
    JobRepositoryError,
    JobRequestError,
)
from md_converter.jobs.models import (
    TERMINAL_JOB_STATES,
    ConversionJob,
    ExpiredJobObjects,
    JobFailure,
    JobOutput,
    JobPage,
    JobState,
    JobStep,
    JobSubmission,
    LeaseHeartbeat,
    result_object_id,
)
from md_converter.persistence.schema import (
    ConversionJobRow,
    TemplateRow,
    TemplateVersionRow,
)
from md_converter.persistence.sql import serialize_sqlite_write


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _job(row: ConversionJobRow) -> ConversionJob:
    return ConversionJob(
        id=UUID(row.id),
        owner_id=UUID(row.owner_id),
        source_object_id=UUID(row.source_object_id),
        template_id=UUID(row.template_id),
        template_version_id=UUID(row.template_version_id),
        output=JobOutput(row.output),
        component_versions=_component_versions(row.component_versions),
        state=JobState(row.state),
        step=JobStep(row.step),
        progress=row.progress,
        request_digest=row.request_digest,
        idempotency_digest=row.idempotency_digest,
        created_at=_required_utc(row.created_at),
        updated_at=_required_utc(row.updated_at),
        attempt=row.attempt,
        source_ready=row.source_ready,
        lease_owner=row.lease_owner,
        lease_token=UUID(row.lease_token) if row.lease_token is not None else None,
        lease_expires_at=_utc(row.lease_expires_at),
        heartbeat_at=_utc(row.heartbeat_at),
        cancel_requested=row.cancel_requested,
        result_object_id=(
            UUID(row.result_object_id)
            if row.state == JobState.SUCCEEDED.value
            and row.result_object_id is not None
            else None
        ),
        error_code=row.error_code,
        error_message=row.error_message,
        expires_at=_utc(row.expires_at),
    )


def _component_versions(value: str) -> tuple[tuple[str, str], ...]:
    try:
        decoded: Any = json.loads(value)
        return tuple((str(name), str(version)) for name, version in decoded)
    except TypeError, ValueError:
        raise JobRepositoryError from None


def _required_utc(value: datetime) -> datetime:
    resolved = _utc(value)
    if resolved is None:  # pragma: no cover - the SQL schema forbids this state
        raise JobRepositoryError
    return resolved


class SqlJobRepository:
    """Atomic lifecycle transitions with dialect-specific queue locking."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("Job repository requires SQLite or PostgreSQL")
        self._engine = engine

    def create(self, submission: JobSubmission) -> tuple[ConversionJob, bool]:
        row = ConversionJobRow(
            id=str(submission.id),
            owner_id=str(submission.owner_id),
            source_object_id=str(submission.source_object_id),
            template_id=str(submission.template_id),
            template_version_id=str(submission.template_version_id),
            output=submission.output.value,
            component_versions=json.dumps(
                submission.component_versions, separators=(",", ":")
            ),
            state=JobState.QUEUED.value,
            step=JobStep.QUEUED.value,
            progress=0,
            request_digest=submission.request_digest,
            idempotency_digest=submission.idempotency_digest,
            created_at=submission.created_at,
            updated_at=submission.created_at,
            attempt=0,
            source_ready=False,
            cancel_requested=False,
            cleanup_completed=False,
        )
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                frozen = database.scalar(
                    select(TemplateRow.id)
                    .join(
                        TemplateVersionRow,
                        TemplateVersionRow.template_id == TemplateRow.id,
                    )
                    .where(
                        TemplateRow.id == str(submission.template_id),
                        TemplateRow.status == "active",
                        TemplateRow.publication_state == "published",
                        TemplateRow.current_version_id
                        == str(submission.template_version_id),
                        TemplateVersionRow.id == str(submission.template_version_id),
                        TemplateVersionRow.publication_state == "published",
                    )
                    .with_for_update()
                )
                if frozen is None:
                    raise JobRequestError
                database.add(row)
                database.flush()
                return _job(row), False
        except JobRequestError:
            raise
        except IntegrityError:
            if submission.idempotency_digest is None:
                raise JobRepositoryError from None
            replay = self._get_idempotent(
                submission.owner_id, submission.idempotency_digest
            )
            if replay is None:
                raise JobRepositoryError from None
            return replay, True
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def activate_source(self, job_id: UUID, now: datetime) -> ConversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                row = database.scalar(
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.id == str(job_id),
                        ConversionJobRow.state == JobState.QUEUED.value,
                        ConversionJobRow.source_ready.is_(False),
                    )
                    .values(source_ready=True, updated_at=now)
                    .returning(ConversionJobRow)
                )
                if row is not None:
                    return _job(row)
                existing = database.get(ConversionJobRow, str(job_id))
                if (
                    existing is None
                    or existing.state != JobState.QUEUED.value
                    or not existing.source_ready
                ):
                    raise JobRepositoryError
                return _job(existing)
        except JobRepositoryError:
            raise
        except SQLAlchemyError:
            raise JobRepositoryError from None

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

    def request_cancel(
        self,
        job_id: UUID,
        owner_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                row = database.scalar(
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
                    )
                    .returning(ConversionJobRow)
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

    def claim(
        self, worker_id: str, now: datetime, lease_expires_at: datetime
    ) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
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
                claimed_id = database.scalar(
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
                    .returning(ConversionJobRow.id)
                )
                if claimed_id is None:
                    return None
                database.flush()
                database.refresh(row)
                return _job(row)
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def heartbeat(self, heartbeat: LeaseHeartbeat) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                job_id = database.scalar(
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
                    .returning(ConversionJobRow.id)
                )
                return job_id is not None
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

    def succeed(  # noqa: PLR0913, PLR0917 - protocol implementation
        self,
        job_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ConversionJob:
        values = {
            "state": JobState.SUCCEEDED.value,
            "step": JobStep.COMPLETE.value,
            "progress": 100,
            "result_object_id": str(result_object_id),
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

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
    ) -> int:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                recovered = tuple(
                    database.scalars(
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
                        .returning(ConversionJobRow.id)
                    )
                )
                incomplete = tuple(
                    database.scalars(
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
                        .returning(ConversionJobRow.id)
                    )
                )
                return len(recovered) + len(incomplete)
        except SQLAlchemyError:
            raise JobRepositoryError from None

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
                    row = database.scalar(
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
                        )
                        .returning(ConversionJobRow)
                    )
                    if row is None:
                        continue
                    derived_results = tuple(
                        result_object_id(UUID(row.id), attempt)
                        for attempt in range(1, row.attempt + 1)
                    )
                    stored_result = (
                        UUID(row.result_object_id)
                        if row.result_object_id is not None
                        else None
                    )
                    result_ids = derived_results
                    if stored_result is not None and stored_result not in result_ids:
                        result_ids = (*result_ids, stored_result)
                    expired.append(
                        ExpiredJobObjects(
                            UUID(row.id),
                            cleanup_token,
                            UUID(row.owner_id),
                            UUID(row.source_object_id),
                            result_ids,
                        )
                    )
                return tuple(expired)
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                cleaned_id = database.scalar(
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
                        cleanup_owner=None,
                        cleanup_token=None,
                        cleanup_expires_at=None,
                    )
                    .returning(ConversionJobRow.id)
                )
                return cleaned_id is not None
        except SQLAlchemyError:
            raise JobRepositoryError from None

    def _get_idempotent(self, owner_id: UUID, digest: str) -> ConversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(ConversionJobRow).where(
                        ConversionJobRow.owner_id == str(owner_id),
                        ConversionJobRow.idempotency_digest == digest,
                    )
                )
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise JobRepositoryError from None

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
                row = database.scalar(
                    update(ConversionJobRow)
                    .where(*desired_conditions)
                    .values(**values)
                    .returning(ConversionJobRow)
                )
                if row is None and cancellation_wins:
                    row = database.scalar(
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
                        )
                        .returning(ConversionJobRow)
                    )
                if row is None:
                    raise JobLeaseLostError("Conversion job lease was lost")
                return _job(row)
        except JobLeaseLostError:
            raise
        except SQLAlchemyError:
            raise JobRepositoryError from None

    @staticmethod
    def _owned_lease(
        job_id: UUID, worker_id: str, lease_token: UUID
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            ConversionJobRow.id == str(job_id),
            ConversionJobRow.state == JobState.RUNNING.value,
            ConversionJobRow.lease_owner == worker_id,
            ConversionJobRow.lease_token == str(lease_token),
        )

    @staticmethod
    def _cleared_lease() -> dict[str, object]:
        return {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "cancel_requested": False,
        }
