"""Shared mapping and SQL helpers for durable reverse jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import ColumnElement

from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.persistence.schema import ReversionAttemptRow, ReversionJobRow
from markweave.reversion_jobs.errors import ReversionJobRepositoryError
from markweave.reversion_jobs.models import (
    ReversionAttempt,
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionTraceMetadata,
)
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from markweave.reversions.formats import FormatAdmission, FormatFamily
from markweave.reversions.models import ReverseOutputMode


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _required_utc(value: datetime) -> datetime:
    resolved = _utc(value)
    if resolved is None:  # pragma: no cover - SQL forbids null
        raise ReversionJobRepositoryError
    return resolved


def _versions(value: str) -> tuple[tuple[str, str], ...]:
    try:
        decoded: Any = json.loads(value)
        return tuple((str(name), str(version)) for name, version in decoded)
    except TypeError, ValueError:
        raise ReversionJobRepositoryError from None


def _trace(value: str | None) -> ReversionTraceMetadata | None:
    if value is None:
        return None
    try:
        decoded: Any = json.loads(value)
        if type(decoded) is not dict or set(decoded) != {
            "schema_version",
            "engine_name",
            "engine_version",
            "source_family",
            "detected_format",
            "result_mode",
            "asset_count",
            "asset_bytes",
            "unavailable_asset_count",
            "local",
            "ocr",
            "hosted_fallback",
        }:
            raise ValueError
        return ReversionTraceMetadata(
            schema_version=decoded["schema_version"],
            engine_name=decoded["engine_name"],
            engine_version=decoded["engine_version"],
            source_family=FormatFamily(decoded["source_family"]),
            detected_format=decoded["detected_format"],
            result_mode=ReverseOutputMode(decoded["result_mode"]),
            asset_count=decoded["asset_count"],
            asset_bytes=decoded["asset_bytes"],
            unavailable_asset_count=decoded["unavailable_asset_count"],
            local=decoded["local"],
            ocr=decoded["ocr"],
            hosted_fallback=decoded["hosted_fallback"],
        )
    except KeyError, TypeError, ValueError:
        raise ReversionJobRepositoryError from None


def _trace_json(trace: ReversionTraceMetadata) -> str:
    return json.dumps(
        {
            "schema_version": trace.schema_version,
            "engine_name": trace.engine_name,
            "engine_version": trace.engine_version,
            "source_family": trace.source_family.value,
            "detected_format": trace.detected_format,
            "result_mode": trace.result_mode.value,
            "asset_count": trace.asset_count,
            "asset_bytes": trace.asset_bytes,
            "unavailable_asset_count": trace.unavailable_asset_count,
            "local": trace.local,
            "ocr": trace.ocr,
            "hosted_fallback": trace.hosted_fallback,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _job(row: ReversionJobRow) -> ReversionJob:
    return ReversionJob(
        id=UUID(row.id),
        owner_id=UUID(row.owner_id),
        source_object_id=UUID(row.source_object_id),
        source_stem=row.source_stem,
        admission=FormatAdmission(
            FormatFamily(row.source_family),
            row.source_extension,
            row.detected_format,
            row.parser_format,
        ),
        source_sha256=row.source_sha256,
        source_size=row.source_size,
        component_versions=_versions(row.component_versions),
        request_digest=row.request_digest,
        idempotency_digest=row.idempotency_digest,
        correlation_id=row.correlation_id,
        state=ReversionJobState(row.state),
        step=ReversionJobStep(row.step),
        created_at=_required_utc(row.created_at),
        updated_at=_required_utc(row.updated_at),
        attempt=row.attempt,
        source_ready=row.source_ready,
        lease_owner=row.lease_owner,
        lease_token=UUID(row.lease_token) if row.lease_token is not None else None,
        lease_expires_at=_utc(row.lease_expires_at),
        heartbeat_at=_utc(row.heartbeat_at),
        current_attempt_id=(
            UUID(row.current_attempt_id) if row.current_attempt_id is not None else None
        ),
        cancel_requested=row.cancel_requested,
        result_mode=(ReverseOutputMode(row.result_mode) if row.result_mode else None),
        result_object_id=(UUID(row.result_object_id) if row.result_object_id else None),
        result_sha256=row.result_sha256,
        result_size=row.result_size,
        trace=_trace(row.trace_metadata),
        error_code=row.error_code,
        error_message=row.error_message,
        expires_at=_utc(row.expires_at),
    )


def _proof(row: ReversionAttemptRow) -> TerminationProof | None:
    if row.proof_id is None:
        return None
    try:
        return TerminationProof(
            UUID(row.proof_id),
            UUID(row.attempt_id),
            UUID(row.proof_unit_id or ""),
            AuthenticatedPrincipal(UUID(row.proof_principal_id or "")),
            row.proof_policy_revision or "",
            EvidenceDigest(row.exit_evidence or ""),
            EvidenceDigest(row.empty_evidence or ""),
            EvidenceDigest(row.removal_evidence or ""),
        )
    except ValueError:
        raise ReversionJobRepositoryError from None


def _attempt(row: ReversionAttemptRow) -> ReversionAttempt:
    return ReversionAttempt(
        job_id=UUID(row.job_id),
        attempt_number=row.attempt_number,
        attempt_id=UUID(row.attempt_id),
        worker_id=row.worker_id,
        lease_token=UUID(row.lease_token),
        leased_at=_required_utc(row.leased_at),
        heartbeat_at=_required_utc(row.heartbeat_at),
        lease_expires_at=_required_utc(row.lease_expires_at),
        principal_id=UUID(row.principal_id),
        create_sequence=row.create_sequence,
        create_intent_at=_utc(row.create_intent_at),
        unit_id=UUID(row.unit_id) if row.unit_id else None,
        policy_revision=row.policy_revision,
        policy_specification=(
            EvidenceDigest(row.policy_specification)
            if row.policy_specification is not None
            else None
        ),
        termination_proof=_proof(row),
        proof_recorded_at=_utc(row.proof_recorded_at),
        proof_acknowledged_at=_utc(row.proof_acknowledged_at),
        proof_recovery_token=(
            UUID(row.proof_recovery_token) if row.proof_recovery_token else None
        ),
        recovery_owner=row.recovery_owner,
        recovery_token=UUID(row.recovery_token) if row.recovery_token else None,
        recovery_expires_at=_utc(row.recovery_expires_at),
    )


class _SqlReversionStore:
    def __init__(
        self, engine: Engine, admission_policy: ReversionAdmissionPolicy | None = None
    ) -> None:
        if engine.dialect.name not in {"sqlite", "postgresql"}:
            raise ValueError("Reversion repository requires SQLite or PostgreSQL")
        self._engine = engine
        self._admission_policy = admission_policy

    @staticmethod
    def _owned_current(
        job_id: UUID, attempt_id: UUID, worker_id: str, lease_token: UUID
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            ReversionJobRow.id == str(job_id),
            ReversionJobRow.state == ReversionJobState.RUNNING.value,
            ReversionJobRow.current_attempt_id == str(attempt_id),
            ReversionJobRow.lease_owner == worker_id,
            ReversionJobRow.lease_token == str(lease_token),
        )

    def _update_job(
        self, database: DatabaseSession, statement: Update, job_id: str
    ) -> ReversionJobRow | None:
        statement = statement.execution_options(synchronize_session=False)
        if self._engine.dialect.name != "sqlite":
            updated_id = database.scalar(statement.returning(ReversionJobRow.id))
            if updated_id is None:
                return None
            database.expire_all()
            return database.get(ReversionJobRow, updated_id)
        result = database.execute(statement)
        if getattr(result, "rowcount", 0) != 1:
            return None
        database.expire_all()
        return database.get(ReversionJobRow, job_id)

    @staticmethod
    def _clear_lease() -> dict[str, object]:
        return {
            "lease_owner": None,
            "lease_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "current_attempt_id": None,
            "cancel_requested": False,
        }
