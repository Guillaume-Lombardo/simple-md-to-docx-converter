"""Transactional durable queue shared by SQLite and PostgreSQL profiles."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import ColumnElement

from md_converter.jobs.errors import (
    JobLeaseLostError,
    JobQueueCapacityExceededError,
    JobRepositoryError,
    JobRequestError,
    JobUserQuotaExceededError,
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
    SourceKind,
    result_manifest_object_id,
    result_object_id,
)
from md_converter.jobs.policy import JobAdmissionPolicy
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
        source_filename=row.source_filename,
        source_kind=SourceKind(row.source_kind)
        if row.source_kind is not None
        else None,
        source_sha256=row.source_sha256,
        source_size=row.source_size,
        template_id=UUID(row.template_id),
        template_version_id=UUID(row.template_version_id),
        output=JobOutput(row.output),
        component_versions=_component_versions(row.component_versions),
        correlation_id=row.correlation_id or row.id,
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
        result_manifest_object_id=(
            UUID(row.result_manifest_object_id)
            if row.state == JobState.SUCCEEDED.value
            and row.result_manifest_object_id is not None
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

    def __init__(
        self, engine: Engine, admission_policy: JobAdmissionPolicy | None = None
    ) -> None:
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("Job repository requires SQLite or PostgreSQL")
        self._engine = engine
        self._admission_policy = admission_policy

    def create(self, submission: JobSubmission) -> tuple[ConversionJob, bool]:
        row = ConversionJobRow(
            id=str(submission.id),
            owner_id=str(submission.owner_id),
            source_object_id=str(submission.source_object_id),
            source_filename=submission.source_filename,
            source_kind=submission.source_kind.value,
            source_sha256=submission.source_sha256,
            source_size=submission.source_size,
            template_id=str(submission.template_id),
            template_version_id=str(submission.template_version_id),
            output=submission.output.value,
            component_versions=json.dumps(
                submission.component_versions, separators=(",", ":")
            ),
            correlation_id=submission.correlation_id,
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
                self._lock_admission(database)
                replay = self._find_idempotent(database, submission)
                if replay is not None:
                    return replay, True
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
                        TemplateVersionRow.retention_token.is_(None),
                    )
                    .with_for_update()
                )
                if frozen is None:
                    raise JobRequestError
                self._enforce_admission(database, submission.owner_id)
                database.add(row)
                database.flush()
                return _job(row), False
        except JobRequestError:
            raise
        except JobUserQuotaExceededError, JobQueueCapacityExceededError:
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

    def _lock_admission(self, database: DatabaseSession) -> None:
        if (
            self._admission_policy is not None
            and self._engine.dialect.name == "postgresql"
        ):
            # One transaction-scoped lock serializes the two coupled capacity counts.
            database.execute(text("SELECT pg_advisory_xact_lock(1830285106)"))

    @staticmethod
    def _find_idempotent(
        database: DatabaseSession, submission: JobSubmission
    ) -> ConversionJob | None:
        if submission.idempotency_digest is None:
            return None
        row = database.scalar(
            select(ConversionJobRow).where(
                ConversionJobRow.owner_id == str(submission.owner_id),
                ConversionJobRow.idempotency_digest == submission.idempotency_digest,
            )
        )
        return _job(row) if row is not None else None

    def _enforce_admission(self, database: DatabaseSession, owner_id: UUID) -> None:
        policy = self._admission_policy
        if policy is None:
            return
        active_states = (JobState.QUEUED.value, JobState.RUNNING.value)
        owner_active = database.scalar(
            select(func.count())
            .select_from(ConversionJobRow)
            .where(
                ConversionJobRow.owner_id == str(owner_id),
                ConversionJobRow.state.in_(active_states),
            )
        )
        if int(owner_active or 0) >= policy.active_jobs_per_user:
            raise JobUserQuotaExceededError("Active conversion-job quota exceeded")
        queue_depth = database.scalar(
            select(func.count())
            .select_from(ConversionJobRow)
            .where(ConversionJobRow.state.in_(active_states))
        )
        if int(queue_depth or 0) >= policy.global_queue_capacity:
            raise JobQueueCapacityExceededError("Conversion queue capacity exceeded")

    def activate_source(self, job_id: UUID, now: datetime) -> ConversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._update_job_row(
                    database,
                    update(ConversionJobRow)
                    .where(
                        ConversionJobRow.id == str(job_id),
                        ConversionJobRow.state == JobState.QUEUED.value,
                        ConversionJobRow.source_ready.is_(False),
                    )
                    .values(source_ready=True, updated_at=now),
                    str(job_id),
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

    def _update_job_row(
        self,
        database: DatabaseSession,
        statement: Update,
        job_id: str,
    ) -> ConversionJobRow | None:
        if self._engine.dialect.name != "sqlite":
            return database.scalar(statement.returning(ConversionJobRow))
        result = database.execute(statement)
        if getattr(result, "rowcount", 0) != 1:
            return None
        return database.get(ConversionJobRow, job_id)

    @staticmethod
    def _cleared_lease() -> dict[str, object]:
        return {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "cancel_requested": False,
        }
