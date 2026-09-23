"""Transactional Composer connection metadata, grants, and encrypted envelopes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, delete, exists, or_, select, text, true, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.connections import (
    ConnectionConfigurationError,
    ConnectionConflictError,
    ConnectionRecord,
    ConnectionScope,
    IdentityMode,
)
from markweave.composer.secrets import EncryptedCredentials, SecretCipher, SecretError
from markweave.persistence.composer.common import DEFAULT_PAGE_LIMIT, validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerAdminPolicyAuditRow,
    ComposerConnectionAuditRow,
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    ComposerKeyIdentityRow,
    ComposerPermissionAuditRow,
    ComposerPersonalPermissionRow,
    RetentionCleanupRunRow,
    UserRow,
)
from markweave.persistence.sql import serialize_sqlite_write


def _connection_grants(
    database: Session, connection_id: str, maximum_allowed_users: int
) -> frozenset[UUID]:
    """Materialize one configured ACL page and an overflow sentinel."""

    values = tuple(
        database.scalars(
            select(ComposerConnectionGrantRow.user_id)
            .where(ComposerConnectionGrantRow.connection_id == connection_id)
            .order_by(ComposerConnectionGrantRow.user_id)
            .limit(maximum_allowed_users + 1)
        )
    )
    if len(values) > maximum_allowed_users:
        raise PersistenceError from None
    return frozenset(UUID(value) for value in values)


class SqlConnectionRepository:
    """Version-fenced connection repository shared by SQLite and PostgreSQL."""

    def __init__(
        self, engine: Engine, maximum_allowed_users: int | None = None
    ) -> None:
        if maximum_allowed_users is not None and (
            isinstance(maximum_allowed_users, bool)
            or not isinstance(maximum_allowed_users, int)
            or maximum_allowed_users <= 0
        ):
            raise ValueError("Maximum allowed users must be positive")
        self._engine = engine
        self._maximum_allowed_users = maximum_allowed_users

    def bind_key_identity(self, cipher: SecretCipher) -> None:
        """Verify a restored key before allowing any credential read or write."""

        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                identity = database.scalar(
                    select(ComposerKeyIdentityRow)
                    .where(ComposerKeyIdentityRow.id == 1)
                    .with_for_update()
                )
                if identity is not None:
                    if identity.fingerprint != cipher.fingerprint:
                        raise SecretError("Composer encryption key does not match data")
                    return
                legacy = database.scalar(
                    select(ComposerCredentialRow).where(
                        or_(
                            ComposerCredentialRow.api_key.is_not(None),
                            ComposerCredentialRow.client_certificate.is_not(None),
                            ComposerCredentialRow.client_private_key.is_not(None),
                            ComposerCredentialRow.ca_bundle.is_not(None),
                        )
                    )
                )
                if legacy is not None:
                    cipher.open_credentials(
                        UUID(legacy.connection_id),
                        EncryptedCredentials(
                            legacy.api_key,
                            legacy.client_certificate,
                            legacy.client_private_key,
                            legacy.ca_bundle,
                        ),
                    )
                database.add(
                    ComposerKeyIdentityRow(id=1, fingerprint=cipher.fingerprint)
                )
                database.flush()
        except IntegrityError:
            # A second process may bind the same restored key concurrently.
            with Session(self._engine) as database:
                identity = database.get(ComposerKeyIdentityRow, 1)
                if identity is None or identity.fingerprint != cipher.fingerprint:
                    raise SecretError(
                        "Composer encryption key does not match data"
                    ) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def _required_limit(self) -> int:
        if self._maximum_allowed_users is None:
            raise ConnectionConfigurationError("Connection limit is not configured")
        return self._maximum_allowed_users

    def get_connection(self, connection_id: UUID) -> ConnectionRecord | None:
        self._required_limit()
        try:
            with Session(self._engine) as database:
                row = database.get(ComposerConnectionRow, str(connection_id))
                return self._record(database, row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_connections(
        self, *, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0
    ) -> tuple[ConnectionRecord, ...]:
        validate_page(limit, offset)
        self._required_limit()
        try:
            with Session(self._engine) as database:
                return tuple(
                    self._record(database, row)
                    for row in database.scalars(
                        select(ComposerConnectionRow)
                        .order_by(ComposerConnectionRow.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_visible_connections(
        self,
        user_id: UUID,
        is_admin: bool,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[ConnectionRecord, ...]:
        """Page only connections authorized for this user in the SQL query."""

        validate_page(limit, offset)
        self._required_limit()
        grant_exists = exists(
            select(ComposerConnectionGrantRow.connection_id).where(
                ComposerConnectionGrantRow.connection_id == ComposerConnectionRow.id,
                ComposerConnectionGrantRow.user_id == str(user_id),
            )
        )
        visible = or_(
            and_(
                ComposerConnectionRow.scope == ConnectionScope.PERSONAL.value,
                ComposerConnectionRow.owner_id == str(user_id),
            ),
            and_(
                ComposerConnectionRow.scope == ConnectionScope.INSTANCE.value,
                true() if is_admin else grant_exists,
            ),
        )
        try:
            with Session(self._engine) as database:
                return tuple(
                    self._record(database, row)
                    for row in database.scalars(
                        select(ComposerConnectionRow)
                        .where(visible)
                        .order_by(ComposerConnectionRow.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_credentials(
        self, connection_id: UUID, user_id: UUID | None
    ) -> EncryptedCredentials | None:
        self._required_limit()
        try:
            with Session(self._engine) as database:
                row = database.scalar(
                    select(ComposerCredentialRow).where(
                        ComposerCredentialRow.connection_id == str(connection_id),
                        ComposerCredentialRow.user_id
                        == (str(user_id) if user_id is not None else None),
                    )
                )
                if row is None:
                    return None
                return EncryptedCredentials(
                    row.api_key,
                    row.client_certificate,
                    row.client_private_key,
                    row.ca_bundle,
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_outage(self, connection_id: UUID, user_id: UUID | None) -> bool:
        self._required_limit()
        try:
            with Session(self._engine) as database:
                if user_id is not None:
                    row = database.scalar(
                        select(ComposerCredentialRow).where(
                            ComposerCredentialRow.connection_id == str(connection_id),
                            ComposerCredentialRow.user_id == str(user_id),
                        )
                    )
                    return bool(row and row.outage)
                row = database.get(ComposerConnectionRow, str(connection_id))
                return bool(row and row.outage)
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_outage(
        self,
        connection_id: UUID,
        user_id: UUID | None,
        outage: bool,
        *,
        expected_generation: int,
    ) -> bool:
        self._required_limit()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ComposerConnectionRow)
                    .where(ComposerConnectionRow.id == str(connection_id))
                    .with_for_update()
                )
                if row is None or row.generation != expected_generation:
                    return False
                if user_id is None:
                    row.outage = outage
                else:
                    credential = database.scalar(
                        select(ComposerCredentialRow)
                        .where(
                            ComposerCredentialRow.connection_id == str(connection_id),
                            ComposerCredentialRow.user_id == str(user_id),
                        )
                        .with_for_update()
                    )
                    if credential is None:
                        return False
                    credential.outage = outage
                return True
        except SQLAlchemyError:
            raise PersistenceError from None

    def can_manage_personal(self, user_id: UUID) -> bool:
        return self.get_personal_permission(user_id)[0]

    def get_personal_permission(self, user_id: UUID) -> tuple[bool, int]:
        try:
            with Session(self._engine) as database:
                row = database.get(ComposerPersonalPermissionRow, str(user_id))
                return (row.enabled, row.version) if row is not None else (False, 0)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_personal_permissions(
        self, *, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0
    ) -> tuple[tuple[UUID, str, bool, int], ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                return tuple(
                    (UUID(user_id), username, bool(enabled), int(version or 0))
                    for user_id, username, enabled, version in database.execute(
                        select(
                            UserRow.id,
                            UserRow.username,
                            ComposerPersonalPermissionRow.enabled,
                            ComposerPersonalPermissionRow.version,
                        )
                        .outerjoin(
                            ComposerPersonalPermissionRow,
                            ComposerPersonalPermissionRow.user_id == UserRow.id,
                        )
                        .order_by(UserRow.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_personal_permission_details(
        self, user_id: UUID
    ) -> tuple[UUID, str, bool, int] | None:
        try:
            with Session(self._engine) as database:
                row = database.execute(
                    select(
                        UserRow.id,
                        UserRow.username,
                        ComposerPersonalPermissionRow.enabled,
                        ComposerPersonalPermissionRow.version,
                    )
                    .outerjoin(
                        ComposerPersonalPermissionRow,
                        ComposerPersonalPermissionRow.user_id == UserRow.id,
                    )
                    .where(UserRow.id == str(user_id))
                ).one_or_none()
                if row is None:
                    return None
                return (
                    UUID(row.id),
                    row.username,
                    bool(row.enabled),
                    int(row.version or 0),
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def set_personal_permission(
        self,
        user_id: UUID,
        enabled: bool,
        *,
        expected_version: int | None,
        actor_id: UUID,
    ) -> int:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ComposerPersonalPermissionRow)
                    .where(ComposerPersonalPermissionRow.user_id == str(user_id))
                    .with_for_update()
                )
                current_version = row.version if row is not None else 0
                if expected_version is None or expected_version != current_version:
                    raise ConnectionConflictError("Personal permission changed")
                if row is None:
                    row = ComposerPersonalPermissionRow(
                        user_id=str(user_id), enabled=enabled, version=1
                    )
                    database.add(row)
                else:
                    row.enabled = enabled
                    row.version += 1
                database.add(
                    ComposerPermissionAuditRow(
                        id=str(uuid4()),
                        user_id=str(user_id),
                        actor_id=str(actor_id),
                        enabled=enabled,
                        version=row.version,
                        created_at=datetime.now(UTC),
                    )
                )
                database.flush()
                return row.version
        except ConnectionConflictError:
            raise
        except IntegrityError:
            raise ConnectionConflictError("Personal permission changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def save_connection(  # noqa: PLR0912, PLR0915 - one atomic policy, grant, secret, and audit write
        self,
        record: ConnectionRecord,
        *,
        expected_version: int | None,
        actor_id: UUID,
        credential_user_id: UUID | None = None,
        credentials: EncryptedCredentials | None = None,
    ) -> ConnectionRecord:
        maximum_allowed_users = self._required_limit()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if len(record.allowed_user_ids) > maximum_allowed_users:
                    raise ConnectionConfigurationError(
                        "Connection grant limit exceeded"
                    )
                row = database.scalar(
                    select(ComposerConnectionRow)
                    .where(ComposerConnectionRow.id == str(record.id))
                    .with_for_update()
                )
                created = row is None
                old_grants = (
                    _connection_grants(database, str(record.id), maximum_allowed_users)
                    if row is not None
                    else frozenset()
                )
                was_enabled = row.enabled if row is not None else False
                if row is None:
                    if expected_version is not None:
                        raise ConnectionConflictError("Connection changed")
                    row = ComposerConnectionRow(
                        id=str(record.id),
                        scope=record.scope.value,
                        owner_id=str(record.owner_id) if record.owner_id else None,
                        identity_mode=record.identity_mode.value,
                        name=record.name,
                        endpoint=record.endpoint,
                        selected_model=record.selected_model,
                        permitted_models=json.dumps(record.permitted_models),
                        enabled=record.enabled,
                        version=1,
                        generation=1,
                        outage=False,
                    )
                    database.add(row)
                    database.flush()
                else:
                    if (
                        expected_version != row.version
                        or row.scope != record.scope.value
                        or row.owner_id
                        != (str(record.owner_id) if record.owner_id else None)
                    ):
                        raise ConnectionConflictError("Connection changed")
                    row.identity_mode = record.identity_mode.value
                    row.name = record.name
                    row.endpoint = record.endpoint
                    row.selected_model = record.selected_model
                    row.permitted_models = json.dumps(record.permitted_models)
                    row.enabled = record.enabled
                    row.version += 1
                    row.generation += 1
                    row.outage = False
                    database.execute(
                        update(ComposerCredentialRow)
                        .where(ComposerCredentialRow.connection_id == str(record.id))
                        .values(outage=False)
                    )

                database.execute(
                    delete(ComposerConnectionGrantRow).where(
                        ComposerConnectionGrantRow.connection_id == str(record.id)
                    )
                )
                database.add_all(
                    ComposerConnectionGrantRow(
                        connection_id=str(record.id), user_id=str(user_id)
                    )
                    for user_id in sorted(record.allowed_user_ids, key=str)
                )
                if credentials is not None:
                    identity = str(credential_user_id) if credential_user_id else None
                    saved = database.scalar(
                        select(ComposerCredentialRow)
                        .where(
                            ComposerCredentialRow.connection_id == str(record.id),
                            ComposerCredentialRow.user_id == identity,
                        )
                        .with_for_update()
                    )
                    if saved is None:
                        database.add(
                            ComposerCredentialRow(
                                id=str(uuid4()),
                                connection_id=str(record.id),
                                user_id=identity,
                                api_key=credentials.api_key,
                                client_certificate=credentials.client_certificate,
                                client_private_key=credentials.client_private_key,
                                ca_bundle=credentials.ca_bundle,
                                outage=False,
                            )
                        )
                    elif all(
                        value is None
                        for value in (
                            credentials.api_key,
                            credentials.client_certificate,
                            credentials.client_private_key,
                            credentials.ca_bundle,
                        )
                    ):
                        database.delete(saved)
                    else:
                        saved.api_key = credentials.api_key
                        saved.client_certificate = credentials.client_certificate
                        saved.client_private_key = credentials.client_private_key
                        saved.ca_bundle = credentials.ca_bundle
                        saved.outage = False
                actions = ["create" if created else "update"]
                if was_enabled and not record.enabled:
                    actions.append("disable")
                if old_grants != record.allowed_user_ids:
                    actions.append(
                        "grants_revoke"
                        if old_grants - record.allowed_user_ids
                        else "grants_update"
                    )
                if credentials is not None:
                    actions.append(
                        "credential_revoke"
                        if all(
                            value is None
                            for value in (
                                credentials.api_key,
                                credentials.client_certificate,
                                credentials.client_private_key,
                                credentials.ca_bundle,
                            )
                        )
                        else "credential_rotate"
                    )
                now = datetime.now(UTC)
                for operation in actions:
                    database.add(
                        ComposerConnectionAuditRow(
                            id=str(uuid4()),
                            connection_id=str(record.id),
                            actor_id=str(actor_id),
                            operation=operation,
                            scope=record.scope.value,
                            owner_id=str(record.owner_id) if record.owner_id else None,
                            target_user_id=(
                                str(credential_user_id) if credential_user_id else None
                            ),
                            version=row.version,
                            generation=row.generation,
                            created_at=now,
                        )
                    )
                database.flush()
                return self._record(database, row)
        except ConnectionConflictError:
            raise
        except IntegrityError:
            raise ConnectionConflictError("Connection changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def revoke_connection(
        self, record: ConnectionRecord, *, expected_version: int, actor_id: UUID
    ) -> ConnectionRecord:
        """Disable a connection and remove every reusable credential identity."""

        self._required_limit()

        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ComposerConnectionRow)
                    .where(ComposerConnectionRow.id == str(record.id))
                    .with_for_update()
                )
                if (
                    row is None
                    or row.version != expected_version
                    or row.scope != record.scope.value
                    or row.owner_id
                    != (str(record.owner_id) if record.owner_id else None)
                ):
                    raise ConnectionConflictError("Connection changed")
                row.enabled = False
                row.version += 1
                row.generation += 1
                row.outage = False
                database.execute(
                    delete(ComposerConnectionGrantRow).where(
                        ComposerConnectionGrantRow.connection_id == str(record.id)
                    )
                )
                database.execute(
                    delete(ComposerCredentialRow).where(
                        ComposerCredentialRow.connection_id == str(record.id)
                    )
                )
                database.add(
                    ComposerConnectionAuditRow(
                        id=str(uuid4()),
                        connection_id=str(record.id),
                        actor_id=str(actor_id),
                        operation="revoke",
                        scope=row.scope,
                        owner_id=row.owner_id,
                        target_user_id=None,
                        version=row.version,
                        generation=row.generation,
                        created_at=datetime.now(UTC),
                    )
                )
                database.flush()
                return self._record(database, row)
        except ConnectionConflictError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_connection_audit(self, *, cutoff_at: datetime, limit: int) -> int:
        """Apply configured retention to content-free Composer audit evidence."""

        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                connection_ids = tuple(
                    database.scalars(
                        select(ComposerConnectionAuditRow.id)
                        .where(ComposerConnectionAuditRow.created_at < cutoff_at)
                        .order_by(
                            ComposerConnectionAuditRow.created_at,
                            ComposerConnectionAuditRow.id,
                        )
                        .limit(limit)
                    )
                )
                remaining = limit - len(connection_ids)
                permission_ids = (
                    tuple(
                        database.scalars(
                            select(ComposerPermissionAuditRow.id)
                            .where(ComposerPermissionAuditRow.created_at < cutoff_at)
                            .order_by(
                                ComposerPermissionAuditRow.created_at,
                                ComposerPermissionAuditRow.id,
                            )
                            .limit(remaining)
                        )
                    )
                    if remaining
                    else ()
                )
                remaining -= len(permission_ids)
                policy_ids = (
                    tuple(
                        database.scalars(
                            select(ComposerAdminPolicyAuditRow.id)
                            .where(ComposerAdminPolicyAuditRow.created_at < cutoff_at)
                            .order_by(
                                ComposerAdminPolicyAuditRow.created_at,
                                ComposerAdminPolicyAuditRow.id,
                            )
                            .limit(remaining)
                        )
                    )
                    if remaining
                    else ()
                )
                removed = 0
                if connection_ids or permission_ids or policy_ids:
                    guard_id = str(uuid4())
                    database.execute(
                        text("INSERT INTO audit_cleanup_guards (id) VALUES (:id)"),
                        {"id": guard_id},
                    )
                    for row_type, ids in (
                        (ComposerConnectionAuditRow, connection_ids),
                        (ComposerPermissionAuditRow, permission_ids),
                        (ComposerAdminPolicyAuditRow, policy_ids),
                    ):
                        if ids:
                            removed += int(
                                database.connection()
                                .execute(delete(row_type).where(row_type.id.in_(ids)))
                                .rowcount
                                or 0
                            )
                    database.execute(
                        text("DELETE FROM audit_cleanup_guards WHERE id = :id"),
                        {"id": guard_id},
                    )
                database.add(
                    RetentionCleanupRunRow(
                        id=str(uuid4()),
                        kind="composer_connection_audit",
                        cutoff_at=cutoff_at,
                        removed_count=removed,
                        completed_at=datetime.now(UTC),
                    )
                )
                return removed
        except SQLAlchemyError:
            raise PersistenceError from None

    def _record(
        self, database: Session, row: ComposerConnectionRow
    ) -> ConnectionRecord:
        grants = _connection_grants(database, row.id, self._required_limit())
        shared = (
            database.scalar(
                select(ComposerCredentialRow).where(
                    ComposerCredentialRow.connection_id == row.id,
                    ComposerCredentialRow.user_id.is_(None),
                )
            )
            if row.scope == "instance" and row.identity_mode == "shared"
            else None
        )
        return ConnectionRecord(
            id=UUID(row.id),
            scope=ConnectionScope(row.scope),
            owner_id=UUID(row.owner_id) if row.owner_id else None,
            identity_mode=IdentityMode(row.identity_mode),
            name=row.name,
            endpoint=row.endpoint,
            selected_model=row.selected_model,
            permitted_models=tuple(json.loads(row.permitted_models)),
            enabled=row.enabled,
            allowed_user_ids=grants,
            version=row.version,
            generation=row.generation,
            has_api_key=bool(shared and shared.api_key),
            has_client_certificate=bool(shared and shared.client_certificate),
            has_ca_bundle=bool(shared and shared.ca_bundle),
        )
