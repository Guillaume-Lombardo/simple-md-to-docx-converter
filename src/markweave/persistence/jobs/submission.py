"""Atomic job submission and source-publication persistence."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import (
    JobQueueCapacityExceededError,
    JobRepositoryError,
    JobRequestError,
    JobUserQuotaExceededError,
)
from markweave.jobs.models import (
    ConversionJob,
    JobState,
    JobStep,
    JobSubmission,
)
from markweave.persistence.jobs.common import _job, _SqlJobStore
from markweave.persistence.schema import (
    ConversionJobRow,
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write


class _JobSubmissionRepository(_SqlJobStore):
    """Submission, admission, idempotency, and source activation operations."""

    def create(self, submission: JobSubmission) -> tuple[ConversionJob, bool]:
        row = ConversionJobRow(
            id=str(submission.id),
            owner_id=str(submission.owner_id),
            source_object_id=str(submission.source_object_id),
            source_filename=submission.source_filename,
            source_kind=submission.source_kind.value,
            source_sha256=submission.source_sha256,
            source_size=submission.source_size,
            template_id=(
                str(submission.template_id)
                if submission.template_id is not None
                else None
            ),
            template_version_id=(
                str(submission.template_version_id)
                if submission.template_version_id is not None
                else None
            ),
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
                if submission.template_id is not None:
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
                            TemplateVersionRow.id
                            == str(submission.template_version_id),
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
