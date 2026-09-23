"""Owner-scoped Composer draft and conversation foundations."""

from hashlib import sha256
from pathlib import PurePath
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from markweave.auth.models import Role, User
from markweave.composer.connections import ConnectionActor, ConnectionState
from markweave.composer.drafts import (
    ComposerDraft,
    ComposerMessage,
    ComposerProposal,
    ProposalState,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerRequestTooLargeError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerDraftListResponse,
    ComposerDraftResponse,
    ComposerDraftSummaryResponse,
    ComposerDraftUpdateRequest,
    ComposerHandoffRequest,
    ComposerMessageCreateRequest,
    ComposerMessageListResponse,
    ComposerMessageResponse,
    ComposerProposalDecisionRequest,
    ComposerProposalListResponse,
    ComposerProposalResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.jobs.models import JobOutput
from markweave.persistence.composer import SqlComposerRepository

_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
}
_SOURCE_EXTENSIONS = frozenset(_MEDIA_TYPES)
_MAXIMUM_TITLE_CHARACTERS = 256
_FIRST_PRINTABLE_CODEPOINT = 32
_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126
_MAXIMUM_OFFSET = 2_147_483_647


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def _new_work_allowed(dependencies: HttpDependencies, user: User) -> None:
    service = dependencies.components.composer_connections
    repository = dependencies.components.composer_connection_repository
    if service is None or repository is None:
        raise ComposerUnavailableError
    actor = ConnectionActor(
        user.id,
        user.role is Role.ADMIN,
        repository.can_manage_personal(user.id),
    )
    if service.availability(actor).state is not ConnectionState.READY:
        raise ComposerUnavailableError


def _draft_response(draft: ComposerDraft) -> ComposerDraftResponse:
    return ComposerDraftResponse(
        id=draft.id,
        title=draft.title,
        content=draft.content,
        version=draft.version,
        current_revision_id=draft.current_revision_id,
        source_kind=draft.source.kind,
        source_media_type=draft.source.media_type,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        etag=draft.etag,
    )


def _draft_summary(draft: ComposerDraft) -> ComposerDraftSummaryResponse:
    return ComposerDraftSummaryResponse(
        id=draft.id,
        title=draft.title,
        version=draft.version,
        current_revision_id=draft.current_revision_id,
        source_kind=draft.source.kind,
        source_media_type=draft.source.media_type,
        updated_at=draft.updated_at,
        etag=draft.etag,
    )


def _message_response(message: ComposerMessage) -> ComposerMessageResponse:
    return ComposerMessageResponse(
        id=message.id,
        draft_id=message.draft_id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def _proposal_response(proposal: ComposerProposal) -> ComposerProposalResponse:
    return ComposerProposalResponse(
        id=proposal.id,
        draft_id=proposal.draft_id,
        base_version=proposal.base_version,
        state=proposal.state.value,
        proposed_value=proposal.proposed_value,
        decided_value=proposal.decided_value,
        provenance=proposal.provenance,
        created_at=proposal.created_at,
        decided_at=proposal.decided_at,
        decided_by=proposal.decided_by,
    )


def _validate_filename(filename: str | None) -> str:
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or any(ord(character) < _FIRST_PRINTABLE_CODEPOINT for character in filename)
        or PurePath(filename).suffix.lower() not in _SOURCE_EXTENSIONS
    ):
        raise ComposerRequestError
    return filename


