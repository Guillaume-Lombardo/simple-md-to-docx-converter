"""Authenticated, scanned presentation planning and default reference download."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.conversion.errors import ConversionError
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.jobs.errors import JobRequestError
from markweave.jobs.models import SourceKind, source_kind_for_filename
from markweave.presentations.models import PresentationDialect, PresentationOptions
from markweave.presentations.preparation import plan_presentation, prepare_source
from markweave.presentations.runtime import default_reference, preparation_limits


class PresentationPlanResponse(BaseModel):
    dialect: PresentationDialect
    slide_level: int
    titles: tuple[str, ...]
    warnings: tuple[str, ...]
    explicit_breaks: bool


def build_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()
    settings = dependencies.settings

    @router.post(
        "/api/v1/presentation-plan",
        response_model=PresentationPlanResponse,
        tags=["presentations"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    async def preview_presentation(
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        source: Annotated[UploadFile, File()],
        dialect: Annotated[PresentationDialect, Form()] = PresentationDialect.AUTO,
        slide_level: Annotated[int, Form(ge=1, le=6)] = 2,
    ) -> PresentationPlanResponse:
        try:
            kind = source_kind_for_filename(source.filename or "")
            content = await source.read(settings.conversion_upload_max_bytes + 1)
        except ValueError:
            raise JobRequestError from None
        finally:
            await source.close()
        if not content or len(content) > settings.conversion_upload_max_bytes:
            raise JobRequestError
        await run_in_threadpool(dependencies.components.scanner.scan, content)
        archive_limits, image_limits = preparation_limits(settings)
        try:
            document = await run_in_threadpool(
                prepare_source,
                content,
                archive=kind is SourceKind.ARCHIVE,
                archive_limits=archive_limits,
                image_limits=image_limits,
            )
            plan = await run_in_threadpool(
                plan_presentation, document, PresentationOptions(dialect, slide_level)
            )
        except ConversionError:
            raise JobRequestError from None
        return PresentationPlanResponse(
            dialect=plan.options.dialect,
            slide_level=slide_level,
            titles=plan.titles,
            warnings=plan.warnings,
            explicit_breaks=plan.explicit_breaks,
        )

    @router.get(
        "/api/v1/presentation-reference",
        tags=["presentations"],
        responses={
            200: {
                "description": "Native Pandoc PowerPoint reference",
                "content": {
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            **error_responses(401, 503),
        },
        response_class=Response,
    )
    def download_presentation_reference(
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        return Response(
            default_reference(settings),
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={
                "Content-Disposition": 'attachment; filename="reference.pptx"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
