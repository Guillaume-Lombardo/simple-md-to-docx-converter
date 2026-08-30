"""Conversion submission, lifecycle, and result routes."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool

from markweave.auth.models import Role, User
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.responses import conversion_response
from markweave.http.schemas import ConversionPageResponse, ConversionResponse
from markweave.jobs.errors import JobRequestError
from markweave.jobs.models import (
    JobOutput,
    JobPage,
    JobRequest,
    source_kind_for_filename,
)
from markweave.observability import CORRELATION_HEADER, CORRELATION_STATE_KEY
from markweave.version import VERSION

COMPONENT_VERSIONS = (
    ("chromium", "151.0.7922.173"),
    ("libreoffice", "26.2.5.2"),
    ("md-converter", VERSION),
    ("mermaid-cli", "11.16.0"),
    ("pandoc", "3.10.2"),
)


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Build conversion routes bound to one application."""

    router = APIRouter()
    settings = dependencies.settings
    components = dependencies.components

    @router.post(
        "/api/v1/conversions",
        response_model=ConversionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["conversions"],
        responses=error_responses(401, 403, 409, 413, 422, 429, 503),
    )
    async def create_conversion(  # noqa: PLR0913, PLR0917 - FastAPI fields
        request: Request,
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        source: Annotated[UploadFile, File()],
        output: Annotated[JobOutput, Form()],
        template_id: Annotated[UUID | None, Form()] = None,
        template_version_id: Annotated[UUID | None, Form()] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ConversionResponse:
        correlation_id = getattr(request.state, CORRELATION_STATE_KEY)
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
            content = await source.read(settings.conversion_upload_max_bytes + 1)
        finally:
            await source.close()
        if not content or len(content) > settings.conversion_upload_max_bytes:
            raise JobRequestError
        await run_in_threadpool(components.scanner.scan, content)
        try:
            job, _replayed = await run_in_threadpool(
                components.jobs.submit,
                JobRequest(
                    owner_id=actor.id,
                    source=content,
                    template_id=template_id,
                    template_version_id=template_version_id,
                    output=output,
                    component_versions=COMPONENT_VERSIONS,
                    now=datetime.now(UTC),
                    correlation_id=correlation_id,
                    source_filename=source_filename,
                    source_kind=source_kind,
                ),
                idempotency_key,
            )
        except ValueError:
            raise JobRequestError from None
        response.headers["Location"] = f"/api/v1/conversions/{job.id}"
        response.headers[CORRELATION_HEADER] = job.correlation_id
        response.headers["Retry-After"] = str(settings.conversion_retry_after_seconds)
        return conversion_response(job)

    @router.get(
        "/api/v1/conversions",
        response_model=ConversionPageResponse,
        tags=["conversions"],
        responses=error_responses(401, 422, 503),
    )
    def list_conversions(
        actor: Annotated[User, Depends(dependencies.current_user)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> ConversionPageResponse:
        page: JobPage = components.jobs.list_owner(actor.id, offset=offset, limit=limit)
        return ConversionPageResponse(
            items=tuple(conversion_response(job) for job in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    @router.get(
        "/api/v1/conversions/{job_id}",
        response_model=ConversionResponse,
        tags=["conversions"],
        responses=error_responses(401, 404, 422, 503),
    )
    def get_conversion(
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> ConversionResponse:
        return conversion_response(
            components.jobs.get_visible(
                job_id,
                actor_id=actor.id,
                actor_is_admin=actor.role is Role.ADMIN,
            )
        )

    @router.delete(
        "/api/v1/conversions/{job_id}",
        response_model=ConversionResponse,
        tags=["conversions"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def cancel_conversion(
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ConversionResponse:
        return conversion_response(
            components.jobs.cancel(
                job_id,
                actor_id=actor.id,
                actor_is_admin=actor.role is Role.ADMIN,
                now=datetime.now(UTC),
            )
        )

    @router.get(
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
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        job, content = components.jobs.download(
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

    @router.get(
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
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        job, content = components.jobs.download_manifest(
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

    return router
