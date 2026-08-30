"""Health, metrics, and immutable audit routes."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse

from markweave.auth.models import User
from markweave.auth.service import AuthorizationService
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.schemas import AuditRecordResponse
from markweave.jobs.runner import EmbeddedWorker
from markweave.observability import AuditRecord, QueueSnapshot, log_event
from markweave.persistence.errors import PersistenceError


def build_router(
    dependencies: HttpDependencies, embedded_worker: EmbeddedWorker | None
) -> APIRouter:
    """Build audit and operational routes bound to one application."""

    router = APIRouter()
    components = dependencies.components

    def admin_user(
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> User:
        AuthorizationService.require_admin(user)
        return user

    @router.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get(
        "/health/ready",
        tags=["health"],
        responses=error_responses(503),
    )
    def ready() -> Response:
        worker_ready = embedded_worker is None or embedded_worker.failure is None
        if worker_ready and components.readiness.is_ready():
            return JSONResponse({"status": "ready"})
        log_event("readiness_failed")
        return JSONResponse(
            {"error": {"code": "NOT_READY", "message": "The service is not ready."}},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @router.get("/metrics", tags=["health"])
    def metrics() -> Response:
        observer = components.queue_observer
        queue = (
            observer.observe_queue(datetime.now(UTC))
            if observer is not None
            else QueueSnapshot(0, 0.0, 0)
        )
        return Response(
            components.metrics.render(queue),
            media_type="text/plain; version=0.0.4",
        )

    @router.get(
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
        reader = components.audit_reader
        if reader is None:
            raise PersistenceError
        return reader.list_recent(offset=offset, limit=limit)

    return router
