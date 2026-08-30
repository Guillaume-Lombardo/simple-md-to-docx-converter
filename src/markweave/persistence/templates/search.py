"""Visibility-aware template identity search queries."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    TemplateRow,
)
from markweave.persistence.templates.common import _SqlTemplateStore, _template
from markweave.templates.models import (
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
    normalize_template_text,
)


class _TemplateSearchRepository(_SqlTemplateStore):
    """Bounded provider-neutral filtered template pagination."""

    def search(
        self,
        query: TemplateSearch,
        *,
        viewer_id: UUID,
        viewer_is_admin: bool,
    ) -> TemplatePage:
        conditions = []
        conditions.append(TemplateRow.publication_state == "published")
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
