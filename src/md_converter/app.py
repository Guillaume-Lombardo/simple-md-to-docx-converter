"""FastAPI application factory and versioned HTTP contract."""

from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Annotated, Any
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
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
from sqlalchemy import Engine
from starlette.concurrency import run_in_threadpool
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
    JobQueueCapacityExceededError,
    JobRepositoryError,
    JobRequestError,
    JobUserQuotaExceededError,
)
from md_converter.jobs.models import (
    ConversionJob,
    JobOutput,
    JobPage,
    JobRequest,
    source_kind_for_filename,
)
from md_converter.jobs.ports import JobRepository
from md_converter.jobs.runner import EmbeddedWorker, ExternalWorkerRuntime, WorkerLoop
from md_converter.jobs.runtime import JobPolicies, build_job_policies
from md_converter.jobs.service import JobService
from md_converter.jobs.worker import ConversionWorker
from md_converter.malware import (
    ClamAVUploadScanner,
    MalwareDetectedError,
    MalwareScannerUnavailableError,
    TrustingUploadScanner,
    UploadScanner,
)
from md_converter.observability import (
    CORRELATION_HEADER,
    CORRELATION_STATE_KEY,
    AuditReader,
    AuditRecord,
    CorrelationMiddleware,
    MetricsHttpServer,
    OperationalMetrics,
    QueueObserver,
    QueueSnapshot,
    log_event,
)
from md_converter.persistence.errors import PersistenceError
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.observability import (
    SqlAuditReader,
    SqlOperationalObserver,
)
from md_converter.persistence.retention import SqlRetentionRepository
from md_converter.persistence.sql import (
    DatabaseReadinessProbe,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.retention import DataRetentionPolicy, RetentionService
from md_converter.storage import (
    FilesystemObjectStore,
    ObjectStore,
    ObjectStoreError,
    S3ObjectStore,
)
from md_converter.templates.errors import (
    TemplateConflictError,
    TemplateIntegrityError,
    TemplatePreconditionRequiredError,
    TemplateRequestError,
    TemplateStorageError,
    TemplateUnavailableError,
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from md_converter.templates.models import (
    TemplateCreate,
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
)
from md_converter.templates.processor import (
    TemplateAwareProcessor,
    build_template_conversion_worker,
)
from md_converter.templates.runtime import build_template_validator
from md_converter.templates.service import TemplateRecoveryPolicy, TemplateService
from md_converter.web import (
    WEB_SECURITY_HEADERS,
    render_conversion_page,
    render_login_page,
    render_templates_page,
)

COMPONENT_VERSIONS = (
    ("chromium", "151.0.7922.173"),
    ("libreoffice", "26.2.5.2"),
    ("md-converter", "0.1.0"),
    ("mermaid-cli", "11.16.0"),
    ("pandoc", "3.10.2"),
)
STATIC_DIRECTORY = Path(__file__).with_name("static")
CSRF_COOKIE_NAME = "__Host-md_converter_csrf"


class BoundedRequestBody:
    """Bound upload request bytes before multipart parsing or spooling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        conversion_maximum_bytes: int,
        template_maximum_bytes: int,
        template_metadata_maximum_bytes: int,
    ) -> None:
        self._app = app
        self._conversion_maximum_bytes = conversion_maximum_bytes
        self._template_maximum_bytes = template_maximum_bytes
        self._template_metadata_maximum_bytes = template_metadata_maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        template_upload = (method == "POST" and path == "/api/v1/templates") or (
            method == "PUT"
            and path.startswith("/api/v1/templates/")
            and path.endswith("/content")
        )
        if method == "POST" and path == "/api/v1/conversions":
            maximum_bytes = self._conversion_maximum_bytes
            error_code = "CONVERSION_REQUEST_TOO_LARGE"
            error_message = "The conversion request is too large."
        elif template_upload:
            maximum_bytes = self._template_maximum_bytes
            error_code = "TEMPLATE_REQUEST_TOO_LARGE"
            error_message = "The template request is too large."
        elif method == "PATCH" and path.startswith("/api/v1/templates/"):
            maximum_bytes = self._template_metadata_maximum_bytes
            error_code = "TEMPLATE_REQUEST_TOO_LARGE"
            error_message = "The template request is too large."
        else:
            await self._app(scope, receive, send)
            return
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > maximum_bytes:
                await self._reject(send, error_code, error_message)
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
    async def _reject(send: Send, code: str, message: str) -> None:
        content = f'{{"error":{{"code":"{code}","message":"{message}"}}}}'.encode()
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
        description=(
            "Send as X-CSRF-Token for authenticated mutations. Browser clients also "
            "receive the same value in the Secure, SameSite=Lax "
            "__Host-md_converter_csrf cookie."
        )
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
    correlation_id: str
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


class TemplateResponse(BaseModel):
    """Visible template identity and optimistic-concurrency revision."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str
    status: TemplateStatus
    revision: int
    current_version_id: UUID | None
    owner_username: str


class TemplatePageResponse(BaseModel):
    items: tuple[TemplateResponse, ...]
    total: int
    offset: int
    limit: int


class TemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    number: int
    sha256: str
    size: int
    created_at: datetime
    created_by: UUID
    restored_from_version_id: UUID | None
    declared_fonts: tuple[str, ...]
    resolved_fonts: tuple[tuple[str, str], ...]
    validation_trace: tuple[str, ...]


class AuditRecordResponse(BaseModel):
    """Administrator-visible content-free immutable audit evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID
    owner_id: UUID
    operation: str
    target_id: UUID
    target_type: str
    target_version: str | None
    version_id: UUID | None
    administrator_intervention: bool
    created_at: datetime


class TemplateMetadataRequest(BaseModel):
    name: str
    description: str


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
    412: "A request precondition failed",
    413: "The request body is too large",
    422: "The request is invalid",
    428: "The request requires a precondition",
    429: "The caller has exceeded a configured quota",
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


def document_correlation_headers(app: FastAPI) -> None:
    """Declare the middleware-generated correlation header on every response."""

    schema = app.openapi()
    header = {
        "description": "Server-generated request correlation identifier.",
        "schema": {"type": "string", "format": "uuid"},
    }
    for path in schema["paths"].values():
        for operation_name, operation in path.items():
            if operation_name not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            }:
                continue
            for response in operation["responses"].values():
                response.setdefault("headers", {})[CORRELATION_HEADER] = header


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

    @app.exception_handler(MalwareDetectedError)
    def malware_detected_handler(
        _request: Request, _error: MalwareDetectedError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": {
                    "code": "UPLOAD_MALWARE_DETECTED",
                    "message": "The upload was rejected by malware scanning.",
                }
            },
        )

    @app.exception_handler(MalwareScannerUnavailableError)
    def malware_scanner_unavailable_handler(
        _request: Request, _error: MalwareScannerUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": {
                    "code": "UPLOAD_SCANNER_UNAVAILABLE",
                    "message": "Upload malware scanning is unavailable.",
                }
            },
        )

    @app.exception_handler(JobUserQuotaExceededError)
    def job_user_quota_handler(
        request: Request, _error: JobUserQuotaExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("owner")
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(request.app.state.conversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "CONVERSION_USER_QUOTA_EXCEEDED",
                    "message": "The active conversion quota is exhausted.",
                }
            },
        )

    @app.exception_handler(JobQueueCapacityExceededError)
    def job_queue_capacity_handler(
        request: Request, _error: JobQueueCapacityExceededError
    ) -> JSONResponse:
        request.app.state.components.metrics.record_saturation("global")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={
                "Retry-After": str(request.app.state.conversion_retry_after_seconds)
            },
            content={
                "error": {
                    "code": "CONVERSION_QUEUE_CAPACITY_EXCEEDED",
                    "message": "The conversion queue is at capacity.",
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

    @app.exception_handler(TemplateUnavailableError)
    def template_unavailable_handler(
        _request: Request, _error: TemplateUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TEMPLATE_NOT_FOUND",
                    "message": "The template was not found.",
                }
            },
        )

    @app.exception_handler(TemplatePreconditionRequiredError)
    def template_precondition_handler(
        _request: Request, _error: TemplatePreconditionRequiredError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=428,
            content={
                "error": {
                    "code": "TEMPLATE_PRECONDITION_REQUIRED",
                    "message": "If-Match is required.",
                }
            },
        )

    @app.exception_handler(TemplateConflictError)
    def template_conflict_handler(
        _request: Request, _error: TemplateConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=412,
            content={
                "error": {
                    "code": "TEMPLATE_PRECONDITION_FAILED",
                    "message": "The template has changed or the operation is not allowed.",
                }
            },
        )

    @app.exception_handler(TemplateValidationError)
    def template_validation_handler(
        _request: Request, error: TemplateValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=(
                413 if error.code is TemplateValidationErrorCode.LIMIT_EXCEEDED else 422
            ),
            content={
                "error": {
                    "code": f"TEMPLATE_{error.code.value.upper()}",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(TemplateIntegrityError)
    def template_integrity_handler(
        _request: Request, _error: TemplateIntegrityError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "TEMPLATE_INTEGRITY_FAILURE",
                    "message": "Template content integrity verification failed.",
                }
            },
        )

    @app.exception_handler(TemplateStorageError)
    def template_storage_handler(
        _request: Request, _error: TemplateStorageError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "TEMPLATE_STORAGE_UNAVAILABLE",
                    "message": "Template storage is unavailable.",
                }
            },
        )

    @app.exception_handler(TemplateRequestError)
    def template_request_handler(
        _request: Request, _error: TemplateRequestError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "TEMPLATE_REQUEST_INVALID",
                    "message": "The template request is invalid.",
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
    scanner: UploadScanner = field(default_factory=TrustingUploadScanner)
    templates: TemplateService | None = None
    job_policies: JobPolicies | None = None
    retention: RetentionService | None = None
    job_repository: JobRepository | None = None
    metrics: OperationalMetrics = field(default_factory=OperationalMetrics)
    queue_observer: QueueObserver | None = None
    audit_reader: AuditReader | None = None
    worker_metrics_bind_host: str = "127.0.0.1"
    worker_metrics_port: int = 9464
    worker_metrics_max_connections: int = 4
    worker_metrics_observation_limit: int = 2
    worker_metrics_accept_queue_size: int = 8
    worker_metrics_request_timeout_seconds: float = 2.0
    owned_engines: tuple[Engine, ...] = field(default=(), repr=False, compare=False)
    _close_lock: Lock = field(
        default_factory=Lock, init=False, repr=False, compare=False
    )
    _closed: Event = field(default_factory=Event, init=False, repr=False, compare=False)

    def close(self) -> None:
        """Cancel observations and dispose only application-owned SQL engines."""

        if not self.owned_engines:
            return
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        with ExitStack() as cleanup:
            for engine in self.owned_engines:
                cleanup.callback(engine.dispose)
            if self.queue_observer is not None:
                self.queue_observer.cancel_observations(
                    timeout_seconds=self.worker_metrics_request_timeout_seconds
                )

    def build_conversion_worker(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> ConversionWorker:
        """Assemble the production worker with all persistent maintenance."""

        if (
            self.templates is None
            or self.job_policies is None
            or self.retention is None
            or self.job_repository is None
        ):
            raise RuntimeError("Production worker components are incomplete")
        return build_template_conversion_worker(
            worker_id=worker_id,
            repository=self.job_repository,
            objects=self.object_store,
            resolver=self.templates,
            processor=processor,
            clock=clock,
            policy=self.job_policies.worker,
            maintenance=self.retention,
            monotonic_clock=monotonic_clock,
            metrics=self.metrics,
        )

    def build_external_worker_loop(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> WorkerLoop:
        """Assemble the shared production loop for an external worker process."""

        if self.job_policies is None:
            raise RuntimeError("Production worker policies are unavailable")
        worker = self.build_conversion_worker(
            worker_id=worker_id,
            processor=processor,
            clock=clock,
            monotonic_clock=monotonic_clock,
        )
        return WorkerLoop(
            worker,
            self.job_policies.schedule,
            monotonic_clock=monotonic_clock,
            metrics=self.metrics,
        )

    def build_embedded_worker(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        thread_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> EmbeddedWorker:
        """Assemble the standalone lifecycle from the same production loop."""

        return EmbeddedWorker(
            self.build_external_worker_loop(
                worker_id=worker_id,
                processor=processor,
                clock=clock,
                monotonic_clock=monotonic_clock,
            ),
            thread_name=thread_name,
        )

    def build_external_worker_runtime(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> ExternalWorkerRuntime:
        """Assemble the external loop with a process-local scrape listener."""

        if self.queue_observer is None:
            raise RuntimeError("External worker queue observation is unavailable")
        return ExternalWorkerRuntime(
            self.build_external_worker_loop(
                worker_id=worker_id,
                processor=processor,
                clock=clock,
                monotonic_clock=monotonic_clock,
            ),
            MetricsHttpServer(
                self.metrics,
                self.queue_observer,
                host=self.worker_metrics_bind_host,
                port=self.worker_metrics_port,
                max_connections=self.worker_metrics_max_connections,
                observation_limit=self.worker_metrics_observation_limit,
                accept_queue_size=self.worker_metrics_accept_queue_size,
                request_timeout_seconds=self.worker_metrics_request_timeout_seconds,
            ),
        )


class ProfileReadinessProbe:
    """Cheap readiness composition for metadata and object persistence."""

    def __init__(self, database: ReadinessProbe, objects: ReadinessProbe) -> None:
        self._database = database
        self._objects = objects

    def is_ready(self) -> bool:
        return self._database.is_ready() and self._objects.is_ready()


def build_components(settings: Settings) -> AppComponents:
    """Assemble the selected coherent persistent storage profile."""
    job_policies = build_job_policies(settings)
    if settings.storage_profile is StorageProfile.STANDALONE:
        data_directory = settings.standalone_data_directory
        if (
            data_directory is None
        ):  # validated by Settings; defensive for type narrowing
            raise RuntimeError("Validated standalone settings are incomplete")
        database_url = standalone_database_url(data_directory)
        object_store: ObjectStore = FilesystemObjectStore(data_directory)
        object_readiness: ReadinessProbe = FilesystemObjectStore(data_directory)
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
        readiness_client_options = {
            **client_options,
            "config": Config(
                connect_timeout=settings.readiness_timeout_seconds,
                read_timeout=settings.readiness_timeout_seconds,
                retries={"max_attempts": 0},
            ),
        }
        object_readiness = S3ObjectStore(
            boto3.client("s3", **readiness_client_options), bucket
        )

    with ExitStack() as pending_engines:
        engine = create_database_engine(database_url)
        pending_engines.callback(engine.dispose)
        upgrade_database(engine)
        readiness_engine = create_database_engine(
            database_url,
            timeout_seconds=settings.readiness_timeout_seconds,
            pool_pre_ping=False,
        )
        pending_engines.callback(readiness_engine.dispose)
        observation_engine = create_database_engine(
            database_url,
            timeout_seconds=settings.worker_metrics_request_timeout_seconds,
            pool_pre_ping=False,
        )
        pending_engines.callback(observation_engine.dispose)
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
        job_repository = SqlJobRepository(engine, job_policies.admission)
        jobs = JobService(job_repository, object_store, job_policies.service)
        templates = TemplateService(
            catalog=SqlTemplateCatalogRepository(engine),
            selections=SqlTemplateSelectionRepository(engine),
            objects=object_store,
            validate_content=build_template_validator(settings),
            recovery_policy=TemplateRecoveryPolicy(
                settings.template_pending_publication_stale_seconds
            ),
        )
        templates.reclaim_pending()
        retention = RetentionService(
            SqlRetentionRepository(engine),
            object_store,
            DataRetentionPolicy(
                template_version_seconds=settings.template_version_retention_seconds,
                audit_seconds=settings.audit_retention_seconds,
                minimum_template_versions=settings.template_min_retained_versions,
                claim_lease_seconds=settings.worker_lease_seconds,
            ),
        )
        metrics = OperationalMetrics()
        components = AppComponents(
            authentication=authentication,
            readiness=ProfileReadinessProbe(
                DatabaseReadinessProbe(readiness_engine), object_readiness
            ),
            object_store=object_store,
            jobs=jobs,
            scanner=ClamAVUploadScanner(
                settings.clamav_host,
                settings.clamav_port,
                settings.clamav_timeout_seconds,
            ),
            templates=templates,
            job_policies=job_policies,
            retention=retention,
            job_repository=job_repository,
            metrics=metrics,
            queue_observer=SqlOperationalObserver(
                observation_engine,
                default_timeout_seconds=(
                    settings.worker_metrics_request_timeout_seconds
                ),
            ),
            audit_reader=SqlAuditReader(engine),
            worker_metrics_bind_host=settings.worker_metrics_bind_host,
            worker_metrics_port=settings.worker_metrics_port,
            worker_metrics_max_connections=settings.worker_metrics_max_connections,
            worker_metrics_observation_limit=settings.worker_metrics_observation_limit,
            worker_metrics_accept_queue_size=settings.worker_metrics_accept_queue_size,
            worker_metrics_request_timeout_seconds=(
                settings.worker_metrics_request_timeout_seconds
            ),
            owned_engines=(engine, readiness_engine, observation_engine),
        )
        pending_engines.pop_all()
        return components


def create_app(  # noqa: PLR0913, PLR0915 - explicit lifecycle and route composition
    settings: Settings | None = None,
    *,
    components: AppComponents | None = None,
    scanner: UploadScanner | None = None,
    embedded_worker: EmbeddedWorker | None = None,
    embedded_worker_stop_timeout_seconds: float = 30.0,
    manage_components: bool = False,
) -> FastAPI:
    """Create a configured application or fail before serving requests."""
    resolved_settings = settings if settings is not None else Settings.load()
    resolved_components = components or build_components(resolved_settings)
    owns_components = components is None or manage_components
    if scanner is not None:
        resolved_components = replace(resolved_components, scanner=scanner)
    auth = resolved_components.authentication
    try:
        auth.bootstrap_admin(
            resolved_settings.initial_admin_username,
            resolved_settings.initial_admin_password.get_secret_value(),
        )
    except Exception:
        if owns_components:
            resolved_components.close()
        raise

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        del _app
        worker_started = False
        try:
            if embedded_worker is not None:
                embedded_worker.start()
                worker_started = True
            yield
        finally:
            try:
                if embedded_worker is not None and worker_started:
                    embedded_worker.stop(
                        timeout_seconds=embedded_worker_stop_timeout_seconds
                    )
            finally:
                if owns_components:
                    resolved_components.close()

    app = FastAPI(
        title="Markdown Converter API",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        BoundedRequestBody,
        conversion_maximum_bytes=resolved_settings.conversion_request_max_bytes,
        template_maximum_bytes=resolved_settings.template_request_max_bytes,
        template_metadata_maximum_bytes=(
            resolved_settings.template_metadata_request_max_bytes
        ),
    )
    app.add_middleware(CorrelationMiddleware, metrics=resolved_components.metrics)
    app.state.components = resolved_components
    app.state.conversion_retry_after_seconds = (
        resolved_settings.conversion_retry_after_seconds
    )
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

    def set_csrf_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=False,
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
        response.delete_cookie(
            CSRF_COOKIE_NAME,
            httponly=False,
            secure=True,
            samesite="lax",
            path="/",
        )

    def web_response(content: str, *, status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(
            content,
            status_code=status_code,
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "no-store"},
        )

    def conversion_response(job: ConversionJob) -> ConversionResponse:
        return ConversionResponse.model_validate(job)

    def template_response(template: TemplateIdentity) -> TemplateResponse:
        user_repository = getattr(auth, "users", None)
        owner = (
            user_repository.get_by_id(template.owner_id)
            if user_repository is not None
            else None
        )
        return TemplateResponse(
            id=template.id,
            owner_id=template.owner_id,
            name=template.name,
            description=template.description,
            status=template.status,
            revision=template.revision,
            current_version_id=template.current_version_id,
            owner_username=owner.username if owner is not None else "Unknown owner",
        )

    def template_etag(template: TemplateIdentity) -> str:
        return f'"template-{template.id}-{template.revision}"'

    def expected_revision(template_id: UUID, if_match: str | None) -> int:
        if if_match is None:
            raise TemplatePreconditionRequiredError
        prefix = f'"template-{template_id}-'
        if not if_match.startswith(prefix) or not if_match.endswith('"'):
            raise TemplateConflictError
        try:
            revision = int(if_match[len(prefix) : -1])
        except ValueError:
            raise TemplateConflictError from None
        if revision <= 0:
            raise TemplateConflictError
        return revision

    def template_runtime() -> TemplateService:
        if resolved_components.templates is None:
            raise RuntimeError("Template API runtime is not configured")
        return resolved_components.templates

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        responses=error_responses(503),
    )
    def ready() -> Response:
        worker_ready = embedded_worker is None or embedded_worker.failure is None
        if worker_ready and resolved_components.readiness.is_ready():
            return JSONResponse({"status": "ready"})
        log_event("readiness_failed")
        return JSONResponse(
            {"error": {"code": "NOT_READY", "message": "The service is not ready."}},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/metrics", tags=["health"])
    def metrics() -> Response:
        observer = resolved_components.queue_observer
        queue = (
            observer.observe_queue(datetime.now(UTC))
            if observer is not None
            else QueueSnapshot(0, 0.0, 0)
        )
        return Response(
            resolved_components.metrics.render(queue),
            media_type="text/plain; version=0.0.4",
        )

    @app.get(
        "/api/v1/audit",
        response_model=tuple[AuditRecordResponse, ...],
        tags=["administration"],
        responses=error_responses(401, 403, 422, 503),
    )
    def list_audit_records(
        _actor: Annotated[User, Depends(admin_user)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> tuple[AuditRecord, ...]:
        reader = resolved_components.audit_reader
        if reader is None:
            raise PersistenceError
        return reader.list_recent(offset=offset, limit=limit)

    @app.get("/", include_in_schema=False)
    def browser_root() -> RedirectResponse:
        return RedirectResponse("/convert", status_code=303)

    @app.get("/static/conversion.css", include_in_schema=False)
    def conversion_stylesheet() -> Response:
        return Response(
            (STATIC_DIRECTORY / "conversion.css").read_bytes(),
            media_type="text/css",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @app.get("/static/conversion.js", include_in_schema=False)
    def conversion_javascript() -> Response:
        return Response(
            (STATIC_DIRECTORY / "conversion.js").read_bytes(),
            media_type="text/javascript",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @app.get("/static/administration.css", include_in_schema=False)
    def administration_stylesheet() -> Response:
        return Response(
            (STATIC_DIRECTORY / "administration.css").read_bytes(),
            media_type="text/css",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @app.get("/static/administration.js", include_in_schema=False)
    def administration_javascript() -> Response:
        return Response(
            (STATIC_DIRECTORY / "administration.js").read_bytes(),
            media_type="text/javascript",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page() -> HTMLResponse:
        return web_response(render_login_page())

    @app.get("/convert", response_class=HTMLResponse, include_in_schema=False)
    def conversion_page(request: Request) -> Response:
        try:
            actor = current_user(request)
        except AuthenticationError:
            return RedirectResponse("/login", status_code=303)
        runtime = template_runtime()
        selected = runtime.resolve(actor)
        label = (
            runtime.selection_label(actor, selected) if selected is not None else None
        )
        recent = resolved_components.jobs.list_owner(actor.id, offset=0, limit=10)
        return web_response(
            render_conversion_page(
                actor,
                selected,
                label,
                recent.items,
                maximum_upload_bytes=resolved_settings.conversion_upload_max_bytes,
            )
        )

    @app.get("/templates", response_class=HTMLResponse, include_in_schema=False)
    def templates_page(request: Request) -> Response:
        try:
            actor = current_user(request)
        except AuthenticationError:
            return RedirectResponse("/login", status_code=303)
        runtime = template_runtime()
        selected = runtime.resolve(actor)
        label = (
            runtime.selection_label(actor, selected) if selected is not None else None
        )
        return web_response(
            render_templates_page(
                actor,
                selected,
                label,
                maximum_upload_bytes=resolved_settings.template_max_archive_bytes,
            )
        )

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
            return web_response(render_login_page(invalid=True), status_code=401)
        response = RedirectResponse("/convert", status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookie(response, result.session_token)
        set_csrf_cookie(response, result.csrf_token)
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
        set_csrf_cookie(response, result.csrf_token)
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
        responses=error_responses(401, 403, 409, 413, 422, 429, 503),
    )
    async def create_conversion(  # noqa: PLR0913, PLR0917 - FastAPI fields
        request: Request,
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        source: Annotated[UploadFile, File()],
        template_id: Annotated[UUID, Form()],
        template_version_id: Annotated[UUID, Form()],
        output: Annotated[JobOutput, Form()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ConversionResponse:
        if source.filename is None:
            await source.close()
            raise JobRequestError
        source_filename = source.filename
        try:
            source_kind = source_kind_for_filename(source_filename)
        except ValueError:
            await source.close()
            raise JobRequestError from None
        try:
            content = await source.read(
                resolved_settings.conversion_upload_max_bytes + 1
            )
        finally:
            await source.close()
        if not content or len(content) > resolved_settings.conversion_upload_max_bytes:
            raise JobRequestError
        await run_in_threadpool(resolved_components.scanner.scan, content)
        try:
            job, _replayed = await run_in_threadpool(
                resolved_components.jobs.submit,
                JobRequest(
                    owner_id=actor.id,
                    source=content,
                    template_id=template_id,
                    template_version_id=template_version_id,
                    output=output,
                    component_versions=COMPONENT_VERSIONS,
                    now=datetime.now(UTC),
                    correlation_id=getattr(request.state, CORRELATION_STATE_KEY),
                    source_filename=source_filename,
                    source_kind=source_kind,
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
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/v1/conversions/{job_id}/result/manifest",
        response_class=Response,
        tags=["conversions"],
        responses={
            200: {
                "description": "Canonical PDF traceability manifest",
                "content": {"application/json": {"schema": {"type": "object"}}},
            },
            **error_responses(401, 404, 409, 422, 503),
        },
    )
    def download_conversion_manifest(
        job_id: UUID, actor: Annotated[User, Depends(current_user)]
    ) -> Response:
        job, content = resolved_components.jobs.download_manifest(
            job_id,
            actor_id=actor.id,
            actor_is_admin=actor.role is Role.ADMIN,
        )
        return Response(
            content,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="conversion-{job.id}-traceability.json"'
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/v1/templates",
        response_model=TemplatePageResponse,
        tags=["templates"],
        responses=error_responses(401, 422, 503),
    )
    def list_templates(  # noqa: PLR0913, PLR0917 - explicit query contract
        actor: Annotated[User, Depends(current_user)],
        name: str | None = None,
        description: str | None = None,
        owner_id: UUID | None = None,
        template_status: Annotated[TemplateStatus | None, Query(alias="status")] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> TemplatePageResponse:
        page: TemplatePage = template_runtime().search(
            actor,
            TemplateSearch(
                name=name,
                description=description,
                owner_id=owner_id,
                status=template_status,
                offset=offset,
                limit=limit,
            ),
        )
        return TemplatePageResponse(
            items=tuple(template_response(item) for item in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    @app.post(
        "/api/v1/templates",
        response_model=TemplateResponse,
        status_code=201,
        tags=["templates"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    async def create_template(  # noqa: PLR0913, PLR0917 - explicit multipart contract
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        name: Annotated[str, Form()],
        description: Annotated[str, Form()],
        expected_fonts: Annotated[list[str], Form()],
        content: Annotated[UploadFile, File()],
    ) -> TemplateResponse:
        try:
            data = await content.read(resolved_settings.template_max_archive_bytes + 1)
        finally:
            await content.close()
        if len(data) > resolved_settings.template_max_archive_bytes:
            raise TemplateValidationError(
                code=TemplateValidationErrorCode.LIMIT_EXCEEDED,
                message="Word template exceeds configured limits.",
            )
        if not data:
            raise TemplateValidationError(
                code=TemplateValidationErrorCode.INVALID_PACKAGE,
                message="Word template package is invalid.",
            )
        await run_in_threadpool(resolved_components.scanner.scan, data)
        if (
            len(name) > resolved_settings.template_max_name_characters
            or len(description) > resolved_settings.template_max_description_characters
        ):
            raise TemplateRequestError
        try:
            template, _version = await run_in_threadpool(
                template_runtime().create_versioned,
                actor,
                TemplateCreate(uuid4(), name, description),
                data,
                tuple(expected_fonts),
            )
        except ValueError:
            raise TemplateRequestError from None
        response.headers["ETag"] = template_etag(template)
        response.headers["Location"] = f"/api/v1/templates/{template.id}"
        return template_response(template)

    @app.get(
        "/api/v1/templates/{template_id}",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 404, 422, 503),
    )
    def get_template(
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(current_user)],
    ) -> TemplateResponse:
        template = template_runtime().get_visible(actor, template_id)
        response.headers["ETag"] = template_etag(template)
        return template_response(template)

    @app.patch(
        "/api/v1/templates/{template_id}",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def update_template(
        template_id: UUID,
        payload: TemplateMetadataRequest,
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateResponse:
        if (
            len(payload.name) > resolved_settings.template_max_name_characters
            or len(payload.description)
            > resolved_settings.template_max_description_characters
        ):
            raise TemplateRequestError
        try:
            template = template_runtime().update_metadata(
                actor,
                template_id,
                expected_revision=expected_revision(template_id, if_match),
                name=payload.name,
                description=payload.description,
            )
        except ValueError:
            raise TemplateRequestError from None
        response.headers["ETag"] = template_etag(template)
        return template_response(template)

    @app.put(
        "/api/v1/templates/{template_id}/content",
        response_model=TemplateVersionResponse,
        status_code=201,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    async def replace_template(  # noqa: PLR0913, PLR0917 - explicit multipart contract
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        content: Annotated[UploadFile, File()],
        expected_fonts: Annotated[list[str], Form()],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateVersionResponse:
        try:
            data = await content.read(resolved_settings.template_max_archive_bytes + 1)
        finally:
            await content.close()
        if len(data) > resolved_settings.template_max_archive_bytes:
            raise TemplateValidationError(
                TemplateValidationErrorCode.LIMIT_EXCEEDED,
                "Word template exceeds configured limits.",
            )
        if not data:
            raise TemplateValidationError(
                TemplateValidationErrorCode.INVALID_PACKAGE,
                "Word template package is invalid.",
            )
        await run_in_threadpool(resolved_components.scanner.scan, data)
        template, version = await run_in_threadpool(
            template_runtime().replace,
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
            content=data,
            expected_fonts=tuple(expected_fonts),
        )
        response.headers["ETag"] = template_etag(template)
        return TemplateVersionResponse.model_validate(version)

    @app.get(
        "/api/v1/templates/{template_id}/versions",
        response_model=tuple[TemplateVersionResponse, ...],
        tags=["templates"],
        responses=error_responses(401, 404, 422, 503),
    )
    def list_template_versions(
        template_id: UUID, actor: Annotated[User, Depends(current_user)]
    ) -> tuple[TemplateVersionResponse, ...]:
        return tuple(
            TemplateVersionResponse.model_validate(version)
            for version in template_runtime().list_versions(actor, template_id)
        )

    def template_download_response(
        actor: User, template_id: UUID, version_id: UUID | None
    ) -> Response:
        _template, version, data = template_runtime().download(
            actor, template_id, version_id
        )
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="template-{template_id}-v{version.number}.docx"',
                "ETag": f'"sha256-{version.sha256}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get(
        "/api/v1/templates/{template_id}/content",
        response_class=Response,
        tags=["templates"],
        responses={
            200: {
                "description": "Immutable current Word template",
                "content": {
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            **error_responses(401, 404, 422, 503),
        },
    )
    def download_current_template(
        template_id: UUID, actor: Annotated[User, Depends(current_user)]
    ) -> Response:
        return template_download_response(actor, template_id, None)

    @app.get(
        "/api/v1/templates/{template_id}/versions/{version_id}/content",
        response_class=Response,
        tags=["templates"],
        responses={
            200: {
                "description": "Immutable historical Word template",
                "content": {
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            **error_responses(401, 404, 422, 503),
        },
    )
    def download_template_version(
        template_id: UUID,
        version_id: UUID,
        actor: Annotated[User, Depends(current_user)],
    ) -> Response:
        return template_download_response(actor, template_id, version_id)

    @app.post(
        "/api/v1/templates/{template_id}/versions/{version_id}/restore",
        response_model=TemplateVersionResponse,
        status_code=201,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def restore_template_version(
        template_id: UUID,
        version_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateVersionResponse:
        template, version = template_runtime().restore(
            actor,
            template_id,
            version_id,
            expected_revision=expected_revision(template_id, if_match),
        )
        response.headers["ETag"] = template_etag(template)
        return TemplateVersionResponse.model_validate(version)

    @app.post(
        "/api/v1/templates/{template_id}/archive",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def archive_template(
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateResponse:
        template = template_runtime().archive(
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
        )
        response.headers["ETag"] = template_etag(template)
        return template_response(template)

    @app.delete(
        "/api/v1/templates/{template_id}",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def delete_template(
        template_id: UUID,
        actor: Annotated[User, Depends(mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> None:
        template_runtime().delete(
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
        )

    @app.put(
        "/api/v1/templates/{template_id}/preferred",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def set_preferred_template(
        template_id: UUID, actor: Annotated[User, Depends(mutation_actor)]
    ) -> None:
        template_runtime().set_preferred(actor, template_id)

    @app.delete(
        "/api/v1/template-preference",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 422, 503),
    )
    def clear_preferred_template(
        actor: Annotated[User, Depends(mutation_actor)],
    ) -> None:
        template_runtime().clear_preferred(actor)

    @app.put(
        "/api/v1/templates/{template_id}/system-fallback",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def set_system_fallback_template(
        template_id: UUID, actor: Annotated[User, Depends(mutation_actor)]
    ) -> None:
        template_runtime().set_system_fallback(actor, template_id)

    document_correlation_headers(app)
    return app
