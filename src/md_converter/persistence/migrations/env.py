"""Alembic environment driven by an already-open application connection."""

from alembic import context  # pragma: no cover - Alembic loader entrypoint

from md_converter.persistence.migrations import (  # pragma: no cover
    run_migration_environment,
)

run_migration_environment(context)  # pragma: no cover
