"""Authorization and generation fencing for Composer model connections."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Event
from typing import Any, Protocol
from uuid import UUID

from markweave.composer.egress import (
    AllowedDestination,
    ConnectionEgress,
    ConnectionPolicy,
    EgressCancelledError,
    EgressCapacityError,
    EgressPolicyError,
    EgressResponseError,
    EgressUnavailableError,
    _valid_model,
    validate_endpoint,
)
from markweave.composer.secrets import (
    EncryptedCredentials,
    PlainCredentials,
    SecretCipher,
    SecretError,
)

_MAX_CONNECTION_NAME_LENGTH = 128
_MIN_KEY_BYTE = 33
_MAX_KEY_BYTE = 126
_DEFAULT_CONNECTION_PAGE_SIZE = 50
_MAX_CONNECTION_PAGE_SIZE = 100


class ConnectionScope(StrEnum):
    INSTANCE = "instance"
    PERSONAL = "personal"


class IdentityMode(StrEnum):
    SHARED = "shared"
    INDIVIDUAL = "individual"


class ConnectionState(StrEnum):
    UNCONFIGURED = "unconfigured"
    DISABLED = "disabled"
    UNAUTHORIZED = "unauthorized"
    READY = "ready"
    OUTAGE = "outage"


class ConnectionAuthorizationError(Exception):
    """The current user may not inspect or use the requested connection."""


class ConnectionConflictError(Exception):
    """The connection changed while an operation was in progress."""


class ConnectionNotFoundError(Exception):
    """A visible connection no longer exists."""


class ConnectionConfigurationError(ValueError):
    """A connection cannot be used with its current configuration."""


@dataclass(frozen=True, slots=True)
class ConnectionActor:
    id: UUID
    is_admin: bool
    can_manage_personal: bool


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    """Content-free metadata; credentials live in a separate encrypted row."""

    id: UUID
    scope: ConnectionScope
    owner_id: UUID | None
    identity_mode: IdentityMode
    endpoint: str
    selected_model: str | None
    permitted_models: tuple[str, ...]
    enabled: bool
    allowed_user_ids: frozenset[UUID]
    version: int
    generation: int
    name: str = ""
    has_api_key: bool = False
    has_client_certificate: bool = False
    has_ca_bundle: bool = False

    def __post_init__(self) -> None:
        if self.scope is ConnectionScope.PERSONAL:
            if (
                self.owner_id is None
                or self.identity_mode is not IdentityMode.INDIVIDUAL
                or self.allowed_user_ids
            ):
                raise ConnectionConfigurationError(
                    "Personal connection metadata is invalid"
                )
        elif self.owner_id is not None:
            raise ConnectionConfigurationError("Instance connection owner is invalid")
        if self.version < 0 or self.generation < 0:
            raise ConnectionConfigurationError("Connection version is invalid")
        if (
            not self.name
            or len(self.name) > _MAX_CONNECTION_NAME_LENGTH
            or self.name.strip() != self.name
        ):
            raise ConnectionConfigurationError("Connection name is invalid")
        if len(set(self.permitted_models)) != len(self.permitted_models):
            raise ConnectionConfigurationError("Permitted models contain duplicates")
        if (
            self.selected_model is not None
            and self.selected_model not in self.permitted_models
        ):
            raise ConnectionConfigurationError("Selected model is not permitted")


@dataclass(frozen=True, slots=True)
class ConnectionAvailability:
    state: ConnectionState
    connection_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CredentialPresence:
    has_api_key: bool
    has_client_certificate: bool
    has_ca_bundle: bool


class ConnectionRepository(Protocol):
    """Storage must atomically compare version and bump version and generation."""

    def get_connection(self, connection_id: UUID) -> ConnectionRecord | None: ...

    def list_connections(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[ConnectionRecord, ...]: ...

    def list_visible_connections(
        self,
        user_id: UUID,
        is_admin: bool,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ConnectionRecord, ...]: ...

    def get_credentials(
        self, connection_id: UUID, user_id: UUID | None
    ) -> EncryptedCredentials | None: ...

    def can_manage_personal(self, user_id: UUID) -> bool: ...

    def get_outage(self, connection_id: UUID, user_id: UUID | None) -> bool: ...

    def set_outage(
        self,
        connection_id: UUID,
        user_id: UUID | None,
        outage: bool,
        *,
        expected_generation: int,
    ) -> bool: ...

    def save_connection(
        self,
        record: ConnectionRecord,
        *,
        expected_version: int | None,
        actor_id: UUID,
        credential_user_id: UUID | None = None,
        credentials: EncryptedCredentials | None = None,
    ) -> ConnectionRecord: ...

    def revoke_connection(
        self,
        record: ConnectionRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> ConnectionRecord: ...


class ConnectionGateway(Protocol):
    """Provider calls accept only a server-owned cancellation event."""

    def discover_models(
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[str, ...]: ...

    def chat(  # noqa: PLR0913 - cancellation is an explicit call contract
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]: ...


class ConnectionService:
    """Own connection access, credential writes, and stale-result rejection."""

    def __init__(
        self,
        repository: ConnectionRepository,
        cipher: SecretCipher,
        egress: ConnectionGateway,
        policy: ConnectionPolicy,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._egress = egress
        self._policy = policy

    def list_visible(
        self,
        actor: ConnectionActor,
        *,
        limit: int = _DEFAULT_CONNECTION_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ConnectionRecord, ...]:
        _validate_page(limit, offset)
        return tuple(
            self._project_visible(actor, record)
            for record in self._repository.list_visible_connections(
                actor.id, actor.is_admin, limit=limit, offset=offset
            )
        )

    def get_visible(
        self, actor: ConnectionActor, connection_id: UUID
    ) -> ConnectionRecord:
        """Authorize an exact record independently of list pagination."""

        record = self._get(connection_id)
        if not _may_manage(actor, record) and not _may_use(actor, record):
            raise ConnectionNotFoundError("Connection was not found")
        return self._project_visible(actor, record)

    def _project_visible(
        self, actor: ConnectionActor, record: ConnectionRecord
    ) -> ConnectionRecord:
        return replace(
            record,
            allowed_user_ids=(
                record.allowed_user_ids if actor.is_admin else frozenset()
            ),
            **_presence_dict(self.credential_presence(actor, record.id)),
        )

    def credential_presence(
        self, actor: ConnectionActor, connection_id: UUID
    ) -> CredentialPresence:
        """Return only field presence for the actor's own selected identity."""

        record = self._get(connection_id)
        if not _may_manage(actor, record) and not _may_use(actor, record):
            raise ConnectionAuthorizationError("Connection access is denied")
        encrypted = self._repository.get_credentials(
            connection_id, _credential_user_id(actor, record)
        )
        return CredentialPresence(
            has_api_key=encrypted is not None and encrypted.api_key is not None,
            has_client_certificate=(
                encrypted is not None
                and encrypted.client_certificate is not None
                and encrypted.client_private_key is not None
            ),
            has_ca_bundle=encrypted is not None and encrypted.ca_bundle is not None,
        )

    def availability(self, actor: ConnectionActor) -> ConnectionAvailability:
        if not self._repository.list_connections(limit=1):
            return ConnectionAvailability(ConnectionState.UNCONFIGURED)
        eligible_found = False
        outage_connection: UUID | None = None
        offset = 0
        while True:
            page = self._repository.list_visible_connections(
                actor.id,
                actor.is_admin,
                limit=_DEFAULT_CONNECTION_PAGE_SIZE,
                offset=offset,
            )
            for record in page:
                if not _may_use(actor, record) or not self._current_capability(
                    actor, record
                ):
                    continue
                eligible_found = True
                if not record.enabled or record.selected_model is None:
                    continue
                identity = _credential_user_id(actor, record)
                encrypted = self._repository.get_credentials(record.id, identity)
                if encrypted is None or not _has_auth_credential(encrypted):
                    continue
                if self._repository.get_outage(record.id, identity):
                    outage_connection = record.id
                else:
                    return ConnectionAvailability(ConnectionState.READY, record.id)
            if len(page) < _DEFAULT_CONNECTION_PAGE_SIZE:
                break
            offset += len(page)
        if outage_connection is not None:
            return ConnectionAvailability(ConnectionState.OUTAGE, outage_connection)
        return ConnectionAvailability(
            ConnectionState.DISABLED if eligible_found else ConnectionState.UNAUTHORIZED
        )

    def save(
        self,
        actor: ConnectionActor,
        record: ConnectionRecord,
        *,
        expected_version: int | None,
        credentials: PlainCredentials | None = None,
        credential_user_id: UUID | None = None,
    ) -> ConnectionRecord:
        """Create or replace metadata through an optimistic atomic repository write."""

        self._validate_record(record)
        prior = self._repository.get_connection(record.id)
        if prior is None:
            if expected_version is not None:
                raise ConnectionConflictError("Connection changed")
        elif (
            expected_version is None
            or prior.version != expected_version
            or prior.scope is not record.scope
            or prior.owner_id != record.owner_id
            or prior.identity_mode is not record.identity_mode
        ):
            raise ConnectionConflictError("Connection changed")
        if (
            not _may_manage(actor, record)
            or (prior is not None and not _may_manage(actor, prior))
            or not self._current_capability(actor, record)
        ):
            raise ConnectionAuthorizationError("Connection access is denied")
        sealed = None
        if credentials is not None:
            self._check_credential_target(actor, record, credential_user_id)
            self._validate_credentials(credentials)
            sealed = self._cipher.seal_credentials(record.id, credentials)
            if record.identity_mode is IdentityMode.SHARED:
                record = _with_presence(record, sealed)
        return self._repository.save_connection(
            record,
            expected_version=expected_version,
            actor_id=actor.id,
            credential_user_id=credential_user_id,
            credentials=sealed,
        )

    def set_credentials(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        credentials: PlainCredentials,
        *,
        expected_version: int,
        credential_user_id: UUID | None = None,
    ) -> ConnectionRecord:
        record = self._get(connection_id)
        self._check_credential_target(actor, record, credential_user_id)
        self._validate_credentials(credentials, require_auth=False)
        updates = self._cipher.seal_credentials(connection_id, credentials)
        previous = self._repository.get_credentials(connection_id, credential_user_id)
        encrypted = _merge_credentials(previous, updates)
        if not _has_auth_credential(encrypted):
            raise ConnectionConfigurationError(
                "An API key or client certificate is required"
            )
        if record.identity_mode is IdentityMode.SHARED:
            record = _with_presence(record, encrypted)
        return self._repository.save_connection(
            record,
            expected_version=expected_version,
            actor_id=actor.id,
            credential_user_id=credential_user_id,
            credentials=encrypted,
        )

    def revoke_credentials(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        *,
        expected_version: int,
        credential_user_id: UUID | None = None,
    ) -> ConnectionRecord:
        record = self._get(connection_id)
        self._check_credential_target(actor, record, credential_user_id)
        encrypted = EncryptedCredentials()
        if record.identity_mode is IdentityMode.SHARED:
            record = _with_presence(record, encrypted)
        return self._repository.save_connection(
            record,
            expected_version=expected_version,
            actor_id=actor.id,
            credential_user_id=credential_user_id,
            credentials=encrypted,
        )

    def revoke(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        *,
        expected_version: int,
    ) -> ConnectionRecord:
        """Atomically disable a connection and destroy all associated credentials."""

        record = self._get(connection_id)
        if not _may_manage(actor, record) or not self._current_capability(
            actor, record
        ):
            raise ConnectionAuthorizationError("Connection access is denied")
        return self._repository.revoke_connection(
            record,
            expected_version=expected_version,
            actor_id=actor.id,
        )

    def discover_models(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[str, ...]:
        record, credentials = self._prepare(
            actor, connection_id, allow_manager=True, cancel_event=cancel_event
        )
        try:
            discovered = self._egress.discover_models(
                record.endpoint, credentials, cancel_event=cancel_event
            )
        except EgressUnavailableError, EgressResponseError:
            _check_cancelled(cancel_event)
            self._repository.set_outage(
                record.id,
                _credential_user_id(actor, record),
                True,
                expected_generation=record.generation,
            )
            raise
        self._fence(actor, record, allow_manager=True, cancel_event=cancel_event)
        _check_cancelled(cancel_event)
        self._repository.set_outage(
            record.id,
            _credential_user_id(actor, record),
            False,
            expected_generation=record.generation,
        )
        _check_cancelled(cancel_event)
        if _may_manage(actor, record):
            return discovered
        return tuple(model for model in discovered if model in record.permitted_models)

    def test(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        *,
        model: str | None = None,
        cancel_event: Event | None = None,
    ) -> None:
        record, credentials = self._prepare(
            actor, connection_id, allow_manager=True, cancel_event=cancel_event
        )
        selected = model or record.selected_model
        if selected is None or selected not in record.permitted_models:
            raise ConnectionConfigurationError("A permitted model must be selected")
        try:
            self._egress.chat(
                record.endpoint,
                credentials,
                model=selected,
                messages=[{"role": "user", "content": "Reply OK."}],
                max_output_tokens=1,
                cancel_event=cancel_event,
            )
        except EgressUnavailableError, EgressResponseError:
            _check_cancelled(cancel_event)
            self._repository.set_outage(
                record.id,
                _credential_user_id(actor, record),
                True,
                expected_generation=record.generation,
            )
            raise
        self._fence(actor, record, allow_manager=True, cancel_event=cancel_event)
        _check_cancelled(cancel_event)
        self._repository.set_outage(
            record.id,
            _credential_user_id(actor, record),
            False,
            expected_generation=record.generation,
        )
        _check_cancelled(cancel_event)

    def chat(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        messages: list[dict[str, str]],
        *,
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Return a completion only while the caller's operation remains active.

        The caller owns durable operation state and final proposal publication.
        """

        record, credentials = self._prepare(
            actor, connection_id, cancel_event=cancel_event
        )
        if record.selected_model is None:
            raise ConnectionConfigurationError("A permitted model must be selected")
        try:
            response = self._egress.chat(
                record.endpoint,
                credentials,
                model=record.selected_model,
                messages=messages,
                max_output_tokens=max_output_tokens,
                cancel_event=cancel_event,
            )
        except EgressUnavailableError, EgressResponseError:
            _check_cancelled(cancel_event)
            self._repository.set_outage(
                record.id,
                _credential_user_id(actor, record),
                True,
                expected_generation=record.generation,
            )
            raise
        self._fence(actor, record, cancel_event=cancel_event)
        _check_cancelled(cancel_event)
        self._repository.set_outage(
            record.id,
            _credential_user_id(actor, record),
            False,
            expected_generation=record.generation,
        )
        _check_cancelled(cancel_event)
        return response

    def _get(self, connection_id: UUID) -> ConnectionRecord:
        record = self._repository.get_connection(connection_id)
        if record is None:
            raise ConnectionNotFoundError("Connection was not found")
        return record

    def _prepare(
        self,
        actor: ConnectionActor,
        connection_id: UUID,
        *,
        allow_manager: bool = False,
        cancel_event: Event | None = None,
    ) -> tuple[ConnectionRecord, PlainCredentials]:
        _check_cancelled(cancel_event)
        record = self._get(connection_id)
        if not _may_use(actor, record) and not (
            allow_manager and _may_manage(actor, record)
        ):
            raise ConnectionAuthorizationError("Connection access is denied")
        if not self._current_capability(actor, record):
            raise ConnectionAuthorizationError("Connection access is denied")
        if not record.enabled:
            raise ConnectionConfigurationError("Connection is disabled")
        self._validate_record(record)
        encrypted = self._repository.get_credentials(
            record.id, _credential_user_id(actor, record)
        )
        if encrypted is None:
            raise ConnectionConfigurationError(
                "Connection credential is not configured"
            )
        try:
            credentials = self._cipher.open_credentials(record.id, encrypted)
        except SecretError:
            raise ConnectionConfigurationError(
                "Connection credential is unavailable"
            ) from None
        self._validate_credentials(credentials)
        _check_cancelled(cancel_event)
        return record, credentials

    def _fence(
        self,
        actor: ConnectionActor,
        original: ConnectionRecord,
        *,
        allow_manager: bool = False,
        cancel_event: Event | None = None,
    ) -> None:
        _check_cancelled(cancel_event)
        current = self._repository.get_connection(original.id)
        if (
            current is None
            or current.generation != original.generation
            or not current.enabled
            or not self._current_capability(actor, current)
            or (
                not _may_use(actor, current)
                and not (allow_manager and _may_manage(actor, current))
            )
        ):
            raise ConnectionConflictError("Connection changed during model call")
        _check_cancelled(cancel_event)

    def _current_capability(
        self, actor: ConnectionActor, record: ConnectionRecord
    ) -> bool:
        if record.scope is ConnectionScope.PERSONAL:
            return actor.can_manage_personal and self._repository.can_manage_personal(
                actor.id
            )
        return True

    def _validate_record(self, record: ConnectionRecord) -> None:
        validate_endpoint(record.endpoint, self._policy)
        if len(record.allowed_user_ids) > self._policy.maximum_allowed_users:
            raise ConnectionConfigurationError(
                "Connection access list exceeds its configured limit"
            )
        if len(record.permitted_models) > self._policy.maximum_models or any(
            not _valid_model(model, self._policy.maximum_model_name_length)
            for model in record.permitted_models
        ):
            raise ConnectionConfigurationError("Permitted model list is invalid")

    def _validate_credentials(
        self, credentials: PlainCredentials, *, require_auth: bool = True
    ) -> None:
        values = (
            credentials.api_key,
            credentials.client_certificate,
            credentials.client_private_key,
            credentials.ca_bundle,
        )
        if all(value is None for value in values):
            raise ConnectionConfigurationError("No credential field was supplied")
        if require_auth and all(value is None for value in values[:3]):
            raise ConnectionConfigurationError(
                "An API key or client certificate is required"
            )
        if any(
            value is not None and len(value) > self._policy.maximum_credential_bytes
            for value in values
        ):
            raise ConnectionConfigurationError(
                "Connection credential exceeds its limit"
            )
        if credentials.api_key is not None and any(
            not _MIN_KEY_BYTE <= byte <= _MAX_KEY_BYTE for byte in credentials.api_key
        ):
            raise ConnectionConfigurationError("API credential is invalid")

    def _check_credential_target(
        self,
        actor: ConnectionActor,
        record: ConnectionRecord,
        credential_user_id: UUID | None,
    ) -> None:
        if not self._current_capability(actor, record):
            raise ConnectionAuthorizationError("Credential access is denied")
        if record.identity_mode is IdentityMode.SHARED:
            if credential_user_id is not None or not _may_manage(actor, record):
                raise ConnectionAuthorizationError("Credential access is denied")
        elif credential_user_id != actor.id or (
            not _may_use(actor, record) and not _may_manage(actor, record)
        ):
            raise ConnectionAuthorizationError("Credential access is denied")


def _may_manage(actor: ConnectionActor, record: ConnectionRecord) -> bool:
    if record.scope is ConnectionScope.INSTANCE:
        return actor.is_admin
    return actor.can_manage_personal and record.owner_id == actor.id


def _may_use(actor: ConnectionActor, record: ConnectionRecord) -> bool:
    if record.scope is ConnectionScope.PERSONAL:
        return record.owner_id == actor.id
    return actor.id in record.allowed_user_ids


def _credential_user_id(
    actor: ConnectionActor, record: ConnectionRecord
) -> UUID | None:
    return actor.id if record.identity_mode is IdentityMode.INDIVIDUAL else None


def _with_presence(
    record: ConnectionRecord, encrypted: EncryptedCredentials
) -> ConnectionRecord:
    return replace(
        record,
        has_api_key=encrypted.api_key is not None,
        has_client_certificate=(
            encrypted.client_certificate is not None
            and encrypted.client_private_key is not None
        ),
        has_ca_bundle=encrypted.ca_bundle is not None,
    )


def _merge_credentials(
    previous: EncryptedCredentials | None, updates: EncryptedCredentials
) -> EncryptedCredentials:
    old = previous or EncryptedCredentials()
    return EncryptedCredentials(
        api_key=updates.api_key if updates.api_key is not None else old.api_key,
        client_certificate=(
            updates.client_certificate
            if updates.client_certificate is not None
            else old.client_certificate
        ),
        client_private_key=(
            updates.client_private_key
            if updates.client_private_key is not None
            else old.client_private_key
        ),
        ca_bundle=updates.ca_bundle if updates.ca_bundle is not None else old.ca_bundle,
    )


def _has_auth_credential(credentials: EncryptedCredentials) -> bool:
    return credentials.api_key is not None or (
        credentials.client_certificate is not None
        and credentials.client_private_key is not None
    )


def _presence_dict(presence: CredentialPresence) -> dict[str, bool]:
    return {
        "has_api_key": presence.has_api_key,
        "has_client_certificate": presence.has_client_certificate,
        "has_ca_bundle": presence.has_ca_bundle,
    }


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise EgressCancelledError("Model operation was cancelled")


def _validate_page(limit: int, offset: int) -> None:
    if (
        type(limit) is not int
        or not 1 <= limit <= _MAX_CONNECTION_PAGE_SIZE
        or type(offset) is not int
        or offset < 0
    ):
        raise ValueError("Connection pagination values are invalid")


__all__ = [
    "AllowedDestination",
    "ConnectionActor",
    "ConnectionAuthorizationError",
    "ConnectionAvailability",
    "ConnectionConfigurationError",
    "ConnectionConflictError",
    "ConnectionEgress",
    "ConnectionGateway",
    "ConnectionNotFoundError",
    "ConnectionPolicy",
    "ConnectionRecord",
    "ConnectionRepository",
    "ConnectionScope",
    "ConnectionService",
    "ConnectionState",
    "CredentialPresence",
    "EgressCancelledError",
    "EgressCapacityError",
    "EgressPolicyError",
    "EgressResponseError",
    "EgressUnavailableError",
    "EncryptedCredentials",
    "IdentityMode",
    "PlainCredentials",
    "SecretCipher",
]
