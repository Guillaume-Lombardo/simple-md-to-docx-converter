"""Administrator local-account routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status

from markweave.auth.models import User
from markweave.auth.policy_errors import IdleSessionPolicyConflictError
from markweave.auth.service import AuthorizationService
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.responses import (
    expected_idle_session_policy_revision,
    idle_session_policy_etag,
)
from markweave.http.schemas import (
    ActiveUpdateRequest,
    IdleSessionPolicyResponse,
    IdleSessionPolicyUpdateRequest,
    PasswordChangeRequirementRequest,
    PasswordResetRequest,
    UserCreateRequest,
    UserResponse,
)


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Build administrator account routes bound to one application."""

    router = APIRouter()
    auth = dependencies.authentication

    def admin_actor(
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> User:
        AuthorizationService.require_admin(user)
        return user

    def admin_user(
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> User:
        AuthorizationService.require_admin(user)
        return user

    @router.get(
        "/api/v1/admin/users",
        response_model=list[UserResponse],
        tags=["administration"],
        responses=error_responses(401, 403),
    )
    def list_users(actor: Annotated[User, Depends(admin_user)]) -> list[UserResponse]:
        return [UserResponse.model_validate(user) for user in auth.list_users(actor)]

    @router.post(
        "/api/v1/admin/users",
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["administration"],
        responses=error_responses(401, 403, 409, 422),
    )
    def create_user(
        payload: UserCreateRequest,
        actor: Annotated[User, Depends(admin_actor)],
    ) -> UserResponse:
        return UserResponse.model_validate(
            auth.create_user(
                actor,
                payload.username,
                payload.password,
                password_change_required=payload.password_change_required,
            )
        )

    @router.patch(
        "/api/v1/admin/users/{user_id}/active",
        response_model=UserResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 404, 422),
    )
    def set_user_active(
        user_id: UUID,
        payload: ActiveUpdateRequest,
        actor: Annotated[User, Depends(admin_actor)],
    ) -> UserResponse:
        return UserResponse.model_validate(
            auth.set_active(actor, user_id, active=payload.active)
        )

    @router.post(
        "/api/v1/admin/users/{user_id}/password",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["administration"],
        responses=error_responses(401, 403, 404, 422),
    )
    def reset_user_password(
        user_id: UUID,
        payload: PasswordResetRequest,
        actor: Annotated[User, Depends(admin_actor)],
    ) -> None:
        auth.reset_password(
            actor,
            user_id,
            payload.password,
            password_change_required=payload.password_change_required,
        )

    @router.patch(
        "/api/v1/admin/users/{user_id}/password-change-required",
        response_model=UserResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 404, 422),
    )
    def set_user_password_change_required(
        user_id: UUID,
        payload: PasswordChangeRequirementRequest,
        actor: Annotated[User, Depends(admin_actor)],
    ) -> UserResponse:
        return UserResponse.model_validate(
            auth.set_password_change_required(actor, user_id, required=payload.required)
        )

    @router.get(
        "/api/v1/admin/session-policy",
        response_model=IdleSessionPolicyResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 503),
    )
    def get_session_policy(
        response: Response,
        actor: Annotated[User, Depends(admin_user)],
    ) -> IdleSessionPolicyResponse:
        policy = auth.get_idle_session_policy(actor)
        response.headers["ETag"] = idle_session_policy_etag(policy.revision)
        return IdleSessionPolicyResponse.model_validate(policy)

    @router.put(
        "/api/v1/admin/session-policy",
        response_model=IdleSessionPolicyResponse,
        tags=["administration"],
        responses=error_responses(401, 403, 412, 422, 428, 503),
    )
    def update_session_policy(
        payload: IdleSessionPolicyUpdateRequest,
        response: Response,
        actor: Annotated[User, Depends(admin_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> IdleSessionPolicyResponse:
        revision = expected_idle_session_policy_revision(if_match)
        updated = auth.update_idle_session_policy(
            actor,
            user_idle_minutes=payload.user_idle_minutes,
            admin_idle_minutes=payload.admin_idle_minutes,
            expected_revision=revision,
        )
        if updated is None:
            raise IdleSessionPolicyConflictError
        response.headers["ETag"] = idle_session_policy_etag(updated.revision)
        return IdleSessionPolicyResponse.model_validate(updated)

    return router
