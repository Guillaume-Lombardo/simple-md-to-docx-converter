"""Template catalog, version, download, and preference routes."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.http.responses import (
    expected_revision,
    template_etag,
    template_response,
)
from markweave.http.schemas import (
    TemplateAdministrationContextResponse,
    TemplateMetadataRequest,
    TemplatePageResponse,
    TemplateResponse,
    TemplateVersionResponse,
)
from markweave.templates.errors import (
    TemplateRequestError,
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from markweave.templates.models import (
    TemplateCreate,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
)


def _expected_fonts_from_form(values: list[str]) -> tuple[str, ...]:
    """Decode the explicit multipart sentinel used to clear a declaration."""

    if values == [""]:
        return ()
    return tuple(values)


def build_router(  # noqa: PLR0915 - route declarations are intentionally grouped
    dependencies: HttpDependencies,
) -> APIRouter:
    """Build template routes bound to one application."""

    router = APIRouter()
    settings = dependencies.settings
    components = dependencies.components
    auth = dependencies.authentication

    @router.get(
        "/api/v1/template-context",
        response_model=TemplateAdministrationContextResponse,
        tags=["templates"],
        responses=error_responses(401, 503),
    )
    def get_template_administration_context(
        response: Response,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> TemplateAdministrationContextResponse:
        response.headers["Cache-Control"] = "no-store"
        preferred_id, fallback_id = dependencies.template_runtime().selection_context(
            actor
        )
        return TemplateAdministrationContextResponse(
            preferred_template_id=preferred_id,
            system_fallback_template_id=fallback_id,
            template_max_archive_bytes=settings.template_max_archive_bytes,
        )

    @router.get(
        "/api/v1/templates",
        response_model=TemplatePageResponse,
        tags=["templates"],
        responses=error_responses(401, 422, 503),
    )
    def list_templates(  # noqa: PLR0913, PLR0917 - explicit query contract
        actor: Annotated[User, Depends(dependencies.current_user)],
        name: str | None = None,
        description: str | None = None,
        owner_id: UUID | None = None,
        template_status: Annotated[TemplateStatus | None, Query(alias="status")] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> TemplatePageResponse:
        page: TemplatePage = dependencies.template_runtime().search(
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
            items=tuple(template_response(item, auth) for item in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    @router.post(
        "/api/v1/templates",
        response_model=TemplateResponse,
        status_code=201,
        tags=["templates"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    async def create_template(  # noqa: PLR0913, PLR0917 - explicit multipart contract
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        name: Annotated[str, Form()],
        description: Annotated[str, Form()],
        expected_fonts: Annotated[list[str], Form()],
        content: Annotated[UploadFile, File()],
    ) -> TemplateResponse:
        try:
            data = await content.read(settings.template_max_archive_bytes + 1)
        finally:
            await content.close()
        if len(data) > settings.template_max_archive_bytes:
            raise TemplateValidationError(
                code=TemplateValidationErrorCode.LIMIT_EXCEEDED,
                message="Word template exceeds configured limits.",
            )
        if not data:
            raise TemplateValidationError(
                code=TemplateValidationErrorCode.INVALID_PACKAGE,
                message="Word template package is invalid.",
            )
        await run_in_threadpool(components.scanner.scan, data)
        if (
            len(name) > settings.template_max_name_characters
            or len(description) > settings.template_max_description_characters
        ):
            raise TemplateRequestError
        try:
            template, _version = await run_in_threadpool(
                dependencies.template_runtime().create_versioned,
                actor,
                TemplateCreate(uuid4(), name, description),
                data,
                _expected_fonts_from_form(expected_fonts),
            )
        except ValueError:
            raise TemplateRequestError from None
        response.headers["ETag"] = template_etag(template)
        response.headers["Location"] = f"/api/v1/templates/{template.id}"
        return template_response(template, auth)

    @router.get(
        "/api/v1/templates/{template_id}",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 404, 422, 503),
    )
    def get_template(
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> TemplateResponse:
        template = dependencies.template_runtime().get_visible(actor, template_id)
        response.headers["ETag"] = template_etag(template)
        return template_response(template, auth)

    @router.patch(
        "/api/v1/templates/{template_id}",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def update_template(
        template_id: UUID,
        payload: TemplateMetadataRequest,
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateResponse:
        if (
            len(payload.name) > settings.template_max_name_characters
            or len(payload.description) > settings.template_max_description_characters
        ):
            raise TemplateRequestError
        try:
            template = dependencies.template_runtime().update_metadata(
                actor,
                template_id,
                expected_revision=expected_revision(template_id, if_match),
                name=payload.name,
                description=payload.description,
            )
        except ValueError:
            raise TemplateRequestError from None
        response.headers["ETag"] = template_etag(template)
        return template_response(template, auth)

    @router.put(
        "/api/v1/templates/{template_id}/content",
        response_model=TemplateVersionResponse,
        status_code=201,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    async def replace_template(  # noqa: PLR0913, PLR0917 - explicit multipart contract
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        content: Annotated[UploadFile, File()],
        expected_fonts: Annotated[list[str], Form()],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateVersionResponse:
        try:
            data = await content.read(settings.template_max_archive_bytes + 1)
        finally:
            await content.close()
        if len(data) > settings.template_max_archive_bytes:
            raise TemplateValidationError(
                TemplateValidationErrorCode.LIMIT_EXCEEDED,
                "Word template exceeds configured limits.",
            )
        if not data:
            raise TemplateValidationError(
                TemplateValidationErrorCode.INVALID_PACKAGE,
                "Word template package is invalid.",
            )
        await run_in_threadpool(components.scanner.scan, data)
        template, version = await run_in_threadpool(
            dependencies.template_runtime().replace,
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
            content=data,
            expected_fonts=_expected_fonts_from_form(expected_fonts),
        )
        response.headers["ETag"] = template_etag(template)
        return TemplateVersionResponse.model_validate(version)

    @router.get(
        "/api/v1/templates/{template_id}/versions",
        response_model=tuple[TemplateVersionResponse, ...],
        tags=["templates"],
        responses=error_responses(401, 404, 422, 503),
    )
    def list_template_versions(
        template_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> tuple[TemplateVersionResponse, ...]:
        return tuple(
            TemplateVersionResponse.model_validate(version)
            for version in dependencies.template_runtime().list_versions(
                actor, template_id
            )
        )

    def template_download_response(
        actor: User, template_id: UUID, version_id: UUID | None
    ) -> Response:
        _template, version, data = dependencies.template_runtime().download(
            actor, template_id, version_id
        )
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="template-{template_id}-v'
                    f'{version.number}.docx"'
                ),
                "Cache-Control": "private, no-store",
                "ETag": f'"sha256-{version.sha256}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get(
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
        template_id: UUID,
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        return template_download_response(actor, template_id, None)

    @router.get(
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
        actor: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        return template_download_response(actor, template_id, version_id)

    @router.post(
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
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateVersionResponse:
        template, version = dependencies.template_runtime().restore(
            actor,
            template_id,
            version_id,
            expected_revision=expected_revision(template_id, if_match),
        )
        response.headers["ETag"] = template_etag(template)
        return TemplateVersionResponse.model_validate(version)

    @router.post(
        "/api/v1/templates/{template_id}/archive",
        response_model=TemplateResponse,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def archive_template(
        template_id: UUID,
        response: Response,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TemplateResponse:
        template = dependencies.template_runtime().archive(
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
        )
        response.headers["ETag"] = template_etag(template)
        return template_response(template, auth)

    @router.delete(
        "/api/v1/templates/{template_id}",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def delete_template(
        template_id: UUID,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> None:
        dependencies.template_runtime().delete(
            actor,
            template_id,
            expected_revision=expected_revision(template_id, if_match),
        )

    @router.put(
        "/api/v1/templates/{template_id}/preferred",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def set_preferred_template(
        template_id: UUID,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> None:
        dependencies.template_runtime().set_preferred(actor, template_id)

    @router.delete(
        "/api/v1/template-preference",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 422, 503),
    )
    def clear_preferred_template(
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> None:
        dependencies.template_runtime().clear_preferred(actor)

    @router.put(
        "/api/v1/templates/{template_id}/system-fallback",
        status_code=204,
        tags=["templates"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def set_system_fallback_template(
        template_id: UUID,
        actor: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> None:
        dependencies.template_runtime().set_system_fallback(actor, template_id)

    return router
