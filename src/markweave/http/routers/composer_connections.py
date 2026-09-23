"""Authorized Composer connection setup and capability routes."""

from dataclasses import replace
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import SecretStr

from markweave.auth.models import Role, User
from markweave.composer.connections import (
    ConnectionActor,
    ConnectionAuthorizationError,
    ConnectionNotFoundError,
    ConnectionRecord,
    ConnectionScope,
    ConnectionService,
    IdentityMode,
    PlainCredentials,
)
from markweave.http.composer_errors import (
    ComposerPreconditionInvalidError,
    ComposerPreconditionRequiredError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerAvailabilityState,
    ComposerCapabilitiesResponse,
    ConnectionCreateRequest,
    ConnectionListResponse,
    ConnectionModelsResponse,
    ConnectionResponse,
    ConnectionTestRequest,
    ConnectionTestResponse,
    ConnectionUpdateRequest,
    CredentialWriteRequest,
    PersonalPermissionListResponse,
    PersonalPermissionResponse,
    PersonalPermissionUpdateRequest,
)
from markweave.http.composer_schemas import (
    ConnectionIdentityMode as HttpIdentityMode,
)
from markweave.http.composer_schemas import (
    ConnectionScope as HttpConnectionScope,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import capacity_error_responses, error_responses

_MINIMUM_ETAG_CHARACTERS = 3
_MAXIMUM_VERSION_DIGITS = 19
_MAXIMUM_SIGNED_VERSION = (1 << 63) - 1
_MAXIMUM_OFFSET = 2_147_483_647


def _version(if_match: str | None) -> int:
    if if_match is None:
        raise ComposerPreconditionRequiredError
    if (
        len(if_match) < _MINIMUM_ETAG_CHARACTERS
        or not if_match.startswith('"')
        or not if_match.endswith('"')
        or not if_match[1:-1].isascii()
        or not if_match[1:-1].isdecimal()
        or len(if_match[1:-1]) > _MAXIMUM_VERSION_DIGITS
    ):
        raise ComposerPreconditionInvalidError
    version = int(if_match[1:-1])
    if version > _MAXIMUM_SIGNED_VERSION:
        raise ComposerPreconditionInvalidError
    return version


def _etag(version: int) -> str:
    return f'"{version}"'


def _actor(dependencies: HttpDependencies, user: User) -> ConnectionActor:
    repository = dependencies.components.composer_connection_repository
    allowed = repository.can_manage_personal(user.id) if repository else False
    return ConnectionActor(
        id=user.id,
        is_admin=user.role is Role.ADMIN,
        can_manage_personal=allowed,
    )


def _service(dependencies: HttpDependencies) -> ConnectionService:
    service = dependencies.components.composer_connections
    if service is None:
        raise ComposerUnavailableError
    return service


def _visible(
    service: ConnectionService, actor: ConnectionActor, id: UUID
) -> ConnectionRecord:
    return service.get_visible(actor, id)


def _status_message(state: ComposerAvailabilityState) -> str | None:
    return {
        ComposerAvailabilityState.UNCONFIGURED: "No model connection is configured.",
        ComposerAvailabilityState.DISABLED: "The model connection is disabled.",
        ComposerAvailabilityState.UNAUTHORIZED: "You do not have access to a model connection.",
        ComposerAvailabilityState.READY: None,
        ComposerAvailabilityState.OUTAGE: "The model provider is temporarily unavailable.",
    }[state]


def _record_response(
    dependencies: HttpDependencies,
    actor: ConnectionActor,
    record: ConnectionRecord,
) -> ConnectionResponse:
    repository = dependencies.components.composer_connection_repository
    outage_user_id = None if record.identity_mode is IdentityMode.SHARED else actor.id
    outage = repository.get_outage(record.id, outage_user_id) if repository else False
    service = dependencies.components.composer_connections
    presence = service.credential_presence(actor, record.id) if service else None
    has_api_key = presence.has_api_key if presence else record.has_api_key
    has_certificate = (
        presence.has_client_certificate if presence else record.has_client_certificate
    )
    has_ca = presence.has_ca_bundle if presence else record.has_ca_bundle
    authorized = (
        repository.can_manage_personal(actor.id)
        if repository and record.scope is ConnectionScope.PERSONAL
        else True
    )
    if not authorized:
        state = ComposerAvailabilityState.UNAUTHORIZED
    elif not record.enabled or (
        service is not None and not service.policy_allows(record)
    ):
        state = ComposerAvailabilityState.DISABLED
    elif not has_api_key and not has_certificate:
        state = ComposerAvailabilityState.UNCONFIGURED
    elif (
        record.selected_model is None
        or record.selected_model not in record.permitted_models
    ):
        state = ComposerAvailabilityState.DISABLED
    elif outage:
        state = ComposerAvailabilityState.OUTAGE
    else:
        state = ComposerAvailabilityState.READY
    return ConnectionResponse(
        id=record.id,
        name=record.name,
        scope=HttpConnectionScope(record.scope.value),
        identity_mode=HttpIdentityMode(record.identity_mode.value),
        endpoint=record.endpoint,
        enabled=record.enabled,
        selected_model=record.selected_model,
        permitted_models=record.permitted_models,
        allowed_user_ids=(
            tuple(sorted(record.allowed_user_ids, key=str)) if actor.is_admin else ()
        ),
        credential_present=has_api_key,
        client_certificate_present=has_certificate,
        internal_ca_present=has_ca,
        authorized=authorized,
        status=state,
        status_message=_status_message(state),
        etag=_etag(record.version),
    )


def _permission(
    details: tuple[UUID, str, bool, int],
) -> PersonalPermissionResponse:
    user_id, username, allowed, version = details
    return PersonalPermissionResponse(
        user_id=user_id, username=username, allowed=allowed, etag=_etag(version)
    )


def _permission_details(
    dependencies: HttpDependencies, user_id: UUID
) -> tuple[UUID, str, bool, int]:
    repository = dependencies.components.composer_connection_repository
    if repository is None:
        raise ComposerUnavailableError
    details = repository.get_personal_permission_details(user_id)
    if details is None:
        raise ConnectionNotFoundError
    return details


def _require_admin(user: User) -> None:
    if user.role is not Role.ADMIN:
        raise ConnectionAuthorizationError


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
    """Bind connection operations to the application's auth and storage ports."""
    router = APIRouter()

    @router.get(
        "/api/v1/composer/capabilities",
        response_model=ComposerCapabilitiesResponse,
        tags=["composer"],
        responses=error_responses(401, 503),
    )
    def capabilities(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerCapabilitiesResponse:
        response.headers["Cache-Control"] = "private, no-store"
        actor = _actor(dependencies, user)
        service = dependencies.components.composer_connections
        if service is None:
            state = ComposerAvailabilityState.UNCONFIGURED
        else:
            state = ComposerAvailabilityState(service.availability(actor).state.value)
        return ComposerCapabilitiesResponse(
            status=state,
            status_message=_status_message(state),
            personal_connections_allowed=actor.can_manage_personal,
            instance_connections_manageable=actor.is_admin,
            maximum_upload_bytes=(
                dependencies.settings.composer_upload_max_bytes
                if service is not None
                else None
            ),
            maximum_credential_bytes=(
                dependencies.settings.composer_maximum_credential_bytes
                if service is not None
                else None
            ),
            maximum_model_request_bytes=(
                dependencies.settings.composer_maximum_request_bytes
                if service is not None
                else None
            ),
            maximum_output_tokens=(
                dependencies.settings.composer_maximum_output_tokens
                if service is not None
                else None
            ),
        )

    @router.get(
        "/api/v1/composer/connections",
        response_model=ConnectionListResponse,
        tags=["composer"],
        responses=error_responses(401, 503),
    )
    def list_connections(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ConnectionListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        service = dependencies.components.composer_connections
        if service is None:
            return ConnectionListResponse(connections=(), limit=limit, offset=offset)
        actor = _actor(dependencies, user)
        return ConnectionListResponse(
            connections=tuple(
                _record_response(dependencies, actor, record)
                for record in service.list_visible(actor, limit=limit, offset=offset)
            ),
            limit=limit,
            offset=offset,
        )

    @router.post(
        "/api/v1/composer/connections",
        response_model=ConnectionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    def create_connection(
        payload: ConnectionCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ConnectionResponse:
        actor = _actor(dependencies, user)
        service = _service(dependencies)
        record = service.save(
            actor,
            ConnectionRecord(
                id=uuid4(),
                scope=ConnectionScope(payload.scope.value),
                owner_id=user.id if payload.scope.value == "personal" else None,
                identity_mode=IdentityMode(payload.identity_mode.value),
                endpoint=payload.endpoint,
                selected_model=payload.selected_model,
                permitted_models=payload.permitted_models,
                enabled=payload.enabled,
                allowed_user_ids=frozenset(payload.allowed_user_ids),
                version=0,
                generation=0,
                name=payload.name,
            ),
            expected_version=None,
        )
        response.headers["ETag"] = _etag(record.version)
        response.headers["Location"] = f"/api/v1/composer/connections/{record.id}"
        response.headers["Cache-Control"] = "private, no-store"
        return _record_response(dependencies, actor, record)

    @router.get(
        "/api/v1/composer/connections/{connection_id}",
        response_model=ConnectionResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_connection(
        connection_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ConnectionResponse:
        actor = _actor(dependencies, user)
        record = _visible(_service(dependencies), actor, connection_id)
        response.headers["ETag"] = _etag(record.version)
        response.headers["Cache-Control"] = "private, no-store"
        return _record_response(dependencies, actor, record)

    @router.patch(
        "/api/v1/composer/connections/{connection_id}",
        response_model=ConnectionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    def update_connection(
        connection_id: UUID,
        payload: ConnectionUpdateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ConnectionResponse:
        actor = _actor(dependencies, user)
        service = _service(dependencies)
        current = _visible(service, actor, connection_id)
        version = _version(if_match)
        updates = payload.model_dump(exclude_unset=True)
        if "allowed_user_ids" in updates and updates["allowed_user_ids"] is not None:
            updates["allowed_user_ids"] = frozenset(updates["allowed_user_ids"])
        record = service.save(
            actor, replace(current, **updates), expected_version=version
        )
        response.headers["ETag"] = _etag(record.version)
        response.headers["Cache-Control"] = "private, no-store"
        return _record_response(dependencies, actor, record)

    @router.put(
        "/api/v1/composer/connections/{connection_id}/credentials",
        response_model=ConnectionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    def write_credentials(
        connection_id: UUID,
        payload: CredentialWriteRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ConnectionResponse:
        actor = _actor(dependencies, user)
        service = _service(dependencies)
        current = _visible(service, actor, connection_id)
        credential_user_id = (
            None if current.identity_mode is IdentityMode.SHARED else user.id
        )
        version = _version(if_match)
        if payload.revoke:
            record = service.revoke_credentials(
                actor,
                connection_id,
                expected_version=version,
                credential_user_id=credential_user_id,
            )
        else:
            record = service.set_credentials(
                actor,
                connection_id,
                PlainCredentials(
                    api_key=_secret_bytes(payload.api_key),
                    client_certificate=_secret_bytes(payload.client_certificate),
                    client_private_key=_secret_bytes(payload.client_private_key),
                    ca_bundle=_secret_bytes(payload.internal_ca),
                ),
                expected_version=version,
                credential_user_id=credential_user_id,
            )
        response.headers["ETag"] = _etag(record.version)
        response.headers["Cache-Control"] = "private, no-store"
        return _record_response(dependencies, actor, record)

    @router.delete(
        "/api/v1/composer/connections/{connection_id}",
        response_model=ConnectionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def revoke_connection(
        connection_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ConnectionResponse:
        actor = _actor(dependencies, user)
        record = _service(dependencies).revoke(
            actor, connection_id, expected_version=_version(if_match)
        )
        response.headers["ETag"] = _etag(record.version)
        response.headers["Cache-Control"] = "private, no-store"
        return _record_response(dependencies, actor, record)

    @router.get(
        "/api/v1/composer/connections/{connection_id}/models",
        response_model=ConnectionModelsResponse,
        tags=["composer"],
        responses=capacity_error_responses(401, 403, 404, 422, 502, 503),
    )
    def discover_models(
        connection_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ConnectionModelsResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ConnectionModelsResponse(
            models=_service(dependencies).discover_models(
                _actor(dependencies, user), connection_id
            )
        )

    @router.post(
        "/api/v1/composer/connections/{connection_id}/test",
        response_model=ConnectionTestResponse,
        tags=["composer"],
        responses=capacity_error_responses(401, 403, 404, 422, 502, 503),
    )
    def test_connection(
        connection_id: UUID,
        payload: ConnectionTestRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ConnectionTestResponse:
        response.headers["Cache-Control"] = "private, no-store"
        _service(dependencies).test(
            _actor(dependencies, user), connection_id, model=payload.model
        )
        return ConnectionTestResponse(
            status=ComposerAvailabilityState.READY, status_message=None
        )

    @router.get(
        "/api/v1/composer/personal-permissions",
        response_model=PersonalPermissionListResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 503),
    )
    def list_personal_permissions(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> PersonalPermissionListResponse:
        _require_admin(user)
        response.headers["Cache-Control"] = "private, no-store"
        repository = dependencies.components.composer_connection_repository
        if repository is None:
            raise ComposerUnavailableError
        return PersonalPermissionListResponse(
            permissions=tuple(
                _permission(details)
                for details in repository.list_personal_permissions(
                    limit=limit, offset=offset
                )
            ),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/personal-permissions/{user_id}",
        response_model=PersonalPermissionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 503),
    )
    def get_personal_permission(
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> PersonalPermissionResponse:
        _require_admin(user)
        permission = _permission(_permission_details(dependencies, user_id))
        response.headers["ETag"] = permission.etag
        response.headers["Cache-Control"] = "private, no-store"
        return permission

    @router.put(
        "/api/v1/composer/personal-permissions/{user_id}",
        response_model=PersonalPermissionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def set_personal_permission(
        user_id: UUID,
        payload: PersonalPermissionUpdateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> PersonalPermissionResponse:
        _require_admin(user)
        details = _permission_details(dependencies, user_id)
        repository = dependencies.components.composer_connection_repository
        if repository is None:
            raise ComposerUnavailableError
        version = repository.set_personal_permission(
            user_id,
            payload.allowed,
            expected_version=_version(if_match),
            actor_id=user.id,
        )
        permission = PersonalPermissionResponse(
            user_id=user_id,
            username=details[1],
            allowed=payload.allowed,
            etag=_etag(version),
        )
        response.headers["ETag"] = permission.etag
        response.headers["Cache-Control"] = "private, no-store"
        return permission

    return router


def _secret_bytes(value: SecretStr | None) -> bytes | None:
    if value is None:
        return None
    return value.get_secret_value().encode("utf-8")
