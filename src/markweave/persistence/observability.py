"""Bounded SQL observability queries shared by SQLite and PostgreSQL."""

from __future__ import annotations

import math
from contextlib import suppress
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Engine,
    Integer,
    String,
    case,
    cast,
    func,
    literal,
    select,
    text,
    union_all,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.jobs.errors import JobRepositoryError
from markweave.jobs.models import JobState
from markweave.observability import AuditRecord, QueueSnapshot
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    AuthenticationAuditRow,
    ConversionJobRow,
    IdleSessionPolicyAuditRow,
    TemplateAuditRow,
)


class SqlOperationalObserver:
    """One-query queue gauges with no row or document materialization."""

    def __init__(
        self, engine: Engine, *, default_timeout_seconds: float | None = None
    ) -> None:
        if default_timeout_seconds is not None and (
            isinstance(default_timeout_seconds, bool)
            or not math.isfinite(default_timeout_seconds)
            or default_timeout_seconds <= 0
        ):
            raise ValueError("Queue observation timeout is invalid")
        self._engine = engine
        self._default_timeout_seconds = default_timeout_seconds
        self._active_lock = Lock()
        self._active_connections: dict[int, Any] = {}

    def observe_queue(
        self,
        now: datetime,
        *,
        timeout_seconds: float | None = None,
        cancelled: Event | None = None,
    ) -> QueueSnapshot:
        resolved_timeout = (
            self._default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if resolved_timeout is not None and (
            isinstance(resolved_timeout, bool)
            or not math.isfinite(resolved_timeout)
            or resolved_timeout <= 0
        ):
            raise ValueError("Queue observation timeout is invalid")
        deadline = None if resolved_timeout is None else monotonic() + resolved_timeout
        try:
            with self._engine.connect() as database:
                driver_connection = database.connection.driver_connection
                self._register(driver_connection, cancelled)
                self._configure_deadline(database, driver_connection, deadline)
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
        finally:
            if "driver_connection" in locals():
                self._clear_deadline(driver_connection)
                self._unregister(driver_connection)
        oldest = _utc(row[1])
        age = 0.0 if oldest is None else max(0.0, (now - oldest).total_seconds())
        return QueueSnapshot(int(row[0] or 0), age, int(row[2] or 0))

    def cancel_observations(self, *, timeout_seconds: float | None = None) -> None:
        """Interrupt active driver calls so listener shutdown remains bounded."""

        cancellation_timeout = timeout_seconds or self._default_timeout_seconds or 1.0
        with self._active_lock:
            connections = tuple(self._active_connections.values())
        for connection in connections:
            interrupt = getattr(connection, "interrupt", None)
            cancel_safe = getattr(connection, "cancel_safe", None)
            with suppress(Exception):
                if callable(interrupt):
                    interrupt()
                elif callable(cancel_safe):
                    cancel_safe(timeout=cancellation_timeout)

    def _register(self, connection: Any, cancelled: Event | None) -> None:
        with self._active_lock:
            self._active_connections[id(connection)] = connection
            if cancelled is not None and cancelled.is_set():
                self._active_connections.pop(id(connection), None)
                raise JobRepositoryError

    def _unregister(self, connection: Any) -> None:
        with self._active_lock:
            self._active_connections.pop(id(connection), None)

    def _configure_deadline(
        self, database: Any, driver_connection: Any, deadline: float | None
    ) -> None:
        if deadline is None:
            return
        if self._engine.dialect.name == "postgresql":
            remaining_ms = max(1, math.ceil((deadline - monotonic()) * 1000))
            database.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": f"{remaining_ms}ms"},
            )
            return
        progress = getattr(driver_connection, "set_progress_handler", None)
        if callable(progress):
            progress(lambda: int(monotonic() >= deadline), 1_000)

    def _clear_deadline(self, driver_connection: Any) -> None:
        progress = getattr(driver_connection, "set_progress_handler", None)
        if callable(progress):
            with suppress(Exception):
                progress(None, 0)


