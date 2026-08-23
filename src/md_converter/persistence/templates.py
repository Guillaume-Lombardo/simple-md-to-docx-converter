"""Transactional SQL template repositories shared by both storage profiles."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Engine, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.base import Executable

from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import (
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
)
from md_converter.templates.errors import TemplateUnavailableError
from md_converter.templates.models import (
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
    normalize_template_text,
)

SYSTEM_TEMPLATE_SELECTION_ID = 1


def _template(row: TemplateRow) -> TemplateIdentity:
    return TemplateIdentity(
        id=UUID(row.id),
        owner_id=UUID(row.owner_id),
        name=row.name,
        description=row.description,
        status=TemplateStatus(row.status),
    )


class SqlTemplateCatalogRepository:
    """Template identity persistence and visibility-aware search."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, template: TemplateIdentity) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                database.add(
                    TemplateRow(
                        id=str(template.id),
                        owner_id=str(template.owner_id),
                        name=template.name,
                        normalized_name=template.normalized_name,
                        description=template.description,
                        normalized_description=template.normalized_description,
                        status=template.status.value,
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, template_id: UUID) -> TemplateIdentity | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(TemplateRow, str(template_id))
                return _template(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def search(
        self,
        query: TemplateSearch,
        *,
        viewer_id: UUID,
        viewer_is_admin: bool,
    ) -> TemplatePage:
        conditions = []
        if not viewer_is_admin:
            conditions.append(
                or_(
                    TemplateRow.status == TemplateStatus.ACTIVE.value,
                    TemplateRow.owner_id == str(viewer_id),
                )
            )
        if query.name is not None and (name := normalize_template_text(query.name)):
            conditions.append(
                TemplateRow.normalized_name.contains(name, autoescape=True)
            )
        if query.description is not None and (
            description := normalize_template_text(query.description)
        ):
            conditions.append(
                TemplateRow.normalized_description.contains(
                    description, autoescape=True
                )
            )
        if query.owner_id is not None:
            conditions.append(TemplateRow.owner_id == str(query.owner_id))
        if query.status is not None:
            conditions.append(TemplateRow.status == query.status.value)
        try:
            with DatabaseSession(self._engine) as database:
                total = database.scalar(
                    select(func.count()).select_from(TemplateRow).where(*conditions)
                )
                rows = database.scalars(
                    select(TemplateRow)
                    .where(*conditions)
                    .order_by(TemplateRow.normalized_name, TemplateRow.id)
                    .offset(query.offset)
                    .limit(query.limit)
                )
                return TemplatePage(
                    items=tuple(_template(row) for row in rows),
                    total=int(total or 0),
                    offset=query.offset,
                    limit=query.limit,
                )
        except SQLAlchemyError:
            raise PersistenceError from None


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
            )
        )
        if active is None:
            raise TemplateUnavailableError
