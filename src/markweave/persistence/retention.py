"""SQL retention adapter shared by SQLite and PostgreSQL profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, delete, literal, select, text, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    AuthenticationAuditRow,
    ConversionJobRow,
    RetentionCleanupRunRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.persistence.templates.common import _version
from markweave.retention import (
    MINIMUM_PROTECTED_TEMPLATE_VERSIONS,
    RetentionClaim,
)


class SqlRetentionRepository:
    """Fenced template cleanup and transactional audit cleanup evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def claim_template_versions(
        self,
        *,
        cutoff_at: datetime,
        now: datetime,
        lease_expires_at: datetime,
        minimum_versions: int,
        limit: int,
    ) -> tuple[RetentionClaim, ...]:
        if limit <= 0 or minimum_versions < MINIMUM_PROTECTED_TEMPLATE_VERSIONS:
            raise ValueError("Retention claim limits are invalid")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                rows = tuple(
                    database.scalars(
                        select(TemplateVersionRow)
                        .where(TemplateVersionRow.publication_state == "published")
                        .order_by(
                            TemplateVersionRow.template_id,
                            TemplateVersionRow.version_number.desc(),
                        )
                        .with_for_update(
                            skip_locked=self._engine.dialect.name == "postgresql"
                        )
                    )
                )
                current_ids = set(
                    database.scalars(
                        select(TemplateRow.current_version_id).where(
                            TemplateRow.current_version_id.is_not(None)
                        )
                    )
                )
                referenced_ids = set(
                    database.scalars(
                        select(ConversionJobRow.template_version_id).where(
                            ConversionJobRow.state != "expired"
                        )
                    )
                )
                per_template: dict[str, int] = {}
                candidates: list[TemplateVersionRow] = []
                for row in rows:
                    rank = per_template.get(row.template_id, 0)
                    per_template[row.template_id] = rank + 1
                    if (
                        rank < minimum_versions
                        or row.id in current_ids
                        or row.id in referenced_ids
                        or self._utc(row.created_at) >= cutoff_at
                        or (
                            row.retention_token is not None
                            and row.retention_lease_expires_at is not None
                            and row.retention_lease_expires_at > now
                        )
                    ):
                        continue
                    candidates.append(row)
                    if len(candidates) == limit:
                        break
                claims: list[RetentionClaim] = []
                for row in candidates:
                    token = uuid4()
                    row.retention_token = str(token)
                    row.retention_lease_expires_at = lease_expires_at
                    claims.append(RetentionClaim(_version(row), token))
                database.flush()
                return tuple(claims)
        except SQLAlchemyError:
            raise PersistenceError from None

    def complete_template_version(
        self, claim: RetentionClaim, *, completed_at: datetime
    ) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                result = database.execute(
                    delete(TemplateVersionRow).where(
                        TemplateVersionRow.id == str(claim.version.id),
                        TemplateVersionRow.template_id
                        == str(claim.version.template_id),
                        TemplateVersionRow.retention_token == str(claim.token),
                        TemplateVersionRow.publication_state == "published",
                        TemplateVersionRow.id.not_in(
                            select(TemplateRow.current_version_id).where(
                                TemplateRow.current_version_id.is_not(None)
                            )
                        ),
                    )
                )
                removed = getattr(result, "rowcount", 0) == 1
                database.add(
                    RetentionCleanupRunRow(
                        id=str(uuid4()),
                        kind="template_version",
                        cutoff_at=claim.version.created_at,
                        removed_count=int(removed),
                        completed_at=completed_at,
                    )
                )
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_audits(
        self, *, cutoff_at: datetime, completed_at: datetime, limit: int
    ) -> int:
        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if self._engine.dialect.name == "postgresql":
                    database.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_id)"),
                        {"lock_id": 1_914_365_011},
                    )
                combined = union_all(
                    select(
                        TemplateAuditRow.id.label("id"),
                        TemplateAuditRow.created_at.label("created_at"),
                        literal("template").label("record_type"),
                    ).where(TemplateAuditRow.created_at < cutoff_at),
                    select(
                        AuthenticationAuditRow.id.label("id"),
                        AuthenticationAuditRow.created_at.label("created_at"),
                        literal("authentication").label("record_type"),
                    ).where(AuthenticationAuditRow.created_at < cutoff_at),
                ).subquery()
                candidates = tuple(
                    database.execute(
                        select(combined)
                        .order_by(
                            combined.c.created_at,
                            combined.c.id,
                            combined.c.record_type,
                        )
                        .limit(limit)
                    )
                )
                removed = 0
                if candidates:
                    guard_id = str(uuid4())
                    database.execute(
                        text("INSERT INTO audit_cleanup_guards (id) VALUES (:id)"),
                        {"id": guard_id},
                    )
                    template_ids = [
                        candidate.id
                        for candidate in candidates
                        if candidate.record_type == "template"
                    ]
                    authentication_ids = [
                        candidate.id
                        for candidate in candidates
                        if candidate.record_type == "authentication"
                    ]
                    for row_type, identifiers in (
                        (TemplateAuditRow, template_ids),
                        (AuthenticationAuditRow, authentication_ids),
                    ):
                        if identifiers:
                            result = database.execute(
                                delete(row_type).where(row_type.id.in_(identifiers))
                            )
                            removed += int(getattr(result, "rowcount", 0))
                    database.execute(
                        text("DELETE FROM audit_cleanup_guards WHERE id = :id"),
                        {"id": guard_id},
                    )
                database.add(
                    RetentionCleanupRunRow(
                        id=str(uuid4()),
                        kind="audit",
                        cutoff_at=cutoff_at,
                        removed_count=removed,
                        completed_at=completed_at,
                    )
                )
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
