"""Shared SQL job mapping and dialect primitives."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import ColumnElement

from markweave.jobs.errors import (
    JobRepositoryError,
)
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobState,
    JobStep,
    SourceKind,
)
from markweave.jobs.policy import JobAdmissionPolicy
from markweave.persistence.schema import (
    ConversionJobRow,
)


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
        template_id=UUID(row.template_id) if row.template_id is not None else None,
        template_version_id=(
            UUID(row.template_version_id)
            if row.template_version_id is not None
            else None
        ),
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


class _SqlJobStore:
    """Validated SQL engine and shared atomic-update helpers."""

    def __init__(
        self, engine: Engine, admission_policy: JobAdmissionPolicy | None = None
    ) -> None:
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("Job repository requires SQLite or PostgreSQL")
        self._engine = engine
        self._admission_policy = admission_policy

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
