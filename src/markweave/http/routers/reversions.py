"""Reverse-conversion capabilities, submission, lifecycle, and results."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.responses import reversion_response
from markweave.http.schemas import (
    ReversionCapabilitiesResponse,
    ReversionPageResponse,
    ReversionResponse,
)
from markweave.observability import CORRELATION_HEADER, CORRELATION_STATE_KEY
from markweave.reversion_jobs.errors import (
    ReversionJobRequestError,
    ReversionServiceUnavailableError,
)
from markweave.reversion_jobs.models import ANYDOC_COMPONENT, ReversionRequest
from markweave.reversion_jobs.service import ReversionService
from markweave.reversions.capabilities import build_reversion_capabilities
from markweave.reversions.detection import admit_reverse_source
from markweave.reversions.models import ReverseOutputMode

_RESULT_MEDIA_TYPES = {
    ReverseOutputMode.MARKDOWN: "text/markdown; charset=utf-8",
    ReverseOutputMode.MARKDOWN_WITH_ASSETS: "application/zip",
    ReverseOutputMode.MARKDOWN_WITH_UNAVAILABLE_ASSETS: "application/zip",
}


def _result_content_disposition(source_stem: str, mode: ReverseOutputMode) -> str:
    extension = "md" if mode is ReverseOutputMode.MARKDOWN else "zip"
    filename = f"{source_stem}.{extension}"
    encoded = quote(filename, safe="")
    if encoded != filename:
        return f"attachment; filename*=UTF-8''{encoded}"
    return f'attachment; filename="{filename}"'


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Build reverse-conversion routes bound to one application."""

    router = APIRouter()

    @router.get(
        "/api/v1/reversions/capabilities",
        response_model=ReversionCapabilitiesResponse,
        tags=["reversions"],
        responses=error_responses(401, 503),
    )
    def get_reversion_capabilities(
        response: Response,
        _actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> ReversionCapabilitiesResponse:
        capabilities = build_reversion_capabilities(
            dependencies.settings.reversion_upload_max_bytes
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        policy = capabilities.admission
        return ReversionCapabilitiesResponse(
            schema_version=capabilities.schema_version,
            format_families=tuple(
                {
                    "family": approved.family,
                    "extensions": approved.extensions,
                    "detected_formats": approved.detected_formats,
                    "content_detection": approved.content_detection,
                    "selected_parser_format": approved.selected_parser_format,
                }
                for approved in policy.formats
            ),
            admission={
                "extension_is_hint": policy.extension_is_hint,
                "mismatch_policy": policy.mismatch_policy,
                "undetected_policy": policy.undetected_policy,
                "csv_policy": policy.csv_policy,
                "scanner_order": policy.scanner_order,
            },
            maximum_upload_bytes=capabilities.maximum_upload_bytes,
            result_package_modes=capabilities.result_package_modes,
            pdf={
                "contract": capabilities.pdf.contract,
                "document_model_available": (capabilities.pdf.document_model_available),
                "embedded_assets_available": (
                    capabilities.pdf.embedded_assets_available
                ),
                "image_preservation": capabilities.pdf.image_preservation,
                "mixed_or_image_only_pages": (
                    capabilities.pdf.mixed_or_image_only_pages
                ),
                "warning": capabilities.pdf.warning,
            },
            execution={
                "local": capabilities.execution.local,
                "ocr": capabilities.execution.ocr,
                "hosted_fallback": capabilities.execution.hosted_fallback,
            },
        )

    @router.post(
        "/api/v1/reversions",
        response_model=ReversionResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["reversions"],
        responses={
            202: {
                "description": "Reverse conversion accepted or idempotently replayed",
                "headers": {
                    "Location": {"schema": {"type": "string"}},
                    "Retry-After": {"schema": {"type": "string"}},
                    "Cache-Control": {"schema": {"type": "string"}},
                    "X-Content-Type-Options": {"schema": {"type": "string"}},
                },
            },
            **error_responses(401, 403, 409, 413, 422, 429, 503),
        },
    )
    async def create_reversion(  # noqa: PLR0913, PLR0917 - FastAPI fields
        request: Request,
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        runtime: Annotated[ReversionService, Depends(dependencies.reversion_runtime)],
        source: Annotated[UploadFile, File()],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ReversionResponse:
        maximum = dependencies.settings.reversion_upload_max_bytes
        retry_after = dependencies.settings.reversion_retry_after_seconds
        if maximum is None or retry_after is None:
            await source.close()
            raise ReversionServiceUnavailableError
        if source.filename is None:
            await source.close()
            raise ReversionJobRequestError
        filename = source.filename
        try:
            content = await source.read(maximum + 1)
        finally:
            await source.close()
        if not content or len(content) > maximum:
            raise ReversionJobRequestError
        await run_in_threadpool(dependencies.components.scanner.scan, content)
        admitted = await run_in_threadpool(admit_reverse_source, filename, content)
        try:
            job, _replayed = await run_in_threadpool(
                runtime.submit,
                ReversionRequest(
                    owner_id=actor.id,
                    source_stem=admitted.source_stem,
                    admission=admitted.admission,
                    component_versions=(ANYDOC_COMPONENT,),
                    now=datetime.now(UTC),
                    source=content,
                    correlation_id=getattr(request.state, CORRELATION_STATE_KEY),
                ),
                idempotency_key,
            )
        except ValueError:
            raise ReversionJobRequestError from None
        response.headers["Location"] = f"/api/v1/reversions/{job.id}"
        response.headers[CORRELATION_HEADER] = job.correlation_id
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return reversion_response(job)

    @router.get(
        "/api/v1/reversions",
        response_model=ReversionPageResponse,
        tags=["reversions"],
        responses=error_responses(401, 422, 503),
    )
    def list_reversions(
        actor: Annotated[User, Depends(dependencies.current_user)],
        runtime: Annotated[ReversionService, Depends(dependencies.reversion_runtime)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> ReversionPageResponse:
        page = runtime.list_owner(actor.id, offset=offset, limit=limit)
        return ReversionPageResponse(
            items=tuple(reversion_response(job) for job in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    @router.get(
        "/api/v1/reversions/{job_id}",
        response_model=ReversionResponse,
        tags=["reversions"],
        responses=error_responses(401, 404, 422, 503),
    )
    def get_reversion(
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
        runtime: Annotated[ReversionService, Depends(dependencies.reversion_runtime)],
    ) -> ReversionResponse:
        return reversion_response(runtime.get(job_id, actor.id))

    @router.delete(
        "/api/v1/reversions/{job_id}",
        response_model=ReversionResponse,
        tags=["reversions"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def cancel_reversion(
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        runtime: Annotated[ReversionService, Depends(dependencies.reversion_runtime)],
    ) -> ReversionResponse:
        return reversion_response(
            runtime.cancel(job_id, actor.id, now=datetime.now(UTC))
        )

    @router.get(
        "/api/v1/reversions/{job_id}/result",
        response_class=Response,
        tags=["reversions"],
        responses={
            200: {
                "description": "Immutable reverse-conversion result",
                "headers": {
                    "Content-Disposition": {"schema": {"type": "string"}},
                    "Cache-Control": {"schema": {"type": "string"}},
                    "X-Content-Type-Options": {"schema": {"type": "string"}},
                },
                "content": {
                    "text/markdown": {"schema": {"type": "string", "format": "binary"}},
                    "application/zip": {
                        "schema": {"type": "string", "format": "binary"}
                    },
                },
            },
            **error_responses(401, 404, 409, 422, 503),
        },
    )
    def download_reversion(
        job_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
        runtime: Annotated[ReversionService, Depends(dependencies.reversion_runtime)],
    ) -> Response:
        job, content = runtime.download(job_id, actor.id)
        if job.result_mode is None:
            raise ReversionJobRequestError
        return Response(
            content,
            media_type=_RESULT_MEDIA_TYPES[job.result_mode],
            headers={
                "Content-Disposition": _result_content_disposition(
                    job.source_stem, job.result_mode
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
