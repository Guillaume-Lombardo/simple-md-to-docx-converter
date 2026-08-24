"""FastAPI application factory and versioned HTTP contract."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import boto3
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from md_converter.auth.errors import LOGIN_ORIGIN_INVALID, AuthenticationError
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
from md_converter.config import Settings, StorageProfile
from md_converter.jobs.errors import (
    JobConflictError,
    JobNotFoundError,
    JobRepositoryError,
    JobRequestError,
)
from md_converter.jobs.models import ConversionJob, JobOutput, JobPage, JobRequest
from md_converter.jobs.service import JobService, JobServicePolicy
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectStore,
    ObjectStoreError,
    S3ObjectStore,
)

COMPONENT_VERSIONS = (
    ("chromium", "151.0.7922.173"),
    ("libreoffice", "26.2.5.2"),
    ("md-converter", "0.1.0"),
    ("mermaid-cli", "11.16.0"),
    ("pandoc", "3.10.2"),
)


class BoundedConversionBody:
    """Bound conversion request bytes before multipart parsing or spooling."""

    def __init__(self, app: ASGIApp, *, maximum_bytes: int) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            scope["method"] == "POST" and scope["path"] == "/api/v1/conversions"
        ):
            await self._app(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self._maximum_bytes:
                await self._reject(send)
                return
            body.extend(chunk)
            more_body = bool(message.get("more_body", False))
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self._app(scope, replay, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        content = (
            b'{"error":{"code":"CONVERSION_REQUEST_TOO_LARGE",'
            b'"message":"The conversion request is too large."}}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(content)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": content})


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


class ConversionResponse(BaseModel):
    """Safe persistent conversion snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    template_id: UUID
    template_version_id: UUID
    output: JobOutput
    component_versions: tuple[tuple[str, str], ...]
    state: str
    step: str
    progress: int
    created_at: datetime
    updated_at: datetime
    attempt: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    expires_at: datetime | None


class ConversionPageResponse(BaseModel):
    """Paginated owner conversion response."""

    items: tuple[ConversionResponse, ...]
    total: int
    offset: int
    limit: int


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
    404: "The requested resource was not found",
    409: "The request conflicts with current state",
    413: "The request body is too large",
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

    @app.exception_handler(PersistenceError)
    def persistence_error_handler(
        _request: Request, _error: PersistenceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "PERSISTENCE_UNAVAILABLE",
                    "message": "Persistent storage is unavailable.",
                }
            },
        )

    @app.exception_handler(JobNotFoundError)
    def job_not_found_handler(
        _request: Request, _error: JobNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "error": {
                    "code": "CONVERSION_NOT_FOUND",
                    "message": "The conversion was not found.",
                }
            },
        )

    @app.exception_handler(JobConflictError)
    def job_conflict_handler(
        _request: Request, _error: JobConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "CONVERSION_CONFLICT",
                    "message": "The conversion request conflicts with current state.",
                }
            },
        )

    @app.exception_handler(JobRequestError)
    def job_request_handler(_request: Request, _error: JobRequestError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "CONVERSION_REQUEST_INVALID",
                    "message": "The conversion request is invalid.",
                }
            },
        )

    @app.exception_handler(JobRepositoryError)
    @app.exception_handler(ObjectStoreError)
    def job_storage_error_handler(
        _request: Request, _error: JobRepositoryError | ObjectStoreError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "CONVERSION_STORAGE_UNAVAILABLE",
                    "message": "Conversion storage is unavailable.",
                }
            },
        )


@dataclass(frozen=True, slots=True)
class AppComponents:
    """Application ports assembled independently of FastAPI."""

    authentication: AuthenticationService
    readiness: ReadinessProbe
    object_store: ObjectStore
    jobs: JobService


class ProfileReadinessProbe:
    """Cheap readiness composition for metadata and object persistence."""

    def __init__(self, database: ReadinessProbe, objects: ObjectStore) -> None:
        self._database = database
        self._objects = objects

    def is_ready(self) -> bool:
        return self._database.is_ready() and self._objects.is_ready()


