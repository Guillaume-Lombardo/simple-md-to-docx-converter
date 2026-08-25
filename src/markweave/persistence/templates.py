"""Transactional SQL template repositories shared by both storage profiles."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession
from sqlalchemy.sql.base import Executable

from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ConversionJobRow,
    SystemTemplateSelectionRow,
    TemplateAuditRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.templates.errors import (
    TemplateConflictError,
    TemplateUnavailableError,
)
from markweave.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePage,
    TemplatePublicationState,
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
        declared_fonts=tuple(json.loads(row.declared_fonts)),
        resolved_fonts=tuple(tuple(item) for item in json.loads(row.resolved_fonts)),
        validation_trace=tuple(json.loads(row.validation_trace)),
        publication_state=TemplatePublicationState(row.publication_state),
        publication_token=(
            UUID(row.publication_token) if row.publication_token else None
        ),
        publication_lease_expires_at=(
            row.publication_lease_expires_at.replace(tzinfo=UTC)
            if row.publication_lease_expires_at is not None
            and row.publication_lease_expires_at.tzinfo is None
            else row.publication_lease_expires_at
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
        declared_fonts=json.dumps(version.declared_fonts, separators=(",", ":")),
        resolved_fonts=json.dumps(version.resolved_fonts, separators=(",", ":")),
        validation_trace=json.dumps(version.validation_trace, separators=(",", ":")),
        publication_state=version.publication_state.value,
        publication_token=(
            str(version.publication_token) if version.publication_token else None
        ),
        publication_lease_expires_at=version.publication_lease_expires_at,
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
                        publication_state="published",
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
            expected_revision=1,
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
                version.publication_state = "published"
                version.publication_token = None
                version.publication_lease_expires_at = None
                database.flush()
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
                        + (
                            0
                            if expected_revision == 1 and version.version_number == 1
                            else 1
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
