"""SQL-filtered author directory and transactional share/revoke fences."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, exists, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import (
    AuthorField,
    AuthorKnowledgeConflictError,
    AuthorKnowledgeLimits,
    AuthorKnowledgeNotFoundError,
    AuthorRecord,
    validate_author,
)
from markweave.persistence.composer.audit import record_content_audit
from markweave.persistence.composer.common import validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    AuthorKnowledgeAuditRow,
    AuthorKnowledgeGrantRow,
    AuthorKnowledgeRow,
    UserRow,
)
from markweave.persistence.sql import serialize_sqlite_write


def _fields_json(fields: dict[str, AuthorField]) -> str:
    return json.dumps(
        {
            key: {
                "value": value.value,
                "provenance": value.provenance,
                "source_reference": value.source_reference,
            }
            for key, value in sorted(fields.items())
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validated_fields(
    name: str, fields_json: str, limits: AuthorKnowledgeLimits
) -> dict[str, AuthorField]:
    if not isinstance(fields_json, str):
        raise ValueError("Author fields must be JSON")
    try:
        raw = json.loads(fields_json)
    except ValueError:
        raise ValueError("Author fields must be JSON") from None
    if not isinstance(raw, dict):
        raise ValueError("Author fields must be an object")
    fields: dict[str, AuthorField] = {}
    for key, value in raw.items():
        if (
            not isinstance(value, dict)
            or set(value) - {"value", "provenance", "source_reference"}
            or "value" not in value
            or "provenance" not in value
        ):
            raise ValueError("Author field is invalid")
        fields[key] = AuthorField(**value)
    validate_author(name, fields, limits)
    return fields


def _record(database: Session, row: AuthorKnowledgeRow) -> AuthorRecord:
    raw = json.loads(row.fields_json)
    fields = {key: AuthorField(**value) for key, value in raw.items()}
    grants = tuple(
        UUID(user_id)
        for user_id in database.scalars(
            select(AuthorKnowledgeGrantRow.user_id)
            .where(AuthorKnowledgeGrantRow.author_id == row.id)
            .order_by(AuthorKnowledgeGrantRow.user_id)
        )
    )
    return AuthorRecord(
        UUID(row.id),
        UUID(row.owner_id),
        row.name,
        fields,
        row.version,
        grants,
        row.created_at.replace(tzinfo=UTC)
        if row.created_at.tzinfo is None
        else row.created_at,
        row.updated_at.replace(tzinfo=UTC)
        if row.updated_at.tzinfo is None
        else row.updated_at,
    )


class SqlAuthorKnowledgeRepository:
    """One author directory contract for SQLite and PostgreSQL."""

    def __init__(
        self,
        engine: Engine,
        limits: AuthorKnowledgeLimits,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._engine = engine
        self._limits = limits
        self._clock = clock
        self._new_id = new_id

    @staticmethod
    def require_access(
        database: Session,
        actor_id: UUID,
        author_id: UUID,
        *,
        expected_version: int | None = None,
        for_update: bool = False,
    ) -> AuthorKnowledgeRow:
        """Authorize with SQL before content leaves the database transaction."""

        visible = or_(
            AuthorKnowledgeRow.owner_id == str(actor_id),
            exists(
                select(AuthorKnowledgeGrantRow.author_id).where(
                    AuthorKnowledgeGrantRow.author_id == AuthorKnowledgeRow.id,
                    AuthorKnowledgeGrantRow.user_id == str(actor_id),
                )
            ),
        )
        statement = select(AuthorKnowledgeRow).where(
            AuthorKnowledgeRow.id == str(author_id),
            visible,
            exists(
                select(UserRow.id).where(
                    UserRow.id == str(actor_id), UserRow.active.is_(True)
                )
            ),
        )
        if for_update:
            statement = statement.with_for_update()
        row = database.scalar(statement)
        if row is None:
            raise AuthorKnowledgeNotFoundError("Author entry does not exist")
        if expected_version is not None and row.version != expected_version:
            raise AuthorKnowledgeConflictError("Author entry changed")
        return row

    def create(self, owner_id: UUID, name: str, fields_json: str) -> AuthorRecord:
        fields = _validated_fields(name, fields_json, self._limits)
        now, author_id = self._clock(), self._new_id()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if (
                    database.scalar(
                        select(UserRow.id).where(
                            UserRow.id == str(owner_id), UserRow.active.is_(True)
                        )
                    )
                    is None
                ):
                    raise AuthorKnowledgeNotFoundError("Author owner does not exist")
                row = AuthorKnowledgeRow(
                    id=str(author_id),
                    owner_id=str(owner_id),
                    name=name.strip(),
                    fields_json=_fields_json(fields),
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                database.add(row)
                self._audit(database, row, owner_id, "create")
                database.flush()
                return _record(database, row)
        except AuthorKnowledgeNotFoundError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, actor_id: UUID, author_id: UUID) -> AuthorRecord:
        try:
            with Session(self._engine) as database:
                return _record(
                    database, self.require_access(database, actor_id, author_id)
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_visible(
        self,
        actor_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
    ) -> tuple[AuthorRecord, ...]:
        validate_page(limit, offset)
        if query is not None and (
            not isinstance(query, str) or len(query) > self._limits.max_name_length
        ):
            raise ValueError("Author search query is invalid")
        try:
            with Session(self._engine) as database:
                statement = select(AuthorKnowledgeRow).where(
                    or_(
                        AuthorKnowledgeRow.owner_id == str(actor_id),
                        exists(
                            select(AuthorKnowledgeGrantRow.author_id).where(
                                AuthorKnowledgeGrantRow.author_id
                                == AuthorKnowledgeRow.id,
                                AuthorKnowledgeGrantRow.user_id == str(actor_id),
                            )
                        ),
                    ),
                    exists(
                        select(UserRow.id).where(
                            UserRow.id == str(actor_id), UserRow.active.is_(True)
                        )
                    ),
                )
                if query:
                    statement = statement.where(
                        AuthorKnowledgeRow.name.icontains(query, autoescape=True)
                    )
                rows = database.scalars(
                    statement.order_by(AuthorKnowledgeRow.name, AuthorKnowledgeRow.id)
                    .limit(limit)
                    .offset(offset)
                ).all()
                return tuple(_record(database, row) for row in rows)
        except SQLAlchemyError:
            raise PersistenceError from None

    def update(  # noqa: PLR0913 - explicit mutation preconditions
        self,
        actor_id: UUID,
        author_id: UUID,
        *,
        if_match: str,
        name: str,
        fields_json: str,
        is_admin: bool = False,
    ) -> AuthorRecord:
        fields = _validated_fields(name, fields_json, self._limits)
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, author_id, if_match, is_admin)
                previous = json.loads(row.fields_json)
                for key, field in fields.items():
                    old = previous.get(key)
                    if (
                        isinstance(old, dict)
                        and old.get("provenance") in {"human_approved", "human_edited"}
                        and field.provenance == "model_suggested"
                    ):
                        raise AuthorKnowledgeConflictError(
                            "Model suggestion requires review before replacing a human value"
                        )
                row.name = name.strip()
                row.fields_json = _fields_json(fields)
                row.version += 1
                row.updated_at = self._clock()
                self._audit(database, row, actor_id, "update", is_admin=is_admin)
                database.flush()
                return _record(database, row)
        except AuthorKnowledgeNotFoundError, AuthorKnowledgeConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def grant(
        self,
        actor_id: UUID,
        author_id: UUID,
        user_id: UUID,
        *,
        if_match: str,
        is_admin: bool = False,
    ) -> AuthorRecord:
        return self._change_grant(
            actor_id, author_id, user_id, if_match, is_admin, grant=True
        )

    def revoke(
        self,
        actor_id: UUID,
        author_id: UUID,
        user_id: UUID,
        *,
        if_match: str,
        is_admin: bool = False,
    ) -> AuthorRecord:
        return self._change_grant(
            actor_id, author_id, user_id, if_match, is_admin, grant=False
        )

    def _change_grant(  # noqa: PLR0913 - explicit grant preconditions
        self,
        actor_id: UUID,
        author_id: UUID,
        user_id: UUID,
        if_match: str,
        is_admin: bool,
        *,
        grant: bool,
    ) -> AuthorRecord:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, author_id, if_match, is_admin)
                if user_id == UUID(row.owner_id):
                    raise ValueError("Owner access cannot be changed")
                target = database.scalar(
                    select(UserRow.id).where(
                        UserRow.id == str(user_id), UserRow.active.is_(True)
                    )
                )
                if target is None:
                    raise AuthorKnowledgeNotFoundError("Target user does not exist")
                existing = database.get(
                    AuthorKnowledgeGrantRow, (str(author_id), str(user_id))
                )
                if (grant and existing is not None) or (not grant and existing is None):
                    raise AuthorKnowledgeConflictError("Author grant state changed")
                if grant:
                    database.add(
                        AuthorKnowledgeGrantRow(
                            author_id=str(author_id),
                            user_id=str(user_id),
                            created_at=self._clock(),
                        )
                    )
                else:
                    database.delete(existing)
                row.version += 1
                row.updated_at = self._clock()
                self._audit(
                    database,
                    row,
                    actor_id,
                    "grant" if grant else "revoke",
                    user_id,
                    is_admin,
                )
                database.flush()
                return _record(database, row)
        except AuthorKnowledgeNotFoundError, AuthorKnowledgeConflictError:
            raise
        except IntegrityError:
            raise AuthorKnowledgeConflictError("Author grant state changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _mutable(
        database: Session,
        actor_id: UUID,
        author_id: UUID,
        if_match: str,
        is_admin: bool,
    ) -> AuthorKnowledgeRow:
        row = database.scalar(
            select(AuthorKnowledgeRow)
            .where(AuthorKnowledgeRow.id == str(author_id))
            .with_for_update()
        )
        if (
            row is None
            or row.owner_id != str(actor_id)
            or database.scalar(
                select(UserRow.id).where(
                    UserRow.id == str(actor_id), UserRow.active.is_(True)
                )
            )
            is None
        ):
            raise AuthorKnowledgeNotFoundError("Author entry does not exist")
        if if_match != f'"{row.version}"':
            raise AuthorKnowledgeConflictError("Author entry changed")
        return row

    def _audit(  # noqa: PLR0913, PLR0917 - content-free audit identity
        self,
        database: Session,
        row: AuthorKnowledgeRow,
        actor_id: UUID,
        operation: str,
        target_user_id: UUID | None = None,
        is_admin: bool = False,
    ) -> None:
        created_at = self._clock()
        database.add(
            AuthorKnowledgeAuditRow(
                id=str(self._new_id()),
                author_id=row.id,
                owner_id=row.owner_id,
                actor_id=str(actor_id),
                operation=operation,
                target_user_id=str(target_user_id) if target_user_id else None,
                version=row.version,
                administrator_intervention=False,
                created_at=created_at,
            )
        )
        record_content_audit(
            database,
            event_id=self._new_id(),
            owner_id=UUID(row.owner_id),
            actor_id=actor_id,
            operation=f"author_{operation}",
            target_kind="author_knowledge",
            target_id=UUID(row.id),
            draft_id=None,
            draft_version=row.version,
            created_at=created_at,
        )
