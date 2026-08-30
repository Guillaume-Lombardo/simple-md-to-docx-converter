"""Transactional preferred-template and system-fallback persistence."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.base import Executable

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
)
from markweave.persistence.templates.audit import _audit_row
from markweave.persistence.templates.common import _template
from markweave.templates.errors import (
    TemplateUnavailableError,
)
from markweave.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplateStatus,
)

SYSTEM_TEMPLATE_SELECTION_ID = 1


class SqlTemplateSelectionRepository:
    """Preference and singleton fallback operations with active checks."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def set_preferred(self, user_id: UUID, template_id: UUID) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                self._require_active(database, template_id)
                statement = self._preference_upsert(user_id, template_id)
                database.execute(statement)
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_preferred_audited(
        self, user_id: UUID, template_id: UUID, audit: TemplateAuditRecord
    ) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                self._require_active(database, template_id)
                database.execute(self._preference_upsert(user_id, template_id))
                database.add(_audit_row(audit))
        except SQLAlchemyError:
            raise PersistenceError from None

    def clear_preferred(self, user_id: UUID) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.execute(
                    delete(TemplatePreferenceRow).where(
                        TemplatePreferenceRow.user_id == str(user_id)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def clear_preferred_audited(
        self, user_id: UUID, audit: TemplateAuditRecord
    ) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                selected = database.scalar(
                    select(TemplateRow)
                    .join(
                        TemplatePreferenceRow,
                        TemplatePreferenceRow.template_id == TemplateRow.id,
                    )
                    .where(TemplatePreferenceRow.user_id == str(user_id))
                    .with_for_update()
                )
                if selected is None:
                    return
                database.execute(
                    delete(TemplatePreferenceRow).where(
                        TemplatePreferenceRow.user_id == str(user_id)
                    )
                )
                database.add(
                    _audit_row(
                        replace(
                            audit,
                            owner_id=UUID(selected.owner_id),
                            template_id=UUID(selected.id),
                        )
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def preferred_id(self, user_id: UUID) -> UUID | None:
        try:
            with DatabaseSession(self._engine) as database:
                value = database.scalar(
                    select(TemplatePreferenceRow.template_id).where(
                        TemplatePreferenceRow.user_id == str(user_id)
                    )
                )
                return UUID(value) if value is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_system_fallback(self, template_id: UUID) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                self._require_active(database, template_id)
                database.execute(self._fallback_upsert(template_id))
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_system_fallback_audited(
        self, template_id: UUID, audit: TemplateAuditRecord
    ) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                self._require_active(database, template_id)
                database.execute(self._fallback_upsert(template_id))
                database.add(_audit_row(audit))
        except SQLAlchemyError:
            raise PersistenceError from None

    def system_fallback_id(self) -> UUID | None:
        try:
            with DatabaseSession(self._engine) as database:
                value = database.scalar(
                    select(SystemTemplateSelectionRow.fallback_template_id).where(
                        SystemTemplateSelectionRow.id == SYSTEM_TEMPLATE_SELECTION_ID
                    )
                )
                return UUID(value) if value is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def resolve(self, user_id: UUID) -> TemplateIdentity | None:
        try:
            with DatabaseSession(self._engine) as database:
                preferred = database.scalar(
                    select(TemplateRow)
                    .join(
                        TemplatePreferenceRow,
                        TemplatePreferenceRow.template_id == TemplateRow.id,
                    )
                    .where(
                        TemplatePreferenceRow.user_id == str(user_id),
                        TemplateRow.status == TemplateStatus.ACTIVE.value,
                        TemplateRow.publication_state == "published",
                    )
                )
                if preferred is not None:
                    return _template(preferred)
                fallback = database.scalar(
                    select(TemplateRow)
                    .join(
                        SystemTemplateSelectionRow,
                        SystemTemplateSelectionRow.fallback_template_id
                        == TemplateRow.id,
                    )
                    .where(
                        SystemTemplateSelectionRow.id == SYSTEM_TEMPLATE_SELECTION_ID,
                        TemplateRow.status == TemplateStatus.ACTIVE.value,
                        TemplateRow.publication_state == "published",
                    )
                )
                return _template(fallback) if fallback is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def _preference_upsert(self, user_id: UUID, template_id: UUID) -> Executable:
        values = {"user_id": str(user_id), "template_id": str(template_id)}
        if self._engine.dialect.name == "postgresql":
            statement = postgresql_insert(TemplatePreferenceRow).values(**values)
        else:
            statement = sqlite_insert(TemplatePreferenceRow).values(**values)
        return statement.on_conflict_do_update(
            index_elements=[TemplatePreferenceRow.user_id],
            set_={"template_id": str(template_id)},
        )

    def _fallback_upsert(self, template_id: UUID) -> Executable:
        values = {
            "id": SYSTEM_TEMPLATE_SELECTION_ID,
            "fallback_template_id": str(template_id),
        }
        if self._engine.dialect.name == "postgresql":
            statement = postgresql_insert(SystemTemplateSelectionRow).values(**values)
        else:
            statement = sqlite_insert(SystemTemplateSelectionRow).values(**values)
        return statement.on_conflict_do_update(
            index_elements=[SystemTemplateSelectionRow.id],
            set_={"fallback_template_id": str(template_id)},
        )

    @staticmethod
    def _require_active(database: DatabaseSession, template_id: UUID) -> None:
        active = database.scalar(
            select(TemplateRow.id).where(
                TemplateRow.id == str(template_id),
                TemplateRow.status == TemplateStatus.ACTIVE.value,
                TemplateRow.publication_state == "published",
            )
        )
        if active is None:
            raise TemplateUnavailableError
