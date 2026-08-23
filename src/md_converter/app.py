"""FastAPI application factory and T06 HTTP contract."""

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Form, Header, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from md_converter.auth.errors import LOGIN_ORIGIN_INVALID, AuthenticationError
from md_converter.auth.memory import (
    MemoryReadinessProbe,
    MemorySessionRepository,
    MemoryUserRepository,
)
from md_converter.auth.models import Role, User
from md_converter.auth.ports import ReadinessProbe
from md_converter.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
)
from md_converter.auth.service import (
    AuthenticationService,
    AuthorizationService,
    SecurityRuntime,
    SessionPolicy,
)
from md_converter.config import Settings


class LoginRequest(BaseModel):
    """JSON local-login request."""

    username: str
    password: str


class UserCreateRequest(BaseModel):
    """Administrator local-account creation request."""

    username: str
    password: str


class ActiveUpdateRequest(BaseModel):
    """Administrator account status request."""

    active: bool


class PasswordResetRequest(BaseModel):
    """Administrator password reset request."""

    password: str


class UserResponse(BaseModel):
    """Public local-account representation without password material."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: Role
    active: bool


class LoginResponse(BaseModel):
    """Successful login response containing the session-bound CSRF token once."""

    user: UserResponse
    csrf_token: str = Field(
        description="Send as X-CSRF-Token for authenticated mutations."
    )


class ErrorDetail(BaseModel):
    """Stable machine-readable functional error detail."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Stable error envelope used for every expected HTTP failure."""

    error: ErrorDetail


