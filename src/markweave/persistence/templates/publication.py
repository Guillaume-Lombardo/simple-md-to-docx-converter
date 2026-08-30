"""Atomic template version reservation and object publication persistence."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.persistence.templates.audit import _audit_row
from markweave.persistence.templates.common import (
    _SqlTemplateStore,
    _template,
    _version_row,
)
from markweave.templates.errors import (
    TemplateConflictError,
)
from markweave.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)


class _TemplatePublicationRepository(_SqlTemplateStore):
    """Version numbering, byte publication finalization, and audit coupling."""

    def create_versioned(
        self,
        template: TemplateIdentity,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        pending = replace(
            version,
            publication_state=TemplatePublicationState.PENDING,
            publication_token=version.publication_token or uuid4(),
            publication_lease_expires_at=(
                version.publication_lease_expires_at or version.created_at
            ),
        )
        self.reserve_create(template, pending)
        return self.finalize_version(
            template.id,
            expected_revision=template.revision,
            version_id=version.id,
            publication_token=self._publication_token(pending),
            audit=audit,
        )

    def reserve_create(
        self, template: TemplateIdentity, version: TemplateVersion
    ) -> None:
        """Persist a hidden identity and pending object row before writing bytes."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
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
                        current_version_id=None,
                        publication_state="pending",
                    )
                )
                database.add(_version_row(version))
        except SQLAlchemyError:
            raise PersistenceError from None

    def reserve_version(
        self, template_id: UUID, *, expected_revision: int, version: TemplateVersion
    ) -> TemplateVersion:
        """Serialize version numbering and persist a retry-visible pending object."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                template = database.scalar(
                    select(TemplateRow)
                    .where(
                        TemplateRow.id == str(template_id),
                        TemplateRow.revision == expected_revision,
                        TemplateRow.status == TemplateStatus.ACTIVE.value,
                        TemplateRow.publication_state == "published",
                    )
                    .with_for_update()
                )
                if template is None:
                    raise TemplateConflictError
                next_number = (
                    int(
                        database.scalar(
                            select(
                                func.coalesce(
                                    func.max(TemplateVersionRow.version_number), 0
                                )
                            ).where(TemplateVersionRow.template_id == str(template_id))
                        )
                        or 0
                    )
                    + 1
                )
                reserved = TemplateVersion(
                    id=version.id,
                    template_id=version.template_id,
                    number=next_number,
                    object_owner_id=version.object_owner_id,
                    sha256=version.sha256,
                    size=version.size,
                    created_at=version.created_at,
                    created_by=version.created_by,
                    restored_from_version_id=version.restored_from_version_id,
                    declared_fonts=version.declared_fonts,
                    resolved_fonts=version.resolved_fonts,
                    validation_trace=version.validation_trace,
                    publication_state=TemplatePublicationState.PENDING,
                    publication_token=version.publication_token,
                    publication_lease_expires_at=version.publication_lease_expires_at,
                )
                database.add(_version_row(reserved))
                database.flush()
                return reserved
        except TemplateConflictError:
            raise
        except IntegrityError:
            raise TemplateConflictError from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def finalize_version(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        version_id: UUID,
        publication_token: UUID,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        """Publish one reserved object and CAS the current version atomically."""
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                version = database.scalar(
                    select(TemplateVersionRow).where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                        TemplateVersionRow.publication_state == "pending",
                        TemplateVersionRow.publication_token == str(publication_token),
                    )
                )
                if version is None:
                    raise TemplateConflictError
                claimed = database.execute(
                    update(TemplateVersionRow)
                    .where(
                        TemplateVersionRow.id == str(version_id),
                        TemplateVersionRow.template_id == str(template_id),
                        TemplateVersionRow.publication_state == "pending",
                        TemplateVersionRow.publication_token == str(publication_token),
                    )
                    .values(
                        publication_state="published",
                        publication_token=None,
                        publication_lease_expires_at=None,
                    )
                )
                if getattr(claimed, "rowcount", 0) != 1:
                    raise TemplateConflictError
                result = database.execute(
                    update(TemplateRow)
                    .where(
                        TemplateRow.id == str(template_id),
                        TemplateRow.revision == expected_revision,
                        TemplateRow.status == TemplateStatus.ACTIVE.value,
                        TemplateRow.publication_state.in_(("pending", "published")),
                    )
                    .values(
                        current_version_id=str(version_id),
                        publication_state="published",
                        revision=TemplateRow.revision
                        + case(
                            (TemplateRow.publication_state == "pending", 0),
                            else_=1,
                        ),
                    )
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise TemplateConflictError
                changed = database.get(TemplateRow, str(template_id))
                if changed is None:  # pragma: no cover - guarded by rowcount
                    raise PersistenceError
                database.add(_audit_row(audit))
                database.flush()
                return _template(changed)
        except TemplateConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def publish_version(
        self,
        template_id: UUID,
        *,
        expected_revision: int,
        version: TemplateVersion,
        audit: TemplateAuditRecord,
    ) -> TemplateIdentity:
        reserved = self.reserve_version(
            template_id,
            expected_revision=expected_revision,
            version=replace(
                version,
                publication_state=TemplatePublicationState.PENDING,
                publication_token=version.publication_token or uuid4(),
                publication_lease_expires_at=(
                    version.publication_lease_expires_at or version.created_at
                ),
            ),
        )
        return self.finalize_version(
            template_id,
            expected_revision=expected_revision,
            version_id=reserved.id,
            publication_token=self._publication_token(reserved),
            audit=audit,
        )

    @staticmethod
    def _publication_token(version: TemplateVersion) -> UUID:
        if version.publication_token is None:  # guarded by the domain model
            raise TemplateConflictError
        return version.publication_token
