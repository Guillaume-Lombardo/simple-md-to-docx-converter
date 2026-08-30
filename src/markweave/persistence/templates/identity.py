"""Template identity metadata and guarded deletion persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ConversionJobRow,
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.persistence.templates.audit import _audit_row
from markweave.persistence.templates.common import (
    _SqlTemplateStore,
    _template,
    _version,
)
from markweave.templates.errors import (
    TemplateConflictError,
)
from markweave.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplateStatus,
    TemplateVersion,
    normalize_template_text,
)


class _TemplateIdentityRepository(_SqlTemplateStore):
    """Identity creation, metadata CAS, status, and guarded deletion."""

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
                        publication_state="published",
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, template_id: UUID) -> TemplateIdentity | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(TemplateRow, str(template_id))
                return (
                    _template(row)
                    if row is not None and row.publication_state == "published"
                    else None
                )
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

    def begin_delete(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        audit: TemplateAuditRecord,
    ) -> tuple[TemplateVersion, ...]:
        """Commit a durable tombstone before any object is removed."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(TemplateRow)
                    .where(TemplateRow.id == str(template_id))
                    .with_for_update()
                )
                if row is None:
                    raise TemplateConflictError
                retry = (
                    row.publication_state == "deleting"
                    and row.revision == expected_revision + 1
                )
                if not retry:
                    if (
                        row.revision != expected_revision
                        or row.status != TemplateStatus.ARCHIVED.value
                        or row.publication_state != "published"
                    ):
                        raise TemplateConflictError
                    referenced = any(
                        (
                            database.scalar(
                                select(TemplatePreferenceRow.user_id)
                                .where(
                                    TemplatePreferenceRow.template_id
                                    == str(template_id)
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
                    row.publication_state = "deleting"
                    row.revision += 1
                    database.add(_audit_row(audit))
                return tuple(
                    _version(item)
                    for item in database.scalars(
                        select(TemplateVersionRow)
                        .where(TemplateVersionRow.template_id == str(template_id))
                        .order_by(TemplateVersionRow.version_number)
                    )
                )
        except TemplateConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def finalize_delete(self, template_id: UUID) -> None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.get(TemplateRow, str(template_id))
                if row is None:
                    return
                if row.publication_state != "deleting":
                    raise TemplateConflictError
                database.delete(row)
        except TemplateConflictError:
            raise
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
                serialize_sqlite_write(database, self._engine)
                statement = (
                    update(TemplateRow)
                    .where(
                        TemplateRow.id == str(template_id),
                        TemplateRow.revision == expected_revision,
                    )
                    .values(**values, revision=TemplateRow.revision + 1)
                )
                if self._engine.dialect.name == "sqlite":
                    result = database.execute(statement)
                    changed = (
                        database.get(TemplateRow, str(template_id))
                        if getattr(result, "rowcount", 0) == 1
                        else None
                    )
                else:
                    changed = database.execute(
                        statement.returning(TemplateRow)
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