def _title(value: str | None, filename: str) -> str:
    title = value.strip() if value is not None else PurePath(filename).stem
    if (
        not title
        or len(title) > _MAXIMUM_TITLE_CHARACTERS
        or any(ord(character) < _FIRST_PRINTABLE_CODEPOINT for character in title)
    ):
        raise ComposerRequestError
    return title


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
    """Expose scanned sources and retained owner drafts without LLM dependency on reads."""
    router = APIRouter()

    @router.post(
        "/api/v1/composer/drafts",
        response_model=ComposerDraftResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 413, 422, 503),
    )
    async def create_draft(
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        source: Annotated[UploadFile, File()],
        title: Annotated[str | None, Form()] = None,
        content: Annotated[str | None, Form()] = None,
    ) -> ComposerDraftResponse:
        maximum = dependencies.settings.composer_upload_max_bytes
        if maximum is None:
            await source.close()
            raise ComposerUnavailableError
        try:
            data = await source.read(maximum + 1)
        finally:
            await source.close()
        if len(data) > maximum:
            raise ComposerRequestTooLargeError
        if not data:
            raise ComposerRequestError
        await run_in_threadpool(dependencies.components.scanner.scan, data)
        filename = _validate_filename(source.filename)
        draft_title = _title(title, filename)
        try:
            original_markdown = (
                data.decode("utf-8") if filename.lower().endswith(".md") else ""
            )
        except UnicodeDecodeError:
            raise ComposerRequestError from None
        draft_content = original_markdown if content is None else content
        _new_work_allowed(dependencies, user)
        draft = await run_in_threadpool(
            _store(dependencies).create_draft_with_source,
            user.id,
            data,
            f"{dependencies.settings.malware_scanning_mode.value}:{uuid4()}",
            title=draft_title,
            content=draft_content,
            media_type=_MEDIA_TYPES[PurePath(filename).suffix.lower()],
        )
        response.headers["Location"] = f"/api/v1/composer/drafts/{draft.id}"
        response.headers["ETag"] = draft.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _draft_response(draft)

    @router.post(
        "/api/v1/composer/drafts/from-conversion/{job_id}",
        response_model=ComposerDraftResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 409, 413, 422, 503),
    )
    async def handoff_conversion(
        job_id: UUID,
        payload: ComposerHandoffRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ComposerDraftResponse:
        maximum = dependencies.settings.composer_upload_max_bytes
        if maximum is None:
            raise ComposerUnavailableError
        job = await run_in_threadpool(
            dependencies.components.jobs.get_visible,
            job_id,
            actor_id=user.id,
            actor_is_admin=False,
        )
        if job.output not in {JobOutput.DOCX, JobOutput.PDF, JobOutput.PPTX}:
            raise ComposerRequestError
        _job, data = await run_in_threadpool(
            dependencies.components.jobs.download,
            job_id,
            actor_id=user.id,
            actor_is_admin=False,
        )
        if len(data) > maximum:
            raise ComposerRequestTooLargeError
        await run_in_threadpool(dependencies.components.scanner.scan, data)
        draft_title = _title(payload.title, f"document.{job.output.value}")
        _new_work_allowed(dependencies, user)
        draft = await run_in_threadpool(
            _store(dependencies).create_draft_with_source,
            user.id,
            data,
            (
                f"{dependencies.settings.malware_scanning_mode.value}:"
                f"conversion:{job.id}:{uuid4()}"
            ),
            title=draft_title,
            content="",
            media_type=_MEDIA_TYPES[f".{job.output.value}"],
            origin_job_id=job.id,
            origin_result_object_id=job.result_object_id,
            origin_result_sha256=sha256(data).hexdigest(),
        )
        response.headers["Location"] = f"/api/v1/composer/drafts/{draft.id}"
        response.headers["ETag"] = draft.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _draft_response(draft)

    @router.get(
        "/api/v1/composer/drafts",
        response_model=ComposerDraftListResponse,
        tags=["composer"],
        responses=error_responses(401, 503),
    )
    def list_drafts(
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerDraftListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerDraftListResponse(
            drafts=tuple(
                _draft_summary(draft)
                for draft in _store(dependencies).list_drafts(
                    user.id, limit=limit, offset=offset
                )
            ),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}",
        response_model=ComposerDraftResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_draft(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerDraftResponse:
        draft = _store(dependencies).get_draft(user.id, draft_id)
        response.headers["ETag"] = draft.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _draft_response(draft)

    @router.put(
        "/api/v1/composer/drafts/{draft_id}",
        response_model=ComposerDraftResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def save_draft(
        draft_id: UUID,
        payload: ComposerDraftUpdateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ComposerDraftResponse:
        if if_match is None:
            raise ComposerPreconditionRequiredError
        draft = _store(dependencies).save_draft(
            user.id,
            draft_id,
            if_match=if_match,
            title=payload.title,
            content=payload.content,
        )
        response.headers["ETag"] = draft.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _draft_response(draft)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/messages",
        response_model=ComposerMessageListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def list_messages(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerMessageListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerMessageListResponse(
            messages=tuple(
                _message_response(message)
                for message in _store(dependencies).list_messages(
                    user.id, draft_id, limit=limit, offset=offset
                )
            ),
            limit=limit,
            offset=offset,
        )

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/messages",
        response_model=ComposerMessageResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def add_message(  # noqa: PLR0913, PLR0917 - explicit FastAPI request fields
        draft_id: UUID,
        payload: ComposerMessageCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerMessageResponse:
        if if_match is None or idempotency_key is None:
            raise ComposerPreconditionRequiredError
        if (
            not idempotency_key
            or len(idempotency_key) > _MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS
            or any(
                ord(character) < _FIRST_VISIBLE_ASCII
                or ord(character) > _LAST_VISIBLE_ASCII
                for character in idempotency_key
            )
        ):
            raise ComposerRequestError
        message = _store(dependencies).add_message(
            user.id,
            draft_id,
            role="user",
            content=payload.content,
            message_id=uuid5(
                NAMESPACE_URL,
                f"composer-message:{user.id}:{draft_id}:{idempotency_key}",
            ),
            if_match=if_match,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _message_response(message)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/proposals",
        response_model=ComposerProposalListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def list_proposals(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerProposalListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerProposalListResponse(
            proposals=tuple(
                _proposal_response(proposal)
                for proposal in _store(dependencies).list_proposals(
                    user.id, draft_id, limit=limit, offset=offset
                )
            ),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}",
        response_model=ComposerProposalResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_proposal(
        draft_id: UUID,
        proposal_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerProposalResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return _proposal_response(
            _store(dependencies).get_proposal(user.id, draft_id, proposal_id)
        )

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}/decision",
        response_model=ComposerProposalResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def decide_proposal(  # noqa: PLR0913, PLR0917 - explicit FastAPI request fields
        draft_id: UUID,
        proposal_id: UUID,
        payload: ComposerProposalDecisionRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ComposerProposalResponse:
        if if_match is None:
            raise ComposerPreconditionRequiredError
        proposal = _store(dependencies).decide_proposal(
            user.id,
            draft_id,
            proposal_id,
            if_match=if_match,
            state=ProposalState(payload.state.value),
            decided_value=payload.decided_value,
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _proposal_response(proposal)

    return router
