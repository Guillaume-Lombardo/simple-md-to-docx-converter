"""Reverse-job admission and owner-bound reads."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.job_admission import (
    ACTIVE_JOB_STATES,
    global_active_job_count,
    lock_global_admission,
)
from markweave.persistence.reversion_jobs.common import (
    _attempt,
    _job,
    _SqlReversionStore,
)
from markweave.persistence.schema import (
    ReversionAttemptRow,
    ReversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobRepositoryError,
    ReversionJobUserQuotaExceededError,
    ReversionQueueCapacityExceededError,
)
from markweave.reversion_jobs.models import (
    ReversionAttempt,
    ReversionJob,
    ReversionJobPage,
    ReversionJobState,
    ReversionJobStep,
    ReversionSubmission,
)


class _SqlReversionAdmission(_SqlReversionStore):
    """Reverse-job admission and owner-bound reads."""

    def create(self, submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        row = ReversionJobRow(
            id=str(submission.id),
            owner_id=str(submission.owner_id),
            source_object_id=str(submission.source_object_id),
            source_stem=submission.source_stem,
            source_extension=submission.admission.extension,
            source_family=submission.admission.family.value,
            detected_format=submission.admission.detected_format,
            parser_format=submission.admission.parser_format,
            source_sha256=submission.source_sha256,
            source_size=submission.source_size,
            component_versions=json.dumps(
                submission.component_versions, separators=(",", ":")
            ),
            request_digest=submission.request_digest,
            idempotency_digest=submission.idempotency_digest,
            correlation_id=submission.correlation_id,
            state=ReversionJobState.QUEUED.value,
            step=ReversionJobStep.QUEUED.value,
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
                if self._admission_policy is not None:
                    lock_global_admission(database, self._engine.dialect.name)
                replay = self._find_idempotent(database, submission)
                if replay is not None:
                    return replay, True
                self._after_idempotency_miss()
                self._enforce_admission(database, submission.owner_id)
                database.add(row)
                database.flush()
                return _job(row), False
        except ReversionJobUserQuotaExceededError, ReversionQueueCapacityExceededError:
            raise
        except IntegrityError:
            if submission.idempotency_digest is None:
                raise ReversionJobRepositoryError from None
            self._after_idempotency_collision()
            replay = self._get_idempotent(
                submission.owner_id, submission.idempotency_digest
            )
            if replay is None:
                raise ReversionJobRepositoryError from None
            if replay.request_digest != submission.request_digest:
                raise ReversionJobConflictError(
                    "Reverse idempotency key conflicts"
                ) from None
            return replay, True
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _after_idempotency_miss(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def _after_idempotency_collision(self) -> None:
        """Private observation seam overridden only by concurrency tests."""

    @staticmethod
    def _find_idempotent(
        database: DatabaseSession, submission: ReversionSubmission
    ) -> ReversionJob | None:
        if submission.idempotency_digest is None:
            return None
        row = database.scalar(
            select(ReversionJobRow).where(
                ReversionJobRow.owner_id == str(submission.owner_id),
                ReversionJobRow.idempotency_digest == submission.idempotency_digest,
            )
        )
        if row is None:
            return None
        if row.request_digest != submission.request_digest:
            raise ReversionJobConflictError("Reverse idempotency key conflicts")
        return _job(row)

    def _get_idempotent(self, owner_id: UUID, digest: str) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(ReversionJobRow).where(
                        ReversionJobRow.owner_id == str(owner_id),
                        ReversionJobRow.idempotency_digest == digest,
                    )
                )
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _enforce_admission(self, database: DatabaseSession, owner_id: UUID) -> None:
        policy = self._admission_policy
        if policy is None:
            return
        owner_active = database.scalar(
            select(func.count())
            .select_from(ReversionJobRow)
            .where(
                ReversionJobRow.owner_id == str(owner_id),
                ReversionJobRow.state.in_(ACTIVE_JOB_STATES),
            )
        )
        if int(owner_active or 0) >= policy.active_jobs_per_user:
            raise ReversionJobUserQuotaExceededError(
                "Active reverse-job quota exceeded"
            )
        if global_active_job_count(database) >= policy.global_queue_capacity:
            raise ReversionQueueCapacityExceededError(
                "Shared conversion queue capacity exceeded"
            )

    def activate_source(self, job_id: UUID, now: datetime) -> ReversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._update_job(
                    database,
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.state == ReversionJobState.QUEUED.value,
                        ReversionJobRow.source_ready.is_(False),
                    )
                    .values(source_ready=True, updated_at=now),
                    str(job_id),
                )
                if row is not None:
                    return _job(row)
                existing = database.get(ReversionJobRow, str(job_id))
                if (
                    existing is None
                    or existing.state != ReversionJobState.QUEUED.value
                    or not existing.source_ready
                ):
                    raise ReversionJobRepositoryError
                return _job(existing)
        except ReversionJobRepositoryError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_owner(self, job_id: UUID, owner_id: UUID) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(ReversionJobRow).where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.owner_id == str(owner_id),
                    )
                )
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def list_owner(
        self, owner_id: UUID, *, offset: int, limit: int
    ) -> ReversionJobPage:
        try:
            with DatabaseSession(self._engine) as database:
                owner = str(owner_id)
                total = database.scalar(
                    select(func.count())
                    .select_from(ReversionJobRow)
                    .where(ReversionJobRow.owner_id == owner)
                )
                rows = database.scalars(
                    select(ReversionJobRow)
                    .where(ReversionJobRow.owner_id == owner)
                    .order_by(
                        ReversionJobRow.created_at.desc(), ReversionJobRow.id.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                )
                return ReversionJobPage(
                    tuple(_job(row) for row in rows), int(total or 0), offset, limit
                )
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_internal(self, job_id: UUID) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(ReversionJobRow, str(job_id))
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_attempt(self, attempt_id: UUID) -> ReversionAttempt | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(ReversionAttemptRow, str(attempt_id))
                return _attempt(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def list_attempts(self, job_id: UUID) -> tuple[ReversionAttempt, ...]:
        try:
            with DatabaseSession(self._engine) as database:
                rows = database.scalars(
                    select(ReversionAttemptRow)
                    .where(ReversionAttemptRow.job_id == str(job_id))
                    .order_by(ReversionAttemptRow.attempt_number)
                )
                return tuple(_attempt(row) for row in rows)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None
