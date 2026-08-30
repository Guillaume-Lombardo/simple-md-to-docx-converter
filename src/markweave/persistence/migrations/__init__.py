"""Alembic migration runner used by both runtime profiles."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import Base

POSTGRES_MIGRATION_LOCK = 720_012


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Database revision transition observed under the migration lock."""

    previous_revision: str | None
    current_revision: str | None


def _current_revision(connection: Connection) -> str | None:
    """Read the single Alembic revision without logging database details."""
    if connection.dialect.name == "sqlite":
        exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'alembic_version'"
        ).scalar_one_or_none()
    elif connection.dialect.name == "postgresql":
        exists = connection.exec_driver_sql(
            "SELECT to_regclass('alembic_version')"
        ).scalar_one_or_none()
    else:
        raise PersistenceError
    if exists is None:
        return None
    revisions = tuple(
        connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars()
    )
    if len(revisions) != 1 or not isinstance(revisions[0], str):
        raise PersistenceError
    return revisions[0]


def run_migration_environment(context: Any) -> None:
    """Configure Alembic around the application-managed connection."""
    connection = context.config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError(
            "Alembic requires an application-managed database connection"
        )
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _migrate_database(
    engine: Engine, revision: str, *, downgrade: bool, observe: bool = False
) -> MigrationResult | None:
    """Run one application-managed Alembic direction under the profile lock."""

    script_location = Path(__file__).parent
    configuration = Config()
    configuration.set_main_option("script_location", str(script_location))
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": POSTGRES_MIGRATION_LOCK},
                )
            configuration.attributes["connection"] = connection
            previous_revision = _current_revision(connection) if observe else None
            operation = command.downgrade if downgrade else command.upgrade
            operation(configuration, revision)
            if observe:
                return MigrationResult(previous_revision, _current_revision(connection))
            return None
    except CommandError, SQLAlchemyError:
        raise PersistenceError from None


def upgrade_database(engine: Engine) -> None:
    """Upgrade a database through Alembic without logging its connection URL."""

    _migrate_database(engine, "head", downgrade=False)


def upgrade_database_observed(engine: Engine) -> MigrationResult:
    """Upgrade and report the locked revision transition for operator output."""
    result = _migrate_database(engine, "head", downgrade=False, observe=True)
    if result is None:  # pragma: no cover - fixed by the observe argument
        raise PersistenceError
    return result


def downgrade_database(engine: Engine, revision: str) -> None:
    """Downgrade to an explicit revision for verified operational rollback."""

    if not revision.strip():
        raise ValueError("Migration revision must not be blank")
    _migrate_database(engine, revision, downgrade=True)


__all__ = [
    "MigrationResult",
    "downgrade_database",
    "run_migration_environment",
    "upgrade_database",
    "upgrade_database_observed",
]
