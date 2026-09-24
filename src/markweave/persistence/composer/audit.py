"""Immutable, content-free Composer mutation audit and bounded retention."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, literal, select, text, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.auth.models import SYSTEM_ACTOR_ID  # noqa: F401 - public re-export
from markweave.persistence.composer.common import DEFAULT_PAGE_LIMIT, utc, validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    AuthorKnowledgeAuditRow,
    ComposerContentAuditRow,
    RetentionCleanupRunRow,
    TypedTemplateAuditRow,
)
from markweave.persistence.sql import serialize_sqlite_write


@dataclass(frozen=True, slots=True)
class ComposerContentAuditEvent:
    """A mutation identity without any document, prompt, response, or secret value."""

    id: UUID
    owner_id: UUID
    actor_id: UUID
    operation: str
    target_kind: str
    target_id: UUID
    draft_id: UUID | None
    draft_version: int
    created_at: datetime


def _event(row: ComposerContentAuditRow) -> ComposerContentAuditEvent:
    return ComposerContentAuditEvent(
        id=UUID(row.id),
        owner_id=UUID(row.owner_id),
        actor_id=UUID(row.actor_id),
        operation=row.operation,
        target_kind=row.target_kind,
        target_id=UUID(row.target_id),
        draft_id=UUID(row.draft_id) if row.draft_id else None,
        draft_version=row.draft_version,
        created_at=utc(row.created_at),
    )


def record_content_audit(  # noqa: PLR0913 - explicit content-free audit identity
    database: Session,
    *,
    event_id: UUID,
    owner_id: UUID,
    actor_id: UUID,
    operation: str,
    target_kind: str,
    target_id: UUID,
    draft_id: UUID | None,
    draft_version: int,
    created_at: datetime,
) -> None:
    """Stage audit evidence in the caller's mutation transaction."""

    database.add(
        ComposerContentAuditRow(
            id=str(event_id),
            owner_id=str(owner_id),
            actor_id=str(actor_id),
            operation=operation,
            target_kind=target_kind,
            target_id=str(target_id),
            draft_id=str(draft_id) if draft_id else None,
            draft_version=draft_version,
            created_at=created_at,
        )
    )


class SqlComposerAuditRepository:
    """Owner-scoped read and operator-window cleanup of Composer audit events."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._new_id = new_id

    def list_content_audit(
        self,
        owner_id: UUID,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[ComposerContentAuditEvent, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                return tuple(
                    _event(row)
                    for row in database.scalars(
                        select(ComposerContentAuditRow)
                        .where(ComposerContentAuditRow.owner_id == str(owner_id))
                        .order_by(
                            ComposerContentAuditRow.created_at.desc(),
                            ComposerContentAuditRow.id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_content_audit(self, *, cutoff_at: datetime, limit: int) -> int:
        """Delete an old bounded page while retaining immutable cleanup evidence."""

        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("Audit cleanup limit must be positive")
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                ids = tuple(
                    database.scalars(
                        select(ComposerContentAuditRow.id)
                        .where(ComposerContentAuditRow.created_at < cutoff_at)
                        .order_by(
                            ComposerContentAuditRow.created_at,
                            ComposerContentAuditRow.id,
                        )
                        .limit(limit)
                    )
                )
                removed = 0
                if ids:
                    guard_id = str(self._new_id())
                    database.execute(
                        text("INSERT INTO audit_cleanup_guards (id) VALUES (:id)"),
                        {"id": guard_id},
                    )
                    removed = int(
                        database.connection()
                        .execute(
                            delete(ComposerContentAuditRow).where(
                                ComposerContentAuditRow.id.in_(ids)
                            )
                        )
                        .rowcount
                        or 0
                    )
                    database.execute(
                        text("DELETE FROM audit_cleanup_guards WHERE id = :id"),
                        {"id": guard_id},
                    )
                database.add(
                    RetentionCleanupRunRow(
                        id=str(self._new_id()),
                        kind="composer_content_audit",
                        cutoff_at=cutoff_at,
                        removed_count=removed,
                        completed_at=self._clock(),
                    )
                )
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_t91_audit(self, *, cutoff_at: datetime, limit: int) -> int:
        """Remove one bounded page of old author/template evidence with a receipt."""

        if isinstance(limit, bool) or limit <= 0:
            raise ValueError("Audit cleanup limit must be positive")
        combined = union_all(
            select(
                AuthorKnowledgeAuditRow.id.label("id"),
                AuthorKnowledgeAuditRow.created_at.label("created_at"),
                literal("author").label("kind"),
            ).where(AuthorKnowledgeAuditRow.created_at < cutoff_at),
            select(
                TypedTemplateAuditRow.id.label("id"),
                TypedTemplateAuditRow.created_at.label("created_at"),
                literal("template").label("kind"),
            ).where(TypedTemplateAuditRow.created_at < cutoff_at),
        ).subquery()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                candidates = tuple(
                    database.execute(
                        select(combined.c.id, combined.c.kind)
                        .order_by(combined.c.created_at, combined.c.id, combined.c.kind)
                        .limit(limit)
                    )
                )
                removed = 0
                if candidates:
                    guard_id = str(self._new_id())
                    database.execute(
                        text("INSERT INTO audit_cleanup_guards (id) VALUES (:id)"),
                        {"id": guard_id},
                    )
                    for row_type, kind in (
                        (AuthorKnowledgeAuditRow, "author"),
                        (TypedTemplateAuditRow, "template"),
                    ):
                        ids = [row.id for row in candidates if row.kind == kind]
                        if ids:
                            result = database.connection().execute(
                                delete(row_type).where(row_type.id.in_(ids))
                            )
                            removed += int(result.rowcount or 0)
                    database.execute(
                        text("DELETE FROM audit_cleanup_guards WHERE id = :id"),
                        {"id": guard_id},
                    )
                database.add(
                    RetentionCleanupRunRow(
                        id=str(self._new_id()),
                        kind="composer_t91_audit",
                        cutoff_at=cutoff_at,
                        removed_count=removed,
                        completed_at=self._clock(),
                    )
                )
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None
