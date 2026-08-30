"""Atomic template version reservation and object publication persistence."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    TemplateVersionRow,
)
from markweave.persistence.templates.common import (
    _SqlTemplateStore,
    _version,
)
from markweave.templates.models import (
    TemplateVersion,
)

SYSTEM_TEMPLATE_SELECTION_ID = 1


class _TemplateVersionQueryRepository(_SqlTemplateStore):
    """Immutable published template-version lookup and history."""

    def get_version(
        self, template_id: UUID, version_id: UUID
    ) -> TemplateVersion | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(TemplateVersionRow).where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                        TemplateVersionRow.publication_state == "published",
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
                        .where(
                            TemplateVersionRow.template_id == str(template_id),
                            TemplateVersionRow.publication_state == "published",
                        )
                        .order_by(TemplateVersionRow.version_number.desc())
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None
