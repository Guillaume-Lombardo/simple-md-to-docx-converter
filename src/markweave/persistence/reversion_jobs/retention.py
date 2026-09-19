"""Fenced expiration and object-cleanup acknowledgement."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.reversion_jobs.common import (
    _SqlReversionStore,
)
from markweave.persistence.schema import (
    ReversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.reversion_jobs.errors import (
    ReversionJobRepositoryError,
)
from markweave.reversion_jobs.models import (
    TERMINAL_REVERSION_STATES,
    ExpiredReversionObjects,
    ReversionJobState,
    reversion_result_object_id,
)


class _SqlReversionRetention(_SqlReversionStore):
    """Fenced expiration and object-cleanup acknowledgement."""

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredReversionObjects, ...]:
        terminal = tuple(
            state.value
            for state in TERMINAL_REVERSION_STATES
            if state is not ReversionJobState.EXPIRED
        )
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                claimable = or_(
                    and_(
                        ReversionJobRow.state.in_(terminal),
                        ReversionJobRow.expires_at <= now,
                    ),
                    and_(
                        ReversionJobRow.state == ReversionJobState.EXPIRED.value,
                        ReversionJobRow.cleanup_completed.is_(False),
                        or_(
                            ReversionJobRow.cleanup_token.is_(None),
                            ReversionJobRow.cleanup_expires_at <= now,
                        ),
                    ),
                )
                statement = (
                    select(ReversionJobRow)
                    .where(claimable)
                    .order_by(ReversionJobRow.expires_at, ReversionJobRow.id)
                    .limit(limit)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                rows = tuple(database.scalars(statement))
                expired: list[ExpiredReversionObjects] = []
                for row in rows:
                    token = uuid4()
                    derived = tuple(
                        reversion_result_object_id(UUID(row.id), number)
                        for number in range(1, row.attempt + 1)
                    )
                    stored = (
                        UUID(row.result_object_id) if row.result_object_id else None
                    )
                    if stored is not None and stored not in derived:
                        derived = (*derived, stored)
                    row.state = ReversionJobState.EXPIRED.value
                    row.error_code = None
                    row.error_message = None
                    row.result_mode = None
                    row.result_object_id = None
                    row.result_sha256 = None
                    row.result_size = None
                    row.trace_metadata = None
                    row.updated_at = now
                    row.cleanup_owner = worker_id
                    row.cleanup_token = str(token)
                    row.cleanup_expires_at = cleanup_lease_expires_at
                    expired.append(
                        ExpiredReversionObjects(
                            UUID(row.id),
                            token,
                            UUID(row.owner_id),
                            UUID(row.source_object_id),
                            derived,
                        )
                    )
                database.flush()
                return tuple(expired)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                result = database.execute(
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.state == ReversionJobState.EXPIRED.value,
                        ReversionJobRow.cleanup_completed.is_(False),
                        ReversionJobRow.cleanup_token == str(cleanup_token),
                    )
                    .values(
                        cleanup_completed=True,
                        cleanup_owner=None,
                        cleanup_token=None,
                        cleanup_expires_at=None,
                    )
                )
                return getattr(result, "rowcount", 0) == 1
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None
