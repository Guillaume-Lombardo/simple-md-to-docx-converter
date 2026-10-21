"""Schema-revision contract for recovery sets created by earlier releases.

A recovery set records the Alembic revision of the database it captured. Restore
accepts a set only when that revision belongs to this release's linear migration
history at or after ``OLDEST_RECOVERY_SET_REVISION``. The set is verified against
the schema that its own revision defines, then migrated forward to the current
head. Unknown revisions, including every revision created by a newer release, are
refused before any target is mutated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import cache
from types import MappingProxyType
from typing import Any

from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import migration_lineage, upgrade_database_to
from markweave.persistence.sql import create_database_engine
from markweave.recovery_manifest import RecoveryError

# Owner decision (2026-10-11, T128): the minimum supported recovery-set revision is the
# migration head of release 0.5.0, which introduced `markweave backup` and the
# `markweave-recovery-set-v1` manifest. Earlier revisions are refused.
OLDEST_RECOVERY_SET_REVISION = "20260829_14"
# Migration-owned tables that are never part of the application model metadata.
AUXILIARY_TABLES = frozenset({"alembic_version", "audit_cleanup_guards"})
# Columns that carry stable object references, read at the recovery-set revision.
REFERENCE_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "template_versions": ("id", "object_owner_id", "publication_state"),
        "conversion_jobs": (
            "owner_id",
            "source_object_id",
            "source_ready",
            "state",
            "cleanup_completed",
            "result_object_id",
            "result_manifest_object_id",
        ),
        "composer_sources": ("id", "owner_id", "publication_state"),
        "composer_drafts": ("id", "owner_id"),
        "composer_revisions": ("id", "draft_id", "publication_state"),
        "composer_artifacts": ("id", "revision_id"),
        "composer_library_versions": ("id", "object_owner_id", "publication_state"),
    }
)

type SchemaShape = Mapping[str, frozenset[str]]


@cache
def _lineage() -> tuple[str, ...]:
    try:
        return migration_lineage()
    except PersistenceError:
        raise RecoveryError("Migration history is unavailable") from None


def head_revision() -> str:
    """Return the single migration head of this release."""

    return _lineage()[-1]


def require_restorable_revision(revision: object) -> str:
    """Accept only a known revision between the oldest recovery set and head."""

    lineage = _lineage()
    if not isinstance(revision, str) or revision not in lineage:
        raise RecoveryError("Recovery set schema revision is unknown to this release")
    if lineage.index(revision) < lineage.index(OLDEST_RECOVERY_SET_REVISION):
        raise RecoveryError(
            "Recovery set schema revision predates supported recovery sets"
        )
    return revision


def sqlite_revision(connection: sqlite3.Connection) -> str:
    """Read the single Alembic revision recorded by a restored SQLite database."""

    try:
        revisions = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
    except sqlite3.Error:
        raise RecoveryError("Restored database schema is incomplete") from None
    if len(revisions) != 1 or not isinstance(revisions[0][0], str):
        raise RecoveryError("Restored database schema is incomplete")
    return revisions[0][0]


def sqlite_shape(connection: sqlite3.Connection) -> SchemaShape:
    """Return application tables and their column names from a SQLite database."""

    try:
        names = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
            ).fetchall()
        )
        return MappingProxyType(
            {
                name: frozenset(
                    row[1]
                    for row in connection.execute(
                        "SELECT * FROM pragma_table_info(?)", (name,)
                    ).fetchall()
                )
                for name in names
            }
        )
    except sqlite3.Error:
        raise RecoveryError("Restored database schema is incomplete") from None


@cache
def reference_shape(revision: str) -> SchemaShape:
    """Build the schema that the migration history defines at one revision.

    The reference database lives only in memory, so read-only or size-limited
    container filesystems never constrain verification.
    """

    require_restorable_revision(revision)
    # SQLAlchemy keeps one in-memory connection per thread for this URL, so the
    # migration and the inspection below observe the same database.
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    try:
        upgrade_database_to(engine, revision)
        with engine.connect() as connection:
            driver = connection.connection.driver_connection
            if not isinstance(driver, sqlite3.Connection):  # pragma: no cover
                raise RecoveryError("Recovery reference schema is unavailable")
            return sqlite_shape(driver)
    except PersistenceError:
        raise RecoveryError("Recovery reference schema is unavailable") from None
    finally:
        engine.dispose()


def require_complete_shape(observed: SchemaShape, reference: SchemaShape) -> None:
    """Refuse a database lacking any table or column of its recorded revision."""

    for table, columns in reference.items():
        present = observed.get(table)
        if present is None or not columns.issubset(present):
            raise RecoveryError("Restored database schema is incomplete")


def read_reference_rows(
    query: Callable[[str], Iterable[Sequence[Any]]], shape: SchemaShape
) -> dict[str, dict[str, Any]]:
    """Read only the reference-bearing columns that exist at the shape's revision."""

    rows: dict[str, dict[str, Any]] = {}
    for table, columns in REFERENCE_COLUMNS.items():
        available = shape.get(table)
        if available is None:
            continue
        selected = tuple(column for column in columns if column in available)
        statement = f"SELECT {', '.join(selected)} FROM {table}"  # noqa: S608 - fixed internal names
        rows[table] = {
            "rows": [dict(zip(selected, row, strict=True)) for row in query(statement)]
        }
    return rows


__all__ = [
    "AUXILIARY_TABLES",
    "OLDEST_RECOVERY_SET_REVISION",
    "REFERENCE_COLUMNS",
    "SchemaShape",
    "head_revision",
    "read_reference_rows",
    "reference_shape",
    "require_complete_shape",
    "require_restorable_revision",
    "sqlite_revision",
    "sqlite_shape",
]
