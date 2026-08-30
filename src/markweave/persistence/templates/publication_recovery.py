"""Atomic template version reservation and object publication persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.persistence.templates.common import (
    _SqlTemplateStore,
    _version,
)
from markweave.templates.models import (
    TemplateVersion,
)

SYSTEM_TEMPLATE_SELECTION_ID = 1


class _TemplatePublicationRecoveryRepository(_SqlTemplateStore):
    """Retry-visible pending publication leases and deletion discovery."""

    def abort_pending(
        self, template_id: UUID, version_id: UUID, publication_token: UUID
    ) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                result = database.execute(
                    delete(TemplateVersionRow).where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                        TemplateVersionRow.publication_state == "pending",
                        TemplateVersionRow.publication_token == str(publication_token),
                    )
                )
                removed = getattr(result, "rowcount", 0) == 1
                template = database.get(TemplateRow, str(template_id))
                if (
                    removed
                    and template is not None
                    and template.publication_state == "pending"
                ):
                    database.delete(template)
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None

    def claim_stale_pending(
        self,
        *,
        stale_before: datetime,
        lease_expires_at: datetime,
        publication_token: UUID,
    ) -> tuple[TemplateVersion, ...]:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                candidates = (
                    select(TemplateVersionRow.id)
                    .where(
                        TemplateVersionRow.publication_state == "pending",
                        TemplateVersionRow.publication_lease_expires_at <= stale_before,
                    )
                    .with_for_update(
                        skip_locked=self._engine.dialect.name == "postgresql"
                    )
                )
                mutation = (
                    update(TemplateVersionRow)
                    .where(TemplateVersionRow.id.in_(candidates))
                    .values(
                        publication_token=str(publication_token),
                        publication_lease_expires_at=lease_expires_at,
                    )
                )
                if self._engine.dialect.name == "sqlite":
                    candidate_ids = tuple(database.scalars(candidates))
                    if not candidate_ids:
                        return ()
                    result = database.execute(
                        update(TemplateVersionRow)
                        .where(
                            TemplateVersionRow.id.in_(candidate_ids),
                            TemplateVersionRow.publication_state == "pending",
                            TemplateVersionRow.publication_lease_expires_at
                            <= stale_before,
                        )
                        .values(
                            publication_token=str(publication_token),
                            publication_lease_expires_at=lease_expires_at,
                        )
                    )
                    if getattr(result, "rowcount", 0) != len(candidate_ids):
                        raise PersistenceError
                    return tuple(
                        _version(row)
                        for row in database.scalars(
                            select(TemplateVersionRow)
                            .where(TemplateVersionRow.id.in_(candidate_ids))
                            .order_by(TemplateVersionRow.id)
                        )
                    )
                return tuple(
                    _version(row)
                    for row in database.scalars(mutation.returning(TemplateVersionRow))
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def release_pending_claim(
        self,
        template_id: UUID,
        version_id: UUID,
        publication_token: UUID,
        *,
        retry_at: datetime,
    ) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                result = database.execute(
                    update(TemplateVersionRow)
                    .where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                        TemplateVersionRow.publication_state == "pending",
                        TemplateVersionRow.publication_token == str(publication_token),
                    )
                    .values(publication_lease_expires_at=retry_at)
                )
                return getattr(result, "rowcount", 0) == 1
        except SQLAlchemyError:
            raise PersistenceError from None

    def pending_deletions(
        self,
    ) -> tuple[tuple[UUID, tuple[TemplateVersion, ...]], ...]:
        try:
            with DatabaseSession(self._engine) as database:
                template_ids = tuple(
                    database.scalars(
                        select(TemplateRow.id).where(
                            TemplateRow.publication_state == "deleting"
                        )
                    )
                )
                return tuple(
                    (
                        UUID(template_id),
                        tuple(
                            _version(row)
                            for row in database.scalars(
                                select(TemplateVersionRow).where(
                                    TemplateVersionRow.template_id == template_id
                                )
                            )
                        ),
                    )
                    for template_id in template_ids
                )
        except SQLAlchemyError:
            raise PersistenceError from None
