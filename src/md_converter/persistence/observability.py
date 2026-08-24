"""Bounded SQL observability queries shared by SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from md_converter.jobs.errors import JobRepositoryError
from md_converter.jobs.models import JobState
from md_converter.observability import AuditRecord, QueueSnapshot
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import ConversionJobRow, TemplateAuditRow


class SqlOperationalObserver:
    """One-query queue gauges with no row or document materialization."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def observe_queue(self, now: datetime) -> QueueSnapshot:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.execute(
                    select(
                        func.count(
                            case(
                                (
                                    ConversionJobRow.state == JobState.QUEUED.value,
                                    1,
                                )
                            )
                        ),
                        func.min(
                            case(
                                (
                                    ConversionJobRow.state == JobState.QUEUED.value,
                                    ConversionJobRow.created_at,
                                )
                            )
                        ),
                        func.count(
                            case(
                                (
                                    ConversionJobRow.state == JobState.RUNNING.value,
                                    1,
                                )
                            )
                        ),
                    )
                ).one()
        except SQLAlchemyError, ValueError, TypeError:
            raise JobRepositoryError from None
        oldest = _utc(row[1])
        age = 0.0 if oldest is None else max(0.0, (now - oldest).total_seconds())
        return QueueSnapshot(int(row[0] or 0), age, int(row[2] or 0))


class SqlAuditReader:
    """Bounded reader for immutable content-free template audit records."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_recent(self, *, offset: int, limit: int) -> tuple[AuditRecord, ...]:
        if offset < 0 or limit <= 0:
            raise ValueError("Audit pagination values are invalid")
        try:
            with DatabaseSession(self._engine) as database:
                rows = database.scalars(
                    select(TemplateAuditRow)
                    .order_by(
                        TemplateAuditRow.created_at.desc(),
                        TemplateAuditRow.id.desc(),
                    )
                    .offset(offset)
                    .limit(limit)
                )
                return tuple(
                    AuditRecord(
                        id=UUID(row.id),
                        actor_id=UUID(row.actor_id),
                        owner_id=UUID(row.owner_id),
                        operation=row.operation,
                        target_id=UUID(row.template_id),
                        version_id=UUID(row.version_id) if row.version_id else None,
                        administrator_intervention=row.administrator_intervention,
                        created_at=_required_utc(row.created_at),
                    )
                    for row in rows
                )
        except SQLAlchemyError, ValueError, TypeError:
            raise PersistenceError from None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _required_utc(value: datetime) -> datetime:
    resolved = _utc(value)
    if resolved is None:  # pragma: no cover - schema forbids this state
        raise PersistenceError
    return resolved
