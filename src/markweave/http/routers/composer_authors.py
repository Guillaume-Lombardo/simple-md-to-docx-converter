"""Private, explicitly shared Composer author directory."""

import json
from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import Role, User
from markweave.composer.author_knowledge import (
    AuthorKnowledgeConflictError,
    AuthorKnowledgeNotFoundError,
    AuthorRecord,
)
from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_t91_schemas import (
    AuthorListResponse,
    AuthorResponse,
    AuthorWriteRequest,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository


def _store(dependencies: HttpDependencies) -> SqlAuthorKnowledgeRepository:
    store = dependencies.components.composer_authors
    if store is None:
        raise ComposerUnavailableError
    return store


def _response(record: AuthorRecord, actor_id: UUID) -> AuthorResponse:
    return AuthorResponse(
        id=record.id,
        owner_id=record.owner_id,
        name=record.name,
        fields={key: asdict(value) for key, value in record.fields.items()},
        version=record.version,
        etag=record.etag,
        shared_with=record.shared_with if record.owner_id == actor_id else (),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _convert_error(error: Exception) -> Exception:
    if isinstance(error, AuthorKnowledgeNotFoundError):
        return ComposerNotFoundError("Author entry does not exist")
    if isinstance(error, AuthorKnowledgeConflictError):
        return ComposerConflictError("Author entry changed")
    if isinstance(error, ValueError):
        return ComposerRequestError("Author entry is invalid")
    return error


def _write_fields(payload: AuthorWriteRequest) -> str:
    return json.dumps(
        {key: value.model_dump(mode="json") for key, value in payload.fields.items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _etag(if_match: str | None) -> str:
    if if_match is None:
        raise ComposerPreconditionRequiredError
    return if_match


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose only SQL-authorized author records and audited named grants."""

    router = APIRouter()

    @router.get(
        "/api/v1/composer/authors",
        response_model=AuthorListResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 422, 503),
    )
    def list_authors(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> AuthorListResponse:
        records = _store(dependencies).list_visible(user.id, limit=limit, offset=offset)
        response.headers["Cache-Control"] = "private, no-store"
        return AuthorListResponse(
            authors=tuple(_response(record, user.id) for record in records),
            limit=limit,
            offset=offset,
        )

    @router.post(
        "/api/v1/composer/authors",
        response_model=AuthorResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    def create_author(
        payload: AuthorWriteRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> AuthorResponse:
        try:
            record = _store(dependencies).create(
                user.id, payload.name, _write_fields(payload)
            )
        except (
            AuthorKnowledgeNotFoundError,
            AuthorKnowledgeConflictError,
            ValueError,
        ) as error:
            raise _convert_error(error) from None
        response.headers["ETag"] = record.etag
        response.headers["Location"] = f"/api/v1/composer/authors/{record.id}"
        response.headers["Cache-Control"] = "private, no-store"
        return _response(record, user.id)

    @router.get(
        "/api/v1/composer/authors/{author_id}",
        response_model=AuthorResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 503),
    )
    def get_author(
        author_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> AuthorResponse:
        try:
            record = _store(dependencies).get(user.id, author_id)
        except AuthorKnowledgeNotFoundError as error:
            raise _convert_error(error) from None
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(record, user.id)

    @router.patch(
        "/api/v1/composer/authors/{author_id}",
        response_model=AuthorResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def update_author(
        author_id: UUID,
        payload: AuthorWriteRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> AuthorResponse:
        validator = _etag(if_match)
        try:
            record = _store(dependencies).update(
                user.id,
                author_id,
                if_match=validator,
                name=payload.name,
                fields_json=_write_fields(payload),
                is_admin=user.role is Role.ADMIN,
            )
        except (
            AuthorKnowledgeNotFoundError,
            AuthorKnowledgeConflictError,
            ValueError,
        ) as error:
            raise _convert_error(error) from None
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(record, user.id)

    @router.put(
        "/api/v1/composer/authors/{author_id}/grants/{user_id}",
        response_model=AuthorResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def grant_author(
        author_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> AuthorResponse:
        validator = _etag(if_match)
        try:
            record = _store(dependencies).grant(
                user.id,
                author_id,
                user_id,
                if_match=validator,
                is_admin=user.role is Role.ADMIN,
            )
        except (
            AuthorKnowledgeNotFoundError,
            AuthorKnowledgeConflictError,
            ValueError,
        ) as error:
            raise _convert_error(error) from None
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(record, user.id)

    @router.delete(
        "/api/v1/composer/authors/{author_id}/grants/{user_id}",
        response_model=AuthorResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def revoke_author(
        author_id: UUID,
        user_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> AuthorResponse:
        validator = _etag(if_match)
        try:
            record = _store(dependencies).revoke(
                user.id,
                author_id,
                user_id,
                if_match=validator,
                is_admin=user.role is Role.ADMIN,
            )
        except (
            AuthorKnowledgeNotFoundError,
            AuthorKnowledgeConflictError,
            ValueError,
        ) as error:
            raise _convert_error(error) from None
        response.headers["ETag"] = record.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(record, user.id)

    return router
