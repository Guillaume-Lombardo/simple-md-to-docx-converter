"""Private immutable typed DOCX filling templates, separate from style references."""

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from fastapi.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.composer.docx_fill import validate_template
from markweave.composer.typed_templates import (
    TYPED_DOCX_SCHEMA_VERSION,
    TypedDocxLimits,
    TypedTemplateError,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerRequestTooLargeError,
    ComposerUnavailableError,
)
from markweave.http.composer_t91_schemas import (
    TypedTemplateListResponse,
    TypedTemplateResponse,
    TypedTemplateVersionListResponse,
    TypedTemplateVersionResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer.typed_templates import (
    SqlTypedTemplateRepository,
    TypedTemplate,
    TypedTemplateVersion,
)

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _store(dependencies: HttpDependencies) -> SqlTypedTemplateRepository:
    store = dependencies.components.composer_fill_templates
    if store is None:
        raise ComposerUnavailableError
    return store


def typed_docx_limits(dependencies: HttpDependencies) -> TypedDocxLimits:
    """Bind typed filling to existing operator-configured archive and request bounds."""

    settings = dependencies.settings
    request_bytes = settings.composer_maximum_request_bytes
    if request_bytes is None:
        raise ComposerUnavailableError
    return TypedDocxLimits(
        max_archive_bytes=settings.template_max_archive_bytes,
        max_entries=settings.template_max_entries,
        max_member_bytes=settings.template_max_member_bytes,
        max_total_bytes=settings.template_max_total_bytes,
        max_compression_ratio=settings.template_max_compression_ratio,
        max_xml_elements=settings.template_max_xml_elements,
        max_xml_depth=settings.template_max_xml_depth,
        max_xml_attributes=settings.template_max_xml_attributes,
        max_schema_bytes=settings.template_metadata_request_max_bytes,
        max_values_bytes=request_bytes,
        max_repeat_items=settings.template_max_xml_elements,
        max_text_characters=request_bytes,
    )


def _identity(record: TypedTemplate, actor_id: UUID) -> TypedTemplateResponse:
    return TypedTemplateResponse(
        id=record.id,
        owner_id=record.owner_id,
        name=record.name,
        version=record.version,
        etag=record.etag,
        active_version_id=record.active_version_id,
        shared_with=record.shared_with if actor_id == record.owner_id else (),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _version(record: TypedTemplateVersion) -> TypedTemplateVersionResponse:
    return TypedTemplateVersionResponse(
        id=record.id,
        template_id=record.template_id,
        number=record.number,
        schema_version=record.schema_version,
        template_schema=json.loads(record.schema_json),
        schema_sha256=record.schema_sha256,
        docx_sha256=record.docx_sha256,
        size=record.size,
        created_at=record.created_at,
    )


def _if_match(header: str | None) -> str:
    if header is None:
        raise ComposerPreconditionRequiredError
    return header


def _schema(raw: str, maximum: int) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > maximum:
        raise ComposerRequestTooLargeError
    try:
        value = json.loads(raw)
    except ValueError:
        raise TypedTemplateError("invalid_schema") from None
    if type(value) is not dict:
        raise TypedTemplateError("invalid_schema")
    return value


async def _admit_upload(
    dependencies: HttpDependencies,
    file: UploadFile,
    raw_schema: str,
) -> tuple[str, bytes]:
    """Read and scan before any DOCX or schema parsing or storage reservation."""

    limits = typed_docx_limits(dependencies)
    try:
        content = await file.read(limits.max_archive_bytes + 1)
    finally:
        await file.close()
    if len(content) > limits.max_archive_bytes:
        raise ComposerRequestTooLargeError
    if not content:
        raise TypedTemplateError("invalid_package")
    await run_in_threadpool(dependencies.components.scanner.scan, content)
    schema = _schema(raw_schema, limits.max_schema_bytes)
    validated = await run_in_threadpool(validate_template, schema, content, limits)
    return validated.canonical_schema_json, content


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose exact typed versions under owner or named-grant authorization."""

    router = APIRouter()

    @router.get(
        "/api/v1/composer/fill-templates",
        response_model=TypedTemplateListResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 422, 503),
    )
    def list_templates(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> TypedTemplateListResponse:
        records = _store(dependencies).list_visible(user.id, limit=limit, offset=offset)
        response.headers["Cache-Control"] = "private, no-store"
        return TypedTemplateListResponse(
            templates=tuple(_identity(item, user.id) for item in records),
            limit=limit,
            offset=offset,
        )

    @router.post(
        "/api/v1/composer/fill-templates",
        response_model=TypedTemplateResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    async def create_template(
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        name: Annotated[str, Form()],
        schema_payload: Annotated[str, Form(alias="schema")],
        file: Annotated[UploadFile, File()],
    ) -> TypedTemplateResponse:
        schema_json, content = await _admit_upload(dependencies, file, schema_payload)
        if (
            not name.strip()
            or len(name) > dependencies.settings.template_max_name_characters
        ):
            raise ComposerRequestError("Typed template name is invalid")
        record = await run_in_threadpool(
            _store(dependencies).create,
            user.id,
            name,
            schema_json,
            content,
            schema_version=TYPED_DOCX_SCHEMA_VERSION,
        )
        response.headers["ETag"] = record.etag
        response.headers["Location"] = f"/api/v1/composer/fill-templates/{record.id}"
        response.headers["Cache-Control"] = "private, no-store"
        return _identity(record, user.id)

    @router.get(
        "/api/v1/composer/fill-templates/{template_id}",
        response_model=TypedTemplateResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_template(
        template_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> TypedTemplateResponse:
        record = _store(dependencies).get(user.id, template_id)
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _identity(record, user.id)

    @router.post(
        "/api/v1/composer/fill-templates/{template_id}/versions",
        response_model=TypedTemplateResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    async def replace_template(  # noqa: PLR0913, PLR0917 - explicit upload fields
        template_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        schema_payload: Annotated[str, Form(alias="schema")],
        file: Annotated[UploadFile, File()],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TypedTemplateResponse:
        match = _if_match(if_match)
        schema_json, content = await _admit_upload(dependencies, file, schema_payload)
        record = await run_in_threadpool(
            _store(dependencies).replace,
            user.id,
            template_id,
            if_match=match,
            schema_json=schema_json,
            docx_bytes=content,
            schema_version=TYPED_DOCX_SCHEMA_VERSION,
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _identity(record, user.id)

    @router.get(
        "/api/v1/composer/fill-templates/{template_id}/versions",
        response_model=TypedTemplateVersionListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def list_versions(
        template_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> TypedTemplateVersionListResponse:
        records = _store(dependencies).list_versions(
            user.id, template_id, limit=limit, offset=offset
        )
        response.headers["Cache-Control"] = "private, no-store"
        return TypedTemplateVersionListResponse(
            versions=tuple(_version(item) for item in records),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/fill-templates/{template_id}/versions/{version_id}",
        response_model=TypedTemplateVersionResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_version(
        template_id: UUID,
        version_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> TypedTemplateVersionResponse:
        record = _store(dependencies).get_version(user.id, template_id, version_id)
        response.headers["Cache-Control"] = "private, no-store"
        return _version(record)

    @router.get(
        "/api/v1/composer/fill-templates/{template_id}/versions/{version_id}/content",
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def download_version(
        template_id: UUID,
        version_id: UUID,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        content = _store(dependencies).download(user.id, template_id, version_id)
        return Response(
            content=content,
            media_type=_DOCX_MEDIA,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="filling-template.docx"',
            },
        )

    @router.put(
        "/api/v1/composer/fill-templates/{template_id}/grants/{user_id}",
        response_model=TypedTemplateResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def grant_template(
        template_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TypedTemplateResponse:
        record = _store(dependencies).grant(
            user.id, template_id, user_id, if_match=_if_match(if_match)
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _identity(record, user.id)

    @router.delete(
        "/api/v1/composer/fill-templates/{template_id}/grants/{user_id}",
        response_model=TypedTemplateResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def revoke_template(
        template_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TypedTemplateResponse:
        record = _store(dependencies).revoke(
            user.id, template_id, user_id, if_match=_if_match(if_match)
        )
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _identity(record, user.id)

    return router