def build_components(settings: Settings) -> AppComponents:
    """Assemble the selected coherent persistent storage profile."""
    if settings.storage_profile is StorageProfile.STANDALONE:
        data_directory = settings.standalone_data_directory
        if (
            data_directory is None
        ):  # validated by Settings; defensive for type narrowing
            raise RuntimeError("Validated standalone settings are incomplete")
        database_url = standalone_database_url(data_directory)
        object_store: ObjectStore = FilesystemObjectStore(data_directory)
    else:
        database_secret = settings.distributed_database_url
        bucket = settings.s3_bucket
        if database_secret is None or bucket is None:
            raise RuntimeError("Validated distributed settings are incomplete")
        database_url = database_secret.get_secret_value()
        client_options: dict[str, Any] = {}
        if settings.s3_endpoint_url is not None:
            client_options["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_region is not None:
            client_options["region_name"] = settings.s3_region
        if settings.s3_access_key_id is not None:
            client_options["aws_access_key_id"] = (
                settings.s3_access_key_id.get_secret_value()
            )
            client_options["aws_secret_access_key"] = (
                settings.s3_secret_access_key.get_secret_value()
                if settings.s3_secret_access_key is not None
                else ""
            )
        object_store = S3ObjectStore(boto3.client("s3", **client_options), bucket)

    engine = create_database_engine(database_url)
    upgrade_database(engine)
    users = SqlUserRepository(engine)
    sessions = SqlSessionRepository(engine)
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
    jobs = JobService(
        SqlJobRepository(engine),
        object_store,
        JobServicePolicy(settings.job_result_retention_seconds),
    )
    return AppComponents(
        authentication=authentication,
        readiness=ProfileReadinessProbe(DatabaseReadinessProbe(engine), object_store),
        object_store=object_store,
        jobs=jobs,
    )


def create_app(  # noqa: PLR0915 - the factory keeps route-local security dependencies
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
    app.add_middleware(
        BoundedConversionBody,
        maximum_bytes=resolved_settings.conversion_request_max_bytes,
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

    def conversion_response(job: ConversionJob) -> ConversionResponse:
        return ConversionResponse.model_validate(job)

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

    @app.post(
        "/api/v1/conversions",
        response_model=ConversionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["conversions"],
        responses=error_responses(401, 403, 409, 413, 422, 503),
    )
    async def create_conversion(  # noqa: PLR0913, PLR0917 - FastAPI fields
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        source: Annotated[UploadFile, File()],
        template_id: Annotated[UUID, Form()],
        template_version_id: Annotated[UUID, Form()],
        output: Annotated[JobOutput, Form()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ConversionResponse:
        content = await source.read(resolved_settings.conversion_upload_max_bytes + 1)
        if not content or len(content) > resolved_settings.conversion_upload_max_bytes:
            raise JobRequestError
        try:
            job, _replayed = resolved_components.jobs.submit(
                JobRequest(
                    owner_id=actor.id,
                    source=content,
                    template_id=template_id,
                    template_version_id=template_version_id,
                    output=output,
                    component_versions=COMPONENT_VERSIONS,
                    now=datetime.now(UTC),
                ),
                idempotency_key,
            )
        except ValueError:
            raise JobRequestError from None
        response.headers["Location"] = f"/api/v1/conversions/{job.id}"
        response.headers["Retry-After"] = str(
            resolved_settings.conversion_retry_after_seconds
        )
        return conversion_response(job)

    @app.get(
        "/api/v1/conversions",
        response_model=ConversionPageResponse,
        tags=["conversions"],
        responses=error_responses(401, 422, 503),
    )
    def list_conversions(
        actor: Annotated[User, Depends(current_user)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> ConversionPageResponse:
        page: JobPage = resolved_components.jobs.list_owner(
            actor.id, offset=offset, limit=limit
        )
        return ConversionPageResponse(
            items=tuple(conversion_response(job) for job in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    @app.get(
        "/api/v1/conversions/{job_id}",
        response_model=ConversionResponse,
        tags=["conversions"],
        responses=error_responses(401, 404, 422, 503),
    )
    def get_conversion(
        job_id: UUID, actor: Annotated[User, Depends(current_user)]
    ) -> ConversionResponse:
        return conversion_response(
            resolved_components.jobs.get_visible(
                job_id,
                actor_id=actor.id,
                actor_is_admin=actor.role is Role.ADMIN,
            )
        )

    @app.delete(
        "/api/v1/conversions/{job_id}",
        response_model=ConversionResponse,
        tags=["conversions"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def cancel_conversion(
        job_id: UUID, actor: Annotated[User, Depends(mutation_actor)]
    ) -> ConversionResponse:
        return conversion_response(
            resolved_components.jobs.cancel(
                job_id,
                actor_id=actor.id,
                actor_is_admin=actor.role is Role.ADMIN,
                now=datetime.now(UTC),
            )
        )

    @app.get(
        "/api/v1/conversions/{job_id}/result",
        response_class=Response,
        tags=["conversions"],
        responses={
            200: {
                "description": "Immutable conversion result",
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            **error_responses(401, 404, 409, 422, 503),
        },
    )
    def download_conversion(
        job_id: UUID, actor: Annotated[User, Depends(current_user)]
    ) -> Response:
        job, content = resolved_components.jobs.download(
            job_id,
            actor_id=actor.id,
            actor_is_admin=actor.role is Role.ADMIN,
        )
        extensions = {
            JobOutput.DOCX: "docx",
            JobOutput.PDF: "pdf",
            JobOutput.BOTH: "zip",
        }
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="conversion-{job.id}.'
                    f'{extensions[job.output]}"'
                )
            },
        )

    return app
