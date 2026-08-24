"""SQL retention adapter shared by SQLite and PostgreSQL profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import (
    AuthenticationAuditRow,
    ConversionJobRow,
    RetentionCleanupRunRow,
    TemplateAuditRow,
    TemplateRow,
    TemplateVersionRow,
)
from md_converter.persistence.sql import serialize_sqlite_write
from md_converter.persistence.templates import _version
from md_converter.retention import (
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
                template_candidates = tuple(
                    database.execute(
                        select(TemplateAuditRow.id, TemplateAuditRow.created_at)
                        .where(TemplateAuditRow.created_at < cutoff_at)
                        .order_by(TemplateAuditRow.created_at, TemplateAuditRow.id)
                        .limit(limit)
                        .with_for_update(
                            skip_locked=self._engine.dialect.name == "postgresql"
                        )
                    )
                )
                authentication_candidates = tuple(
                    database.execute(
                        select(
                            AuthenticationAuditRow.id,
                            AuthenticationAuditRow.created_at,
                        )
                        .where(AuthenticationAuditRow.created_at < cutoff_at)
                        .order_by(
                            AuthenticationAuditRow.created_at,
                            AuthenticationAuditRow.id,
                        )
                        .limit(limit)
                        .with_for_update(
                            skip_locked=self._engine.dialect.name == "postgresql"
                        )
                    )
                )
                candidates = sorted(
                    [
                        (self._utc(row.created_at), row.id, "template")
                        for row in template_candidates
                    ]
                    + [
                        (self._utc(row.created_at), row.id, "authentication")
                        for row in authentication_candidates
                    ]
                )[:limit]
                removed = 0
                if candidates:
                    template_ids = [
                        candidate[1]
                        for candidate in candidates
                        if candidate[2] == "template"
                    ]
                    authentication_ids = [
                        candidate[1]
                        for candidate in candidates
                        if candidate[2] == "authentication"
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
