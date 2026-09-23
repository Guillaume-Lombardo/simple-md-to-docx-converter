"""Transactional administrator approval for Composer model egress."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.connections import ConnectionConflictError
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerAdminPolicyAuditRow,
    ComposerAdminPolicyRow,
)
from markweave.persistence.sql import serialize_sqlite_write


@dataclass(frozen=True, slots=True)
class StoredAdminPolicy:
    enabled: bool
    destinations: tuple[str, ...]
    networks: tuple[str, ...]
    version: int


class SqlComposerAdminPolicyRepository:
    """One compare-and-swap policy row for SQLite and PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self) -> StoredAdminPolicy | None:
        try:
            with Session(self._engine) as database:
                row = database.get(ComposerAdminPolicyRow, 1)
                return _from_row(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def put(  # noqa: PLR0913 - CAS audit needs actor and stored-policy baseline
        self,
        *,
        enabled: bool,
        destinations: tuple[str, ...],
        networks: tuple[str, ...],
        expected_version: int,
        actor_id: UUID,
        default_enabled: bool,
    ) -> StoredAdminPolicy:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ComposerAdminPolicyRow)
                    .where(ComposerAdminPolicyRow.id == 1)
                    .with_for_update()
                )
                current_version = row.version if row is not None else 0
                if current_version != expected_version:
                    raise ConnectionConflictError("Composer policy changed")
                old_enabled = row.enabled if row is not None else default_enabled
                if row is None:
                    row = ComposerAdminPolicyRow(id=1, version=1)
                    database.add(row)
                else:
                    row.version += 1
                row.enabled = enabled
                row.destinations = json.dumps(destinations, separators=(",", ":"))
                row.networks = json.dumps(networks, separators=(",", ":"))
                database.add(
                    ComposerAdminPolicyAuditRow(
                        id=str(uuid4()),
                        actor_id=str(actor_id),
                        old_enabled=old_enabled,
                        new_enabled=enabled,
                        version=row.version,
                        created_at=datetime.now(UTC),
                    )
                )
                database.flush()
                return _from_row(row)
        except IntegrityError:
            # PostgreSQL cannot lock a singleton row before its first insert.
            raise ConnectionConflictError("Composer policy changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None


def _from_row(row: ComposerAdminPolicyRow) -> StoredAdminPolicy:
    try:
        destinations = json.loads(row.destinations)
        networks = json.loads(row.networks)
        if (
            not isinstance(destinations, list)
            or not isinstance(networks, list)
            or any(not isinstance(item, str) for item in (*destinations, *networks))
        ):
            raise ValueError
        return StoredAdminPolicy(
            bool(row.enabled), tuple(destinations), tuple(networks), row.version
        )
    except ValueError, TypeError:
        raise PersistenceError from None