class SqlAuditReader:
    """Bounded reader for immutable content-free template audit records."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list_recent(self, *, offset: int, limit: int) -> tuple[AuditRecord, ...]:
        if offset < 0 or limit <= 0:
            raise ValueError("Audit pagination values are invalid")
        try:
            with DatabaseSession(self._engine) as database:
                combined = union_all(
                    select(
                        TemplateAuditRow.id.label("id"),
                        TemplateAuditRow.actor_id.label("actor_id"),
                        TemplateAuditRow.owner_id.label("owner_id"),
                        TemplateAuditRow.operation.label("operation"),
                        TemplateAuditRow.template_id.label("target_id"),
                        literal("template").label("target_type"),
                        TemplateAuditRow.version_id.label("target_version"),
                        TemplateAuditRow.version_id.label("version_id"),
                        TemplateAuditRow.administrator_intervention.label(
                            "administrator_intervention"
                        ),
                        TemplateAuditRow.created_at.label("created_at"),
                        literal(None, type_=Integer).label("old_user_idle_minutes"),
                        literal(None, type_=Integer).label("old_admin_idle_minutes"),
                        literal(None, type_=Integer).label("new_user_idle_minutes"),
                        literal(None, type_=Integer).label("new_admin_idle_minutes"),
                    ),
                    select(
                        AuthenticationAuditRow.id.label("id"),
                        AuthenticationAuditRow.actor_id.label("actor_id"),
                        AuthenticationAuditRow.owner_id.label("owner_id"),
                        AuthenticationAuditRow.operation.label("operation"),
                        AuthenticationAuditRow.target_id.label("target_id"),
                        literal("user").label("target_type"),
                        cast(AuthenticationAuditRow.auth_version, String).label(
                            "target_version"
                        ),
                        literal(None, type_=String).label("version_id"),
                        AuthenticationAuditRow.administrator_intervention.label(
                            "administrator_intervention"
                        ),
                        AuthenticationAuditRow.created_at.label("created_at"),
                        literal(None, type_=Integer).label("old_user_idle_minutes"),
                        literal(None, type_=Integer).label("old_admin_idle_minutes"),
                        literal(None, type_=Integer).label("new_user_idle_minutes"),
                        literal(None, type_=Integer).label("new_admin_idle_minutes"),
                    ),
                    select(
                        IdleSessionPolicyAuditRow.id.label("id"),
                        IdleSessionPolicyAuditRow.actor_id.label("actor_id"),
                        IdleSessionPolicyAuditRow.actor_id.label("owner_id"),
                        IdleSessionPolicyAuditRow.operation.label("operation"),
                        literal("00000000-0000-0000-0000-000000000001").label(
                            "target_id"
                        ),
                        literal("session_policy").label("target_type"),
                        cast(IdleSessionPolicyAuditRow.revision, String).label(
                            "target_version"
                        ),
                        literal(None, type_=String).label("version_id"),
                        literal(True).label("administrator_intervention"),
                        IdleSessionPolicyAuditRow.created_at.label("created_at"),
                        IdleSessionPolicyAuditRow.old_user_idle_minutes.label(
                            "old_user_idle_minutes"
                        ),
                        IdleSessionPolicyAuditRow.old_admin_idle_minutes.label(
                            "old_admin_idle_minutes"
                        ),
                        IdleSessionPolicyAuditRow.new_user_idle_minutes.label(
                            "new_user_idle_minutes"
                        ),
                        IdleSessionPolicyAuditRow.new_admin_idle_minutes.label(
                            "new_admin_idle_minutes"
                        ),
                    ),
                ).subquery()
                rows = database.execute(
                    select(combined)
                    .order_by(
                        combined.c.created_at.desc(),
                        combined.c.id.desc(),
                        combined.c.target_type.desc(),
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
                        target_id=UUID(row.target_id),
                        target_type=row.target_type,
                        target_version=row.target_version,
                        version_id=UUID(row.version_id) if row.version_id else None,
                        administrator_intervention=row.administrator_intervention,
                        created_at=_required_utc(row.created_at),
                        old_user_idle_minutes=getattr(
                            row, "old_user_idle_minutes", None
                        ),
                        old_admin_idle_minutes=getattr(
                            row, "old_admin_idle_minutes", None
                        ),
                        new_user_idle_minutes=getattr(
                            row, "new_user_idle_minutes", None
                        ),
                        new_admin_idle_minutes=getattr(
                            row, "new_admin_idle_minutes", None
                        ),
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
