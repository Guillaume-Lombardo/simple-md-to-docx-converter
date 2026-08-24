"""Transactional SQL template repositories shared by both storage profiles."""

from __future__ import annotations

from datetime import UTC
from uuid import UUID

from sqlalchemy import Engine, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.base import Executable

from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.schema import (
    ConversionJobRow,
    SystemTemplateSelectionRow,
    TemplateAuditRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
)
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateUnavailableError,
)
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
    TemplateVersion,
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
        revision=row.revision,
        current_version_id=(
            UUID(row.current_version_id) if row.current_version_id else None
        ),
    )


def _version(row: TemplateVersionRow) -> TemplateVersion:
    created_at = (
        row.created_at.replace(tzinfo=UTC)
        if row.created_at.tzinfo is None
        else row.created_at.astimezone(UTC)
    )
    return TemplateVersion(
        id=UUID(row.id),
        template_id=UUID(row.template_id),
        number=row.version_number,
        object_owner_id=UUID(row.object_owner_id),
        sha256=row.sha256,
        size=row.size,
        created_at=created_at,
        created_by=UUID(row.created_by),
        restored_from_version_id=(
            UUID(row.restored_from_version_id) if row.restored_from_version_id else None
        ),
    )


def _version_row(version: TemplateVersion) -> TemplateVersionRow:
    return TemplateVersionRow(
        id=str(version.id),
        template_id=str(version.template_id),
        version_number=version.number,
        object_owner_id=str(version.object_owner_id),
        sha256=version.sha256,
        size=version.size,
        created_at=version.created_at,
        created_by=str(version.created_by),
        restored_from_version_id=(
            str(version.restored_from_version_id)
            if version.restored_from_version_id
            else None
        ),
    )


def _audit_row(audit: TemplateAuditRecord) -> TemplateAuditRow:
    return TemplateAuditRow(
        id=str(audit.id),
        actor_id=str(audit.actor_id),
        owner_id=str(audit.owner_id),
        template_id=str(audit.template_id),
        operation=audit.operation,
        version_id=str(audit.version_id) if audit.version_id else None,
        administrator_intervention=audit.administrator_intervention,
        created_at=audit.created_at,
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
                        revision=template.revision,
                        current_version_id=(
                            str(template.current_version_id)
                            if template.current_version_id
                            else None
                        ),
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def create_versioned(
        self,
        template: TemplateIdentity,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
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
                        revision=template.revision,
                        current_version_id=str(version.id),
                    )
                )
                database.add(_version_row(version))
                database.add(_audit_row(audit))
            return template
        except SQLAlchemyError:
            raise PersistenceError from None

    def update_metadata(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        name: str,
        description: str,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        return self._cas_update(
            template_id,
            expected_revision,
            {
                "name": name,
                "normalized_name": normalize_template_text(name),
                "description": description,
                "normalized_description": normalize_template_text(description),
            },
            audit,
        )

    def publish_version(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                changed = database.execute(
                    update(TemplateRow)
                    .where(
                        TemplateRow.id == str(template_id),
                        TemplateRow.revision == expected_revision,
                        TemplateRow.status == TemplateStatus.ACTIVE.value,
                    )
                    .values(
                        current_version_id=str(version.id),
                        revision=TemplateRow.revision + 1,
                    )
                    .returning(TemplateRow)
                ).scalar_one_or_none()
                if changed is None:
                    raise TemplateConflictError
                database.add(_version_row(version))
                database.add(_audit_row(audit))
                database.flush()
                return _template(changed)
        except TemplateConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_status(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        status: str,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        return self._cas_update(
            template_id, expected_revision, {"status": status}, audit
        )

    def delete_guarded(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateVersion, ...]:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                row = database.get(TemplateRow, str(template_id))
                if (
                    row is None
                    or row.revision != expected_revision
                    or row.status != TemplateStatus.ARCHIVED.value
                ):
                    raise TemplateConflictError
                referenced = any(
                    (
                        database.scalar(
                            select(TemplatePreferenceRow.user_id)
                            .where(
                                TemplatePreferenceRow.template_id == str(template_id)
                            )
                            .limit(1)
                        ),
                        database.scalar(
                            select(SystemTemplateSelectionRow.id)
                            .where(
                                SystemTemplateSelectionRow.fallback_template_id
                                == str(template_id)
                            )
                            .limit(1)
                        ),
                        database.scalar(
                            select(ConversionJobRow.id)
                            .where(ConversionJobRow.template_id == str(template_id))
                            .limit(1)
                        ),
                    )
                )
                if referenced:
                    raise TemplateConflictError
                versions = tuple(
                    _version(item)
                    for item in database.scalars(
                        select(TemplateVersionRow)
                        .where(TemplateVersionRow.template_id == str(template_id))
                        .order_by(TemplateVersionRow.version_number)
                    )
                )
                database.delete(row)
                database.add(_audit_row(audit))
            return versions
        except TemplateConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_version(
        self, template_id: UUID, version_id: UUID
    ) -> TemplateVersion | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(TemplateVersionRow).where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                    )
                )
                return _version(row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_versions(self, template_id: UUID) -> tuple[TemplateVersion, ...]:
        try:
            with DatabaseSession(self._engine) as database:
                return tuple(
                    _version(row)
                    for row in database.scalars(
                        select(TemplateVersionRow)
                        .where(TemplateVersionRow.template_id == str(template_id))
                        .order_by(TemplateVersionRow.version_number.desc())
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def _cas_update(
        self,
        template_id: UUID,
        expected_revision: int,
        values: dict[str, object],
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                changed = database.execute(
                    update(TemplateRow)
                    .where(
                        TemplateRow.id == str(template_id),
                        TemplateRow.revision == expected_revision,
                    )
                    .values(**values, revision=TemplateRow.revision + 1)
                    .returning(TemplateRow)
                ).scalar_one_or_none()
                if changed is None:
                    raise TemplateConflictError
                database.add(_audit_row(audit))
                database.flush()
                return _template(changed)
        except TemplateConflictError:
            raise
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
