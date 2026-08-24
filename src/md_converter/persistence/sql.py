"""Transactional authentication repositories for SQLite and PostgreSQL."""

from __future__ import annotations

import builtins
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, delete, event, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from md_converter.auth.models import (
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    Role,
    Session,
    User,
)
from md_converter.config import ConfigurationError
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import AuthenticationAuditRow, SessionRow, UserRow


def create_database_engine(
    database_url: str | URL,
    *,
    timeout_seconds: float | None = None,
    pool_pre_ping: bool = True,
) -> Engine:
    """Create a profile-neutral synchronous SQLAlchemy engine."""
    try:
        if isinstance(database_url, str) and database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        resolved_url = (
            make_url(database_url) if isinstance(database_url, str) else database_url
        )
        sqlite = resolved_url.get_backend_name() == "sqlite"
        connect_args: dict[str, object] = {"check_same_thread": False} if sqlite else {}
        if timeout_seconds is not None:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise ValueError("Database timeout must be positive and finite")
            if sqlite:
                connect_args["timeout"] = timeout_seconds
            else:
                connect_args.update(
                    {
                        "connect_timeout": max(1, math.ceil(timeout_seconds)),
                        "options": (
                            "-c statement_timeout="
                            f"{max(1, math.ceil(timeout_seconds * 1000))}"
                        ),
                    }
                )
        engine = create_engine(
            resolved_url,
            connect_args=connect_args,
            hide_parameters=True,
            pool_pre_ping=pool_pre_ping,
        )
        if sqlite:
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine
    except SQLAlchemyError:
        raise PersistenceError from None


def serialize_sqlite_write(database: DatabaseSession, engine: Engine) -> None:
    """Acquire SQLite's write reservation before reading mutation preconditions."""
    if engine.dialect.name == "sqlite":
        database.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def standalone_database_url(data_directory: Path) -> URL:
    """Build the fixed SQLite metadata location below the standalone PVC root."""
    return URL.create(
        drivername="sqlite+pysqlite",
        database=str(data_directory / "metadata.sqlite3"),
    )


