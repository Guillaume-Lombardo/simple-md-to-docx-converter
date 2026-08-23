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


def upgrade_database(engine: Engine) -> None:
    """Upgrade a database through Alembic without logging its connection URL."""
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
            command.upgrade(configuration, "head")
    except CommandError, SQLAlchemyError:
        raise PersistenceError from None


__all__ = ["run_migration_environment", "upgrade_database"]
