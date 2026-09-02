"""Authentication and self-service password routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from markweave.auth.models import User
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.responses import (
    clear_session_cookie,
    set_csrf_cookie,
    set_session_cookie,
)
from markweave.http.schemas import (
    LoginRequest,
    LoginResponse,
    PasswordChangeRequest,
    UserResponse,
)


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Build API authentication routes bound to one application."""

    router = APIRouter()
    auth = dependencies.authentication
    settings = dependencies.settings

    @router.post(
        "/api/v1/login",
        response_model=LoginResponse,
        tags=["authentication"],
        responses=error_responses(401, 403, 422),
    )
    def api_login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        _origin: Annotated[None, Depends(dependencies.enforce_login_origin)],
    ) -> LoginResponse:
        result = auth.login(
            payload.username,
            payload.password,
            previous_session_token=dependencies.session_token(request),
        )
        set_session_cookie(response, settings, result.session_token)
        set_csrf_cookie(response, settings, result.csrf_token)
        return LoginResponse(
            user=UserResponse(
                **UserResponse.model_validate(result.user).model_dump(),
                effective_idle_minutes=auth.effective_idle_minutes(result.user.role),
            ),
            csrf_token=result.csrf_token,
        )

    @router.post(
        "/api/v1/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["authentication"],
        responses=error_responses(401, 403, 422),
    )
    def api_logout(
        request: Request,
        response: Response,
        _actor: Annotated[User, Depends(dependencies.password_change_actor)],
    ) -> None:
        auth.logout(dependencies.session_token(request))
        clear_session_cookie(response, settings)

    @router.get(
        "/api/v1/session",
        response_model=UserResponse,
        tags=["authentication"],
        responses=error_responses(401),
    )
    def api_session(
        user: Annotated[User, Depends(dependencies.authenticated_user)],
    ) -> UserResponse:
        return UserResponse(
            **UserResponse.model_validate(user).model_dump(),
            effective_idle_minutes=auth.effective_idle_minutes(user.role),
        )

    @router.post(
        "/api/v1/password",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["authentication"],
        responses=error_responses(401, 403, 422),
    )
    def change_own_password(
        payload: PasswordChangeRequest,
        response: Response,
        actor: Annotated[User, Depends(dependencies.password_change_actor)],
    ) -> None:
        auth.change_password(actor, payload.password, payload.confirmation)
        clear_session_cookie(response, settings)

    return router