ERROR_DESCRIPTIONS = {
    401: "Authentication failed or is required",
    403: "The operation is forbidden",
    404: "The account was not found",
    409: "The username conflicts with an existing account",
    422: "The request is invalid",
    503: "The service is not ready",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Build explicit OpenAPI entries for stable error envelopes."""
    return {
        status_code: {
            "model": ErrorResponse,
            "description": ERROR_DESCRIPTIONS[status_code],
        }
        for status_code in status_codes
    }


def install_error_handlers(app: FastAPI) -> None:
    """Install sanitized functional and request-validation error handlers."""

    @app.exception_handler(AuthenticationError)
    def authentication_error_handler(
        _request: Request, error: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
        )

    @app.exception_handler(RequestValidationError)
    def request_validation_error_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "REQUEST_INVALID",
                    "message": "The request is invalid.",
                }
            },
        )


@dataclass(frozen=True, slots=True)
class AppComponents:
    """Application ports assembled independently of FastAPI."""

    authentication: AuthenticationService
    readiness: ReadinessProbe


def build_components(settings: Settings) -> AppComponents:
    """Assemble temporary T06 adapters behind durable ports."""
    users = MemoryUserRepository()
    sessions = MemorySessionRepository()
    hasher = Argon2idPasswordHasher(
        memory_cost=settings.argon2_memory_cost,
        time_cost=settings.argon2_time_cost,
        parallelism=settings.argon2_parallelism,
    )
    authentication = AuthenticationService(
        users=users,
        sessions=sessions,
        security=SecurityRuntime(
            hasher=hasher,
            tokens=SecretsTokenGenerator(settings.session_token_bytes),
            clock=SystemClock(),
        ),
        policy=SessionPolicy(
            idle_seconds=settings.session_idle_seconds,
            absolute_seconds=settings.session_absolute_seconds,
        ),
    )
    return AppComponents(
        authentication=authentication,
        readiness=MemoryReadinessProbe(),
    )


def create_app(
    settings: Settings | None = None,
    *,
    components: AppComponents | None = None,
) -> FastAPI:
    """Create a configured application or fail before serving requests."""
    resolved_settings = settings if settings is not None else Settings.load()
    resolved_components = components or build_components(resolved_settings)
    auth = resolved_components.authentication
    auth.bootstrap_admin(
        resolved_settings.initial_admin_username,
        resolved_settings.initial_admin_password.get_secret_value(),
    )

    app = FastAPI(
        title="Markdown Converter API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.components = resolved_components
    install_error_handlers(app)

    def session_token(request: Request) -> str | None:
        return request.cookies.get(resolved_settings.session_cookie_name)

    def current_user(request: Request) -> User:
        return auth.authenticate(session_token(request))

    def enforce_login_origin(request: Request) -> None:
        origin = request.headers.get("Origin")
        if origin is None:
            return
        expected = str(request.base_url).rstrip("/").casefold()
        if origin.rstrip("/").casefold() != expected:
            raise LOGIN_ORIGIN_INVALID.new()

    def mutation_actor(
        request: Request,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> User:
        token = session_token(request)
        user = auth.authenticate(token)
        auth.validate_csrf(token, csrf_token)
        return user

    def admin_actor(user: Annotated[User, Depends(mutation_actor)]) -> User:
        AuthorizationService.require_admin(user)
        return user

    def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
        AuthorizationService.require_admin(user)
        return user

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            resolved_settings.session_cookie_name,
            token,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
            max_age=resolved_settings.session_absolute_seconds,
        )

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            resolved_settings.session_cookie_name,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        responses=error_responses(503),
    )
    def ready() -> Response:
        if resolved_components.readiness.is_ready():
            return JSONResponse({"status": "ready"})
        return JSONResponse(
            {"error": {"code": "NOT_READY", "message": "The service is not ready."}},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page() -> str:
        return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sign in</title></head>
<body><main><h1>Sign in</h1>
<form method="post" action="/login">
<label>Username <input name="username" autocomplete="username" required></label>
<label>Password <input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Sign in</button>
</form></main></body></html>"""

    @app.post("/login", include_in_schema=False)
    def browser_login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
        _origin: Annotated[None, Depends(enforce_login_origin)],
    ) -> Response:
        try:
            result = auth.login(
                username,
                password,
                previous_session_token=session_token(request),
            )
        except AuthenticationError:
            return HTMLResponse(
                '<!doctype html><html lang="en"><body><main>'
                '<h1>Sign in</h1><p role="alert">The username or password is incorrect.</p>'
                "</main></body></html>",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        response = RedirectResponse("/docs", status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookie(response, result.session_token)
        return response

    @app.post(
        "/api/v1/login",
        response_model=LoginResponse,
        tags=["authentication"],
        responses=error_responses(401, 403, 422),
    )
    def api_login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        _origin: Annotated[None, Depends(enforce_login_origin)],
    ) -> LoginResponse:
        result = auth.login(
            payload.username,
            payload.password,
            previous_session_token=session_token(request),
        )
        set_session_cookie(response, result.session_token)
        return LoginResponse(
            user=UserResponse.model_validate(result.user),
            csrf_token=result.csrf_token,
        )

    @app.post(
        "/api/v1/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["authentication"],
        responses=error_responses(401, 403, 422),
    )
    def api_logout(
        request: Request,
        response: Response,
        _actor: Annotated[User, Depends(mutation_actor)],
    ) -> None:
        auth.logout(session_token(request))
        clear_session_cookie(response)

    @app.get(
        "/api/v1/session",
        response_model=UserResponse,
        tags=["authentication"],
        responses=error_responses(401),
    )
    def api_session(user: Annotated[User, Depends(current_user)]) -> UserResponse:
        return UserResponse.model_validate(user)

    @app.get(
        "/api/v1/admin/users",
        response_model=list[UserResponse],
        tags=["administration"],
        responses=error_responses(401, 403),
    )
    def list_users(actor: Annotated[User, Depends(admin_user)]) -> list[UserResponse]:
        return [UserResponse.model_validate(user) for user in auth.list_users(actor)]

    @app.post(
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
            auth.create_user(actor, payload.username, payload.password)
        )

    @app.patch(
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

    @app.post(
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
        auth.reset_password(actor, user_id, payload.password)

    return app
