"""Private typed DOCX template identities and two-phase version publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, exists, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.persistence.composer.audit import record_content_audit
from markweave.persistence.composer.common import validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    TypedTemplateAuditRow,
    TypedTemplateGrantRow,
    TypedTemplateRow,
    TypedTemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)


class TypedTemplateNotFoundError(LookupError):
    """A typed template or exact version is absent or unauthorized."""


class TypedTemplateConflictError(RuntimeError):
    """The template revision or pending publication changed."""


class TypedTemplateArtifactError(RuntimeError):
    """The exact DOCX object is absent, corrupt, or unavailable."""


@dataclass(frozen=True, slots=True)
class TypedTemplate:
    id: UUID
    owner_id: UUID
    name: str
    version: int
    active_version_id: UUID
    shared_with: tuple[UUID, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


@dataclass(frozen=True, slots=True)
class TypedTemplateVersion:
    id: UUID
    template_id: UUID
    number: int
    schema_version: int
    schema_json: str
    schema_sha256: str
    docx_sha256: str
    size: int
    created_at: datetime


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _version(row: TypedTemplateVersionRow) -> TypedTemplateVersion:
    return TypedTemplateVersion(
        UUID(row.id),
        UUID(row.template_id),
        row.number,
        row.schema_version,
        row.schema_json,
        row.schema_sha256,
        row.sha256,
        row.size,
        _utc(row.created_at),
    )


def _identity(database: Session, row: TypedTemplateRow) -> TypedTemplate:
    if row.current_version_id is None:
        raise TypedTemplateNotFoundError("Typed template does not exist")
    grants = tuple(
        UUID(user_id)
        for user_id in database.scalars(
            select(TypedTemplateGrantRow.user_id)
            .where(TypedTemplateGrantRow.template_id == row.id)
            .order_by(TypedTemplateGrantRow.user_id)
        )
    )
    return TypedTemplate(
        UUID(row.id),
        UUID(row.owner_id),
        row.name,
        row.revision,
        UUID(row.current_version_id),
        grants,
        _utc(row.created_at),
        _utc(row.updated_at),
    )


class SqlTypedTemplateRepository:
    """Equivalent SQLite/filesystem and PostgreSQL/S3 version publication."""

    def __init__(
        self,
        engine: Engine,
        objects: ObjectStore,
        *,
        publication_lease: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        if publication_lease <= timedelta(0):
            raise ValueError("Publication lease must be positive")
        self._engine, self._objects = engine, objects
        self._clock, self._new_id = clock, new_id
        self._publication_lease = publication_lease

    @staticmethod
    def require_version_access(  # noqa: PLR0913 - exact authorization fence
        database: Session,
        actor_id: UUID,
        version_id: UUID,
        *,
        expected_docx_sha256: str | None = None,
        expected_schema_sha256: str | None = None,
        for_update: bool = False,
    ) -> TypedTemplateVersionRow:
        """Recheck the named user grant and exact digests in a caller transaction."""

        visible = or_(
            TypedTemplateRow.owner_id == str(actor_id),
            exists(
                select(TypedTemplateGrantRow.template_id).where(
                    TypedTemplateGrantRow.template_id == TypedTemplateRow.id,
                    TypedTemplateGrantRow.user_id == str(actor_id),
                )
            ),
        )
        statement = (
            select(TypedTemplateVersionRow)
            .join(
                TypedTemplateRow,
                TypedTemplateVersionRow.template_id == TypedTemplateRow.id,
            )
            .where(
                TypedTemplateVersionRow.id == str(version_id),
                TypedTemplateVersionRow.publication_state == "published",
                TypedTemplateRow.publication_state == "published",
                visible,
                exists(
                    select(UserRow.id).where(
                        UserRow.id == str(actor_id), UserRow.active.is_(True)
                    )
                ),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = database.scalar(statement)
        if row is None:
            raise TypedTemplateNotFoundError("Typed template version does not exist")
        if expected_docx_sha256 is not None and row.sha256 != expected_docx_sha256:
            raise TypedTemplateConflictError("Typed template content changed")
        if (
            expected_schema_sha256 is not None
            and row.schema_sha256 != expected_schema_sha256
        ):
            raise TypedTemplateConflictError("Typed template schema changed")
        return row

    @staticmethod
    def _visible(
        database: Session, actor_id: UUID, template_id: UUID
    ) -> TypedTemplateRow:
        visible = or_(
            TypedTemplateRow.owner_id == str(actor_id),
            exists(
                select(TypedTemplateGrantRow.template_id).where(
                    TypedTemplateGrantRow.template_id == TypedTemplateRow.id,
                    TypedTemplateGrantRow.user_id == str(actor_id),
                )
            ),
        )
        row = database.scalar(
            select(TypedTemplateRow).where(
                TypedTemplateRow.id == str(template_id),
                TypedTemplateRow.publication_state == "published",
                visible,
                exists(
                    select(UserRow.id).where(
                        UserRow.id == str(actor_id), UserRow.active.is_(True)
                    )
                ),
            )
        )
        if row is None:
            raise TypedTemplateNotFoundError("Typed template does not exist")
        return row

    def create(
        self,
        owner_id: UUID,
        name: str,
        schema_json: str,
        docx_bytes: bytes,
        *,
        schema_version: int,
    ) -> TypedTemplate:
        """Publish a caller-validated and scanned DOCX with immutable schema."""

        self._validate_inputs(name, schema_json, docx_bytes, schema_version)
        template_id, version_id, token = self._new_id(), self._new_id(), self._new_id()
        now = self._clock()
        key = ObjectKey(ObjectScope.TYPED_TEMPLATE, owner_id, version_id)
        version = self._version_row(
            template_id,
            version_id,
            owner_id,
            owner_id,
            schema_json,
            schema_version,
            docx_bytes,
            1,
            1,
            token,
            now,
        )
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
                    raise TypedTemplateNotFoundError("Template owner does not exist")
                database.add(
                    TypedTemplateRow(
                        id=str(template_id),
                        owner_id=str(owner_id),
                        name=name.strip(),
                        revision=1,
                        current_version_id=None,
                        publication_state="pending",
                        created_at=now,
                        updated_at=now,
                    )
                )
                database.flush()
                database.add(version)
            self._write_and_verify(
                key, docx_bytes, hashlib.sha256(docx_bytes).hexdigest()
            )
            return self._finalize(template_id, version_id, token)
        except (
            TypedTemplateNotFoundError,
            TypedTemplateConflictError,
            TypedTemplateArtifactError,
        ):
            raise
        except IntegrityError:
            raise TypedTemplateConflictError("Typed template already exists") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def replace(  # noqa: PLR0913 - explicit publication preconditions
        self,
        actor_id: UUID,
        template_id: UUID,
        *,
        if_match: str,
        schema_json: str,
        docx_bytes: bytes,
        schema_version: int,
        is_admin: bool = False,
    ) -> TypedTemplate:
        """Reserve a hidden next version; publish only after verified object write."""

        self._validate_inputs("replacement", schema_json, docx_bytes, schema_version)
        version_id, token, now = self._new_id(), self._new_id(), self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, template_id, if_match, is_admin)
                owner_id = UUID(row.owner_id)
                number = (
                    int(
                        database.scalar(
                            select(
                                func.coalesce(
                                    func.max(TypedTemplateVersionRow.number), 0
                                )
                            ).where(
                                TypedTemplateVersionRow.template_id == str(template_id)
                            )
                        )
                        or 0
                    )
                    + 1
                )
                version = self._version_row(
                    template_id,
                    version_id,
                    UUID(row.owner_id),
                    actor_id,
                    schema_json,
                    schema_version,
                    docx_bytes,
                    number,
                    row.revision,
                    token,
                    now,
                )
                database.add(version)
            key = ObjectKey(ObjectScope.TYPED_TEMPLATE, owner_id, version_id)
            self._write_and_verify(
                key, docx_bytes, hashlib.sha256(docx_bytes).hexdigest()
            )
            return self._finalize(template_id, version_id, token)
        except (
            TypedTemplateNotFoundError,
            TypedTemplateConflictError,
            TypedTemplateArtifactError,
        ):
            raise
        except IntegrityError:
            raise TypedTemplateConflictError("Typed template version changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, actor_id: UUID, template_id: UUID) -> TypedTemplate:
        try:
            with Session(self._engine) as database:
                return _identity(
                    database, self._visible(database, actor_id, template_id)
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
    ) -> tuple[TypedTemplate, ...]:
        validate_page(limit, offset)
        if query is not None and not isinstance(query, str):
            raise ValueError("Typed template search query is invalid")
        try:
            with Session(self._engine) as database:
                statement = select(TypedTemplateRow).where(
                    TypedTemplateRow.publication_state == "published",
                    exists(
                        select(UserRow.id).where(
                            UserRow.id == str(actor_id), UserRow.active.is_(True)
                        )
                    ),
                    or_(
                        TypedTemplateRow.owner_id == str(actor_id),
                        exists(
                            select(TypedTemplateGrantRow.template_id).where(
                                TypedTemplateGrantRow.template_id
                                == TypedTemplateRow.id,
                                TypedTemplateGrantRow.user_id == str(actor_id),
                            )
                        ),
                    ),
                )
                if query:
                    statement = statement.where(
                        TypedTemplateRow.name.icontains(query, autoescape=True)
                    )
                rows = database.scalars(
                    statement.order_by(TypedTemplateRow.name, TypedTemplateRow.id)
                    .limit(limit)
                    .offset(offset)
                ).all()
                return tuple(_identity(database, row) for row in rows)
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_version(
        self,
        actor_id: UUID,
        template_id: UUID,
        version_id: UUID,
    ) -> TypedTemplateVersion:
        try:
            with Session(self._engine) as database:
                row = self.require_version_access(database, actor_id, version_id)
                if row.template_id != str(template_id):
                    raise TypedTemplateNotFoundError(
                        "Typed template version does not exist"
                    )
                return _version(row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_versions(
        self, actor_id: UUID, template_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[TypedTemplateVersion, ...]:
        """List only published versions of an authorized typed template."""

        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                self._visible(database, actor_id, template_id)
                return tuple(
                    _version(row)
                    for row in database.scalars(
                        select(TypedTemplateVersionRow)
                        .where(
                            TypedTemplateVersionRow.template_id == str(template_id),
                            TypedTemplateVersionRow.publication_state == "published",
                        )
                        .order_by(
                            TypedTemplateVersionRow.number, TypedTemplateVersionRow.id
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def download(
        self,
        actor_id: UUID,
        template_id: UUID,
        version_id: UUID,
    ) -> bytes:
        """Read the exact published DOCX and verify its persisted digest and length."""

        try:
            with Session(self._engine) as database:
                row = self.require_version_access(database, actor_id, version_id)
                if row.template_id != str(template_id):
                    raise TypedTemplateNotFoundError(
                        "Typed template version does not exist"
                    )
                key = ObjectKey(
                    ObjectScope.TYPED_TEMPLATE, UUID(row.object_owner_id), version_id
                )
                size, digest = row.size, row.sha256
            bounded = getattr(self._objects, "get_bounded", None)
            content = (
                bounded(key, size) if callable(bounded) else self._objects.get(key)
            )
            if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
                raise TypedTemplateArtifactError(
                    "Typed template content integrity check failed"
                )
            return content
        except ObjectNotFoundError, ObjectStoreError:
            raise TypedTemplateArtifactError(
                "Typed template content is unavailable"
            ) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def grant(
        self,
        actor_id: UUID,
        template_id: UUID,
        user_id: UUID,
        *,
        if_match: str,
        is_admin: bool = False,
    ) -> TypedTemplate:
        return self._change_grant(
            actor_id, template_id, user_id, if_match, is_admin, grant=True
        )

    def revoke(
        self,
        actor_id: UUID,
        template_id: UUID,
        user_id: UUID,
        *,
        if_match: str,
        is_admin: bool = False,
    ) -> TypedTemplate:
        return self._change_grant(
            actor_id, template_id, user_id, if_match, is_admin, grant=False
        )

    def _change_grant(  # noqa: PLR0913 - explicit grant preconditions
        self,
        actor_id: UUID,
        template_id: UUID,
        user_id: UUID,
        if_match: str,
        is_admin: bool,
        *,
        grant: bool,
    ) -> TypedTemplate:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._mutable(database, actor_id, template_id, if_match, is_admin)
                if user_id == UUID(row.owner_id):
                    raise ValueError("Owner access cannot be changed")
                if (
                    database.scalar(
                        select(UserRow.id).where(
                            UserRow.id == str(user_id), UserRow.active.is_(True)
                        )
                    )
                    is None
                ):
                    raise TypedTemplateNotFoundError("Target user does not exist")
                existing = database.get(
                    TypedTemplateGrantRow, (str(template_id), str(user_id))
                )
                if (grant and existing is not None) or (not grant and existing is None):
                    raise TypedTemplateConflictError(
                        "Typed template grant state changed"
                    )
                if grant:
                    database.add(
                        TypedTemplateGrantRow(
                            template_id=str(template_id),
                            user_id=str(user_id),
                            created_at=self._clock(),
                        )
                    )
                else:
                    database.delete(existing)
                row.revision += 1
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
                return _identity(database, row)
        except TypedTemplateNotFoundError, TypedTemplateConflictError:
            raise
        except IntegrityError:
            raise TypedTemplateConflictError(
                "Typed template grant state changed"
            ) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def recover_pending(self, *, limit: int) -> int:
        """Finalize complete expired objects or remove abandoned hidden reservations."""

        if type(limit) is not int or limit <= 0:
            raise ValueError("Recovery limit must be positive")
        now = self._clock()
        with Session(self._engine) as database:
            pending = tuple(
                database.scalars(
                    select(TypedTemplateVersionRow)
                    .where(
                        TypedTemplateVersionRow.publication_state == "pending",
                        TypedTemplateVersionRow.lease_expires_at <= now,
                    )
                    .order_by(
                        TypedTemplateVersionRow.created_at, TypedTemplateVersionRow.id
                    )
                    .limit(limit)
                )
            )
            reservations = tuple(
                (
                    UUID(row.template_id),
                    UUID(row.id),
                    UUID(row.publication_token),
                    UUID(row.object_owner_id),
                    row.sha256,
                    row.size,
                )
                for row in pending
                if row.publication_token is not None
            )
        processed = 0
        for template_id, version_id, token, owner_id, digest, size in reservations:
            key = ObjectKey(ObjectScope.TYPED_TEMPLATE, owner_id, version_id)
            try:
                content = self._objects.get(key)
                valid = (
                    len(content) == size
                    and hashlib.sha256(content).hexdigest() == digest
                )
            except ObjectNotFoundError:
                valid = False
            except ObjectStoreError:
                continue
            try:
                if valid:
                    self._finalize(template_id, version_id, token)
                else:
                    self._discard(template_id, version_id, token)
            except TypedTemplateConflictError:
                self._discard(template_id, version_id, token)
            processed += 1
        return processed

    def _finalize(
        self, template_id: UUID, version_id: UUID, token: UUID
    ) -> TypedTemplate:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                version = database.scalar(
                    select(TypedTemplateVersionRow)
                    .where(
                        TypedTemplateVersionRow.id == str(version_id),
                        TypedTemplateVersionRow.template_id == str(template_id),
                        TypedTemplateVersionRow.publication_state == "pending",
                        TypedTemplateVersionRow.publication_token == str(token),
                    )
                    .with_for_update()
                )
                if version is None:
                    raise TypedTemplateConflictError(
                        "Typed template publication changed"
                    )
                row = database.scalar(
                    select(TypedTemplateRow)
                    .where(
                        TypedTemplateRow.id == str(template_id),
                        TypedTemplateRow.revision == version.expected_revision,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise TypedTemplateConflictError("Typed template changed")
                first = row.current_version_id is None
                if first != (version.number == 1):
                    raise TypedTemplateConflictError(
                        "Typed template publication changed"
                    )
                row.current_version_id = str(version_id)
                row.publication_state = "published"
                if not first:
                    row.revision += 1
                row.updated_at = self._clock()
                version.publication_state = "published"
                version.publication_token = None
                version.lease_expires_at = None
                self._audit(
                    database,
                    row,
                    UUID(version.created_by),
                    "create" if first else "replace",
                    version_id=version_id,
                    is_admin=version.created_by != row.owner_id,
                )
                database.flush()
                return _identity(database, row)
        except TypedTemplateConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def _discard(self, template_id: UUID, version_id: UUID, token: UUID) -> None:
        with Session(self._engine) as database, database.begin():
            serialize_sqlite_write(database, self._engine)
            version = database.scalar(
                select(TypedTemplateVersionRow)
                .where(
                    TypedTemplateVersionRow.id == str(version_id),
                    TypedTemplateVersionRow.publication_state == "pending",
                    TypedTemplateVersionRow.publication_token == str(token),
                )
                .with_for_update()
            )
            if version is None:
                return
            # Keep the reservation locked until object removal succeeds. A crash
            # leaves a hidden row for the next recovery sweep, never an orphan.
            self._objects.delete(
                ObjectKey(
                    ObjectScope.TYPED_TEMPLATE,
                    UUID(version.object_owner_id),
                    version_id,
                )
            )
            database.delete(version)
            row = database.get(TypedTemplateRow, str(template_id))
            if row is not None and row.current_version_id is None:
                database.delete(row)

    @staticmethod
    def _validate_inputs(
        name: str, schema_json: str, content: bytes, schema_version: int
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Typed template name is required")
        if not isinstance(content, bytes) or not content:
            raise ValueError("Validated DOCX content is required")
        if type(schema_version) is not int or schema_version <= 0:
            raise ValueError("Typed template schema version is invalid")
        try:
            schema = json.loads(schema_json)
        except ValueError, TypeError:
            raise ValueError("Typed template schema is invalid JSON") from None
        if (
            not isinstance(schema, dict)
            or json.dumps(
                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            != schema_json
        ):
            raise ValueError("Typed template schema must be canonical JSON")

    def _version_row(  # noqa: PLR0913, PLR0917 - complete immutable version identity
        self,
        template_id: UUID,
        version_id: UUID,
        owner_id: UUID,
        actor_id: UUID,
        schema_json: str,
        schema_version: int,
        content: bytes,
        number: int,
        expected_revision: int,
        token: UUID,
        now: datetime,
    ) -> TypedTemplateVersionRow:
        return TypedTemplateVersionRow(
            id=str(version_id),
            template_id=str(template_id),
            number=number,
            expected_revision=expected_revision,
            object_owner_id=str(owner_id),
            created_by=str(actor_id),
            sha256=hashlib.sha256(content).hexdigest(),
            size=len(content),
            schema_version=schema_version,
            schema_json=schema_json,
            schema_sha256=hashlib.sha256(schema_json.encode("utf-8")).hexdigest(),
            publication_state="pending",
            publication_token=str(token),
            lease_expires_at=now + self._publication_lease,
            created_at=now,
        )

    def _write_and_verify(self, key: ObjectKey, content: bytes, digest: str) -> None:
        try:
            self._objects.put(key, content)
            if hashlib.sha256(self._objects.get(key)).hexdigest() != digest:
                raise TypedTemplateArtifactError(
                    "Typed template content integrity check failed"
                )
        except ObjectNotFoundError, ObjectStoreError:
            raise TypedTemplateArtifactError(
                "Typed template content publication failed"
            ) from None

    @staticmethod
    def _mutable(
        database: Session,
        actor_id: UUID,
        template_id: UUID,
        if_match: str,
        is_admin: bool,
    ) -> TypedTemplateRow:
        row = database.scalar(
            select(TypedTemplateRow)
            .where(
                TypedTemplateRow.id == str(template_id),
                TypedTemplateRow.publication_state == "published",
            )
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
            raise TypedTemplateNotFoundError("Typed template does not exist")
        if if_match != f'"{row.revision}"':
            raise TypedTemplateConflictError("Typed template changed")
        return row

    def _audit(  # noqa: PLR0913, PLR0917 - content-free audit identity
        self,
        database: Session,
        row: TypedTemplateRow,
        actor_id: UUID,
        operation: str,
        target_user_id: UUID | None = None,
        is_admin: bool = False,
        version_id: UUID | None = None,
    ) -> None:
        created_at = self._clock()
        database.add(
            TypedTemplateAuditRow(
                id=str(self._new_id()),
                template_id=row.id,
                owner_id=row.owner_id,
                actor_id=str(actor_id),
                operation=operation,
                version_id=str(version_id) if version_id else None,
                target_user_id=str(target_user_id) if target_user_id else None,
                revision=row.revision,
                administrator_intervention=False,
                created_at=created_at,
            )
        )
        record_content_audit(
            database,
            event_id=self._new_id(),
            owner_id=UUID(row.owner_id),
            actor_id=actor_id,
            operation=f"typed_template_{operation}",
            target_kind="typed_template",
            target_id=UUID(row.id),
            draft_id=None,
            draft_version=row.revision,
            created_at=created_at,
        )
