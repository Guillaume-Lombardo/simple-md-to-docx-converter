"""Durable owner-approved Composer model steps and cancellation routes."""

import hashlib
import json
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import Role, User
from markweave.composer.author_knowledge import (
    AuthorKnowledgeConflictError,
    AuthorKnowledgeNotFoundError,
)
from markweave.composer.author_prompt import AuthorPromptSnapshot, compose_author_prompt
from markweave.composer.connections import ConnectionActor
from markweave.composer.revisions import ComposerConflictError
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerModelStepCreateRequest,
    ComposerModelStepResponse,
    ComposerQuestionAnswerRequest,
    ComposerQuestionListResponse,
    ComposerQuestionResponse,
)
from markweave.http.composer_step_runner import ModelStepInput
from markweave.http.composer_t91_schemas import (
    AuthorPromptPreviewRequest,
    AuthorPromptPreviewResponse,
    AuthorReference,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import capacity_error_responses, error_responses
from markweave.persistence.composer import ComposerModelStep
from markweave.persistence.composer.questions import ComposerQuestion

_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126


def _response(step: ComposerModelStep) -> ComposerModelStepResponse:
    return ComposerModelStepResponse(
        id=step.id,
        draft_id=step.draft_id,
        connection_id=step.connection_id,
        model_identity=step.model_identity,
        base_version=step.base_version,
        status=step.status,
        proposal_id=step.proposal_id,
        intent="question" if step.intent == "question" else "proposal",
        answered_question_id=step.answered_question_id,
        question_id=step.question_id,
        error_code=step.error_code,
        created_at=step.created_at,
        updated_at=step.updated_at,
    )


def _question_response(question: ComposerQuestion) -> ComposerQuestionResponse:
    return ComposerQuestionResponse.model_validate(asdict(question))


def _require_headers(
    if_match: str | None, idempotency_key: str | None
) -> tuple[str, str]:
    if if_match is None or idempotency_key is None:
        raise ComposerPreconditionRequiredError
    if (
        not idempotency_key
        or len(idempotency_key) > _MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS
        or any(
            not _FIRST_VISIBLE_ASCII <= ord(character) <= _LAST_VISIBLE_ASCII
            for character in idempotency_key
        )
    ):
        raise ComposerRequestError
    return if_match, idempotency_key


def _payload_digest(payload: ComposerModelStepCreateRequest, limit: int) -> str:
    try:
        encoded = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except UnicodeEncodeError:
        raise ComposerRequestError from None
    if len(encoded) > limit:
        raise ComposerRequestError
    return hashlib.sha256(encoded).hexdigest()


def _actor(dependencies: HttpDependencies, user: User) -> ConnectionActor:
    permissions = dependencies.components.composer_connection_repository
    return ConnectionActor(
        id=user.id,
        is_admin=user.role is Role.ADMIN,
        can_manage_personal=(
            permissions.can_manage_personal(user.id) if permissions else False
        ),
    )


def _preview_authors(  # noqa: PLR0913, PLR0917 - exact preview authorization inputs
    dependencies: HttpDependencies,
    user: User,
    draft_id: UUID,
    content: str,
    author_ids: tuple[UUID, ...],
    if_match: str,
) -> tuple[str, str, tuple[tuple[UUID, int], ...]]:
    store = dependencies.components.composer_store
    authors = dependencies.components.composer_authors
    if store is None or authors is None:
        raise ComposerUnavailableError
    draft = store.get_draft(user.id, draft_id)
    if draft.etag != if_match:
        raise ComposerConflictError("Composer draft changed")
    if len(set(author_ids)) != len(author_ids):
        raise ComposerRequestError("An author was selected more than once")
    try:
        records = tuple(authors.get(user.id, item) for item in author_ids)
    except AuthorKnowledgeNotFoundError, AuthorKnowledgeConflictError:
        raise ComposerConflictError("Author access changed") from None
    snapshots = tuple(
        AuthorPromptSnapshot(
            id=record.id,
            version=record.version,
            name=record.name,
            fields={name: asdict(field) for name, field in record.fields.items()},
        )
        for record in records
    )
    transmitted, digest = compose_author_prompt(content, snapshots)
    return transmitted, digest, tuple((item.id, item.version) for item in snapshots)


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
    """Expose explicit model-step transmission and owner-scoped cancellation."""

    router = APIRouter()

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/model-steps/preview",
        response_model=AuthorPromptPreviewResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def preview_model_step(
        draft_id: UUID,
        payload: AuthorPromptPreviewRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> AuthorPromptPreviewResponse:
        if if_match is None:
            raise ComposerPreconditionRequiredError
        connections = dependencies.components.composer_connections
        if connections is None:
            raise ComposerUnavailableError
        record = connections.get_visible(
            _actor(dependencies, user), payload.connection_id
        )
        if (
            not record.enabled
            or record.endpoint != payload.approved_endpoint
            or record.selected_model != payload.approved_model
            or payload.approved_model not in record.permitted_models
        ):
            raise ComposerConflictError("Connection changed before preview")
        transmitted, digest, refs = _preview_authors(
            dependencies,
            user,
            draft_id,
            payload.content,
            payload.author_ids,
            if_match,
        )
        limit = dependencies.settings.composer_maximum_request_bytes
        if limit is None or len(transmitted.encode("utf-8")) > limit:
            raise ComposerRequestError("Model prompt exceeds the configured limit")
        response.headers["Cache-Control"] = "private, no-store"
        return AuthorPromptPreviewResponse(
            transmitted_content=transmitted,
            author_refs=tuple(
                AuthorReference(id=ref[0], version=ref[1]) for ref in refs
            ),
            preview_digest=digest,
        )

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/model-steps",
        response_model=ComposerModelStepResponse,
        status_code=202,
        tags=["composer"],
        responses=capacity_error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def start_model_step(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        payload: ComposerModelStepCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerModelStepResponse:
        runner = dependencies.components.composer_steps
        request_limit = dependencies.settings.composer_maximum_request_bytes
        output_limit = dependencies.settings.composer_maximum_output_tokens
        if runner is None or request_limit is None or output_limit is None:
            raise ComposerUnavailableError
        if payload.max_output_tokens > output_limit:
            raise ComposerRequestError
        etag, key = _require_headers(if_match, idempotency_key)
        digest = _payload_digest(payload, request_limit)
        author_refs = tuple((item.id, item.version) for item in payload.author_refs)
        content = payload.content
        if author_refs:
            content, preview_digest, current_refs = _preview_authors(
                dependencies,
                user,
                draft_id,
                payload.content,
                tuple(ref[0] for ref in author_refs),
                etag,
            )
            if (
                current_refs != author_refs
                or payload.author_preview_digest != preview_digest
            ):
                raise ComposerConflictError("Author preview changed")
        elif payload.author_preview_digest is not None:
            raise ComposerRequestError("Author preview is invalid")
        if len(content.encode("utf-8")) > request_limit:
            raise ComposerRequestError("Model prompt exceeds the configured limit")
        response.headers["Cache-Control"] = "private, no-store"
        step = runner.start(
            _actor(dependencies, user),
            draft_id,
            ModelStepInput(
                connection_id=payload.connection_id,
                if_match=etag,
                idempotency_key=key,
                approved_endpoint=payload.approved_endpoint,
                approved_model=payload.approved_model,
                content=content,
                max_output_tokens=payload.max_output_tokens,
                payload_digest=digest,
                intent=payload.intent,
                answered_question_id=payload.answered_question_id,
                author_refs=author_refs,
                author_preview_digest=payload.author_preview_digest,
            ),
        )
        return _response(step)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/questions",
        response_model=ComposerQuestionListResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 422, 503),
    )
    def list_questions(  # noqa: PLR0913, PLR0917 - explicit FastAPI page fields
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
        order: Literal["asc", "desc"] = "asc",
    ) -> ComposerQuestionListResponse:
        store = dependencies.components.composer_model_step_repository
        if store is None:
            raise ComposerUnavailableError
        response.headers["Cache-Control"] = "private, no-store"
        questions = store.list_questions(
            user.id, draft_id, limit=limit, offset=offset, order=order
        )
        return ComposerQuestionListResponse(
            questions=tuple(_question_response(item) for item in questions),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/questions/{question_id}",
        response_model=ComposerQuestionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 503),
    )
    def get_question(
        draft_id: UUID,
        question_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerQuestionResponse:
        store = dependencies.components.composer_model_step_repository
        if store is None:
            raise ComposerUnavailableError
        response.headers["Cache-Control"] = "private, no-store"
        return _question_response(store.get_question(user.id, draft_id, question_id))

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/questions/{question_id}/answer",
        response_model=ComposerQuestionResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def answer_question(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        question_id: UUID,
        payload: ComposerQuestionAnswerRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerQuestionResponse:
        store = dependencies.components.composer_model_step_repository
        if store is None:
            raise ComposerUnavailableError
        limit = dependencies.settings.composer_maximum_request_bytes
        if limit is None or len(payload.content.encode("utf-8")) > limit:
            raise ComposerRequestError
        etag, key = _require_headers(if_match, idempotency_key)
        question, new_etag = store.answer_question(
            user.id,
            draft_id,
            question_id,
            content=payload.content,
            if_match=etag,
            idempotency_key=key,
        )
        response.headers["ETag"] = new_etag
        response.headers["Cache-Control"] = "private, no-store"
        return _question_response(question)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/model-steps/{step_id}",
        response_model=ComposerModelStepResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 503),
    )
    def get_model_step(
        draft_id: UUID,
        step_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerModelStepResponse:
        store = dependencies.components.composer_model_step_repository
        if store is None:
            raise ComposerUnavailableError
        response.headers["Cache-Control"] = "private, no-store"
        return _response(store.get_model_step(user.id, draft_id, step_id))

    @router.delete(
        "/api/v1/composer/drafts/{draft_id}/model-steps/{step_id}",
        response_model=ComposerModelStepResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 503),
    )
    def cancel_model_step(
        draft_id: UUID,
        step_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ComposerModelStepResponse:
        runner = dependencies.components.composer_steps
        store = dependencies.components.composer_model_step_repository
        if store is None:
            raise ComposerUnavailableError
        response.headers["Cache-Control"] = "private, no-store"
        step = (
            runner.cancel(user.id, draft_id, step_id)
            if runner is not None
            else store.cancel_model_step(user.id, draft_id, step_id)
        )
        return _response(step)

    return router
