"""Alembic migration runner used by both runtime profiles."""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import Base

POSTGRES_MIGRATION_LOCK = 720_012


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


def _migrate_database(engine: Engine, revision: str, *, downgrade: bool) -> None:
    """Run one application-managed Alembic direction under the profile lock."""

    script_location = Path(__file__).parent
    configuration = Config()
    configuration.set_main_option("script_location", str(script_location))
    try:
        with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": POSTGRES_MIGRATION_LOCK},
                )
            configuration.attributes["connection"] = connection
            operation = command.downgrade if downgrade else command.upgrade
            operation(configuration, revision)
    except CommandError, SQLAlchemyError:
        raise PersistenceError from None


def upgrade_database(engine: Engine) -> None:
    """Upgrade a database through Alembic without logging its connection URL."""

    _migrate_database(engine, "head", downgrade=False)


def downgrade_database(engine: Engine, revision: str) -> None:
    """Downgrade to an explicit revision for verified operational rollback."""

    if not revision.strip():
        raise ValueError("Migration revision must not be blank")
    _migrate_database(engine, revision, downgrade=True)


__all__ = ["downgrade_database", "run_migration_environment", "upgrade_database"]
