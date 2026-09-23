"""Administrator Composer setup inside deployment egress constraints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from markweave.auth.models import Role, User
from markweave.composer.admin_policy import AdminPolicy, ComposerAdminPolicy
from markweave.composer.connections import ConnectionAuthorizationError
from markweave.composer.egress import EgressPolicyError
from markweave.http.composer_errors import ComposerUnavailableError
from markweave.http.composer_schemas import (
    ComposerAdminPolicyResponse,
    ComposerAdminPolicyUpdateRequest,
    ComposerDestinationResolveRequest,
    ComposerDestinationResolveResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.routers.composer_connections import _version


def _admin(user: User) -> None:
    if user.role is not Role.ADMIN:
        raise ConnectionAuthorizationError("Administrator access is required")


def _policy(dependencies: HttpDependencies) -> ComposerAdminPolicy:
    policy = dependencies.components.composer_admin_policy
    if policy is None:
        raise ComposerUnavailableError
    return policy


def _response(policy: AdminPolicy) -> ComposerAdminPolicyResponse:
    return ComposerAdminPolicyResponse(
        mode=policy.mode,
        enabled=policy.enabled,
        allowed_destinations=policy.destinations,
        allowed_networks=policy.networks,
        editable_destinations=policy.mode == "delegated",
        etag=f'"{policy.version}"',
    )


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose a bounded and versioned browser setup contract to administrators."""

    router = APIRouter()

    @router.get(
        "/api/v1/admin/composer-policy",
        response_model=ComposerAdminPolicyResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 503),
    )
    def get_policy(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerAdminPolicyResponse:
        _admin(user)
        response.headers["Cache-Control"] = "private, no-store"
        return _response(_policy(dependencies).read())

    @router.put(
        "/api/v1/admin/composer-policy",
        response_model=ComposerAdminPolicyResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 412, 422, 503),
    )
    def put_policy(
        payload: ComposerAdminPolicyUpdateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ComposerAdminPolicyResponse:
        _admin(user)
        policy = _policy(dependencies)
        current = policy.read()
        if current.mode == "operator" and (
            payload.allowed_destinations != current.destinations
            or payload.allowed_networks != current.networks
        ):
            raise EgressPolicyError("Operator Composer ceiling is immutable")
        saved = policy.write(
            enabled=payload.enabled,
            destinations=payload.allowed_destinations,
            networks=payload.allowed_networks,
            expected_version=_version(if_match),
            actor_id=user.id,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["ETag"] = f'"{saved.version}"'
        return _response(saved)

    @router.post(
        "/api/v1/admin/composer-policy/resolve",
        response_model=ComposerDestinationResolveResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 422, 503),
    )
    def resolve_destination(
        payload: ComposerDestinationResolveRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ComposerDestinationResolveResponse:
        _admin(user)
        policy = _policy(dependencies)
        if policy.read().mode != "delegated":
            raise ConnectionAuthorizationError(
                "Destination approval is operator managed"
            )
        destination, addresses = policy.resolve(payload.endpoint)
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerDestinationResolveResponse(
            destination=destination, addresses=addresses
        )

    return router
