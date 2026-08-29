"""Transactional authentication repositories for SQLite and PostgreSQL."""

from __future__ import annotations

import builtins
import math
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, delete, event, select, text, update
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.dml import Update

from markweave.auth.models import (
    SYSTEM_ACTOR_ID,
    AuthenticationAuditContext,
    AuthenticationAuditOperation,
    ProvisionedUser,
    Role,
    Session,
    User,
)
from markweave.config import ConfigurationError
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import AuthenticationAuditRow, SessionRow, UserRow


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
        engine_options: dict[str, object] = {}
        if timeout_seconds is not None:
            if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
                raise ValueError("Database timeout must be positive and finite")
            if sqlite:
                connect_args["timeout"] = timeout_seconds
            else:
                engine_options["pool_timeout"] = timeout_seconds
                connect_args["connect_timeout"] = max(1, math.ceil(timeout_seconds))
        engine = create_engine(
            resolved_url,
            connect_args=connect_args,
            hide_parameters=True,
            pool_pre_ping=pool_pre_ping,
            **engine_options,
        )
        if sqlite:
            event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        elif timeout_seconds is not None:
            event.listen(
                engine,
                "connect",
                partial(
                    _enable_postgresql_statement_timeout,
                    milliseconds=max(1, math.ceil(timeout_seconds * 1000)),
                ),
            )
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


def _enable_postgresql_statement_timeout(
    dbapi_connection: Any,
    _connection_record: Any,
    *,
    milliseconds: int,
) -> None:
    """Add a bounded statement budget without rewriting existing libpq options."""

    previous_autocommit = dbapi_connection.autocommit
    try:
        dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (f"{milliseconds}ms",),
            )
        finally:
            cursor.close()
    finally:
        dbapi_connection.autocommit = previous_autocommit


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
        password_change_required=row.password_change_required,
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
                        password_change_required=user.password_change_required,
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

    def provision(
        self, records: builtins.list[ProvisionedUser], now: datetime
    ) -> builtins.list[User]:
        """Apply the whole startup file under one profile-neutral transaction."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if self._engine.dialect.name == "postgresql":
                    database.execute(text("SELECT pg_advisory_xact_lock(1296914258)"))
                provisioned: list[User] = []
                for record in records:
                    row = database.scalar(
                        select(UserRow)
                        .where(
                            UserRow.normalized_username == record.normalized_username
                        )
                        .with_for_update()
                    )
                    if row is None:
                        row = UserRow(
                            id=str(uuid4()),
                            username=record.username,
                            normalized_username=record.normalized_username,
                            password_hash=record.password_hash,
                            role=record.role.value,
                            active=record.active,
                            auth_version=0,
                            password_change_required=(record.password_change_required),
                        )
                        database.add(row)
                        database.flush()
                        operation = AuthenticationAuditOperation.PROVISION_CREATE
                    else:
                        row.username = record.username
                        row.password_hash = record.password_hash
                        row.role = record.role.value
                        row.active = record.active
                        row.password_change_required = record.password_change_required
                        row.auth_version += 1
                        database.execute(
                            delete(SessionRow).where(SessionRow.user_id == row.id)
                        )
                        operation = AuthenticationAuditOperation.PROVISION_UPDATE
                    user = _user(row)
                    database.add(
                        self._audit_row(
                            user,
                            AuthenticationAuditContext(
                                uuid4(), SYSTEM_ACTOR_ID, operation, now
                            ),
                        )
                    )
                    provisioned.append(user)
                return provisioned
        except IntegrityError:
            raise ConfigurationError("Invalid user provisioning file") from None
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
                serialize_sqlite_write(database, self._engine)
                result = self._update_user_row(
                    database,
                    update(UserRow)
                    .where(
                        UserRow.id == str(user_id),
                        UserRow.active.is_(True),
                        UserRow.auth_version == expected_auth_version,
                    )
                    .values(**values),
                    str(user_id),
                )
                return _user(result) if result is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def update_security(
        self,
        user_id: UUID,
        *,
        active: bool | None = None,
        password_hash: str | None = None,
        password_change_required: bool | None = None,
        audit: AuthenticationAuditContext | None = None,
    ) -> User | None:
        values: dict[str, object] = {"auth_version": UserRow.auth_version + 1}
        if active is not None:
            values["active"] = active
        if password_hash is not None:
            values["password_hash"] = password_hash
        if password_change_required is not None:
            values["password_change_required"] = password_change_required
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                result = self._update_user_row(
                    database,
                    update(UserRow).where(UserRow.id == str(user_id)).values(**values),
                    str(user_id),
                )
                if result is not None and audit is not None:
                    database.add(self._audit_row(_user(result), audit))
                return _user(result) if result is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def commit_password_change(
        self,
        user_id: UUID,
        expected_auth_version: int,
        password_hash: str,
        audit: AuthenticationAuditContext,
    ) -> User | None:
        """Compare-and-set required renewal against the authenticated snapshot."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                result = self._update_user_row(
                    database,
                    update(UserRow)
                    .where(
                        UserRow.id == str(user_id),
                        UserRow.active.is_(True),
                        UserRow.password_change_required.is_(True),
                        UserRow.auth_version == expected_auth_version,
                    )
                    .values(
                        password_hash=password_hash,
                        password_change_required=False,
                        auth_version=UserRow.auth_version + 1,
                    ),
                    str(user_id),
                )
                if result is not None:
                    database.add(self._audit_row(_user(result), audit))
                return _user(result) if result is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def _update_user_row(
        self,
        database: DatabaseSession,
        statement: Update,
        user_id: str,
    ) -> UserRow | None:
        if self._engine.dialect.name != "sqlite":
            return database.execute(statement.returning(UserRow)).scalar_one_or_none()
        result = database.execute(statement)
        if getattr(result, "rowcount", 0) != 1:
            return None
        return database.get(UserRow, user_id)

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
            administrator_intervention=audit.operation
            in {
                AuthenticationAuditOperation.CREATE,
                AuthenticationAuditOperation.DEACTIVATE,
                AuthenticationAuditOperation.REACTIVATE,
                AuthenticationAuditOperation.RESET_PASSWORD,
                AuthenticationAuditOperation.REQUIRE_PASSWORD_CHANGE,
            },
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