def _user(row: UserRow) -> User:
    return User(
        id=UUID(row.id),
        username=row.username,
        normalized_username=row.normalized_username,
        password_hash=row.password_hash,
        role=Role(row.role),
        active=row.active,
        auth_version=row.auth_version,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _session(row: SessionRow) -> Session:
    return Session(
        token_digest=row.token_digest,
        csrf_digest=row.csrf_digest,
        user_id=UUID(row.user_id),
        auth_version=row.auth_version,
        created_at=_utc(row.created_at),
        last_seen_at=_utc(row.last_seen_at),
        idle_expires_at=_utc(row.idle_expires_at),
        absolute_expires_at=_utc(row.absolute_expires_at),
    )


class SqlUserRepository:
    """User repository whose mutations are single database transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def bootstrap_admin(
        self, username: str, normalized_username: str, password_hash: str
    ) -> User:
        user = User(
            id=uuid4(),
            username=username.strip(),
            normalized_username=normalized_username,
            password_hash=password_hash,
            role=Role.ADMIN,
        )
        try:
            self.create(
                user,
                audit=AuthenticationAuditContext(
                    uuid4(),
                    user.id,
                    AuthenticationAuditOperation.BOOTSTRAP_ADMIN_CREATE,
                    datetime.now(UTC),
                ),
            )
            return user
        except KeyError:
            existing = self.get_by_normalized_username(normalized_username)
            if existing is None or existing.role is not Role.ADMIN:
                raise ConfigurationError(
                    "Initial administrator conflicts with an account"
                ) from None
            return existing

    def create(
        self, user: User, *, audit: AuthenticationAuditContext | None = None
    ) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.add(
                    UserRow(
                        id=str(user.id),
                        username=user.username,
                        normalized_username=user.normalized_username,
                        password_hash=user.password_hash,
                        role=user.role.value,
                        active=user.active,
                        auth_version=user.auth_version,
                    )
                )
                if audit is not None:
                    database.add(self._audit_row(user, audit))
        except IntegrityError:
            raise KeyError(user.normalized_username) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_by_id(self, user_id: UUID) -> User | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(UserRow, str(user_id))
                return _user(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_by_normalized_username(self, normalized_username: str) -> User | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(UserRow).where(
                        UserRow.normalized_username == normalized_username
                    )
                )
                return _user(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def list(self) -> builtins.list[User]:
        try:
            with DatabaseSession(self._engine) as database:
                rows = database.scalars(
                    select(UserRow).order_by(UserRow.normalized_username)
                )
                return [_user(row) for row in rows]
        except SQLAlchemyError:
            raise PersistenceError from None

    def commit_verified_login(
        self,
        user_id: UUID,
        expected_auth_version: int,
        replacement_hash: str | None,
    ) -> User | None:
        values: dict[str, object] = {"auth_version": UserRow.auth_version}
        if replacement_hash is not None:
            values["password_hash"] = replacement_hash
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                result = database.execute(
                    update(UserRow)
                    .where(
                        UserRow.id == str(user_id),
                        UserRow.active.is_(True),
                        UserRow.auth_version == expected_auth_version,
                    )
                    .values(**values)
                    .returning(UserRow)
                ).scalar_one_or_none()
                return _user(result) if result is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def update_security(
        self,
        user_id: UUID,
        *,
        active: bool | None = None,
        password_hash: str | None = None,
        audit: AuthenticationAuditContext | None = None,
    ) -> User | None:
        values: dict[str, object] = {"auth_version": UserRow.auth_version + 1}
        if active is not None:
            values["active"] = active
        if password_hash is not None:
            values["password_hash"] = password_hash
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                result = database.execute(
                    update(UserRow)
                    .where(UserRow.id == str(user_id))
                    .values(**values)
                    .returning(UserRow)
                ).scalar_one_or_none()
                if result is not None and audit is not None:
                    database.add(self._audit_row(_user(result), audit))
                return _user(result) if result is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _audit_row(
        user: User, audit: AuthenticationAuditContext
    ) -> AuthenticationAuditRow:
        return AuthenticationAuditRow(
            id=str(audit.id),
            actor_id=str(audit.actor_id),
            owner_id=str(user.id),
            operation=audit.operation.value,
            target_id=str(user.id),
            auth_version=user.auth_version,
            administrator_intervention=(
                audit.operation
                is not AuthenticationAuditOperation.BOOTSTRAP_ADMIN_CREATE
            ),
            created_at=audit.created_at,
        )


class SqlSessionRepository:
    """Persistent digest-only session repository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, session: Session) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.add(self._row(session))
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, token_digest: str) -> Session | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(SessionRow, token_digest)
                return _session(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def save(self, session: Session) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.execute(
                    update(SessionRow)
                    .where(SessionRow.token_digest == session.token_digest)
                    .values(
                        csrf_digest=session.csrf_digest,
                        user_id=str(session.user_id),
                        auth_version=session.auth_version,
                        created_at=session.created_at,
                        last_seen_at=session.last_seen_at,
                        idle_expires_at=session.idle_expires_at,
                        absolute_expires_at=session.absolute_expires_at,
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def revoke(self, token_digest: str) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.execute(
                    delete(SessionRow).where(SessionRow.token_digest == token_digest)
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def revoke_user(self, user_id: UUID) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.execute(
                    delete(SessionRow).where(SessionRow.user_id == str(user_id))
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _row(session: Session) -> SessionRow:
        return SessionRow(
            token_digest=session.token_digest,
            csrf_digest=session.csrf_digest,
            user_id=str(session.user_id),
            auth_version=session.auth_version,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            idle_expires_at=session.idle_expires_at,
            absolute_expires_at=session.absolute_expires_at,
        )


class DatabaseReadinessProbe:
    """Cheap connection-only readiness check for the selected database."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def is_ready(self) -> bool:
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:  # readiness deliberately converts adapter failures to false
            return False
        return True
