"""Read and apply bounded DOCX and PPTX text edits on exact, owner-scoped revisions."""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import User
from markweave.composer.docx_edits import (
    DOCX_EDIT_MEDIA_TYPE,
    apply_docx_text_edits,
    inspect_docx_text,
)
from markweave.composer.office_edits import (
    OFFICE_EDIT_ENGINE_VERSION,
    ReplaceText,
    operations_record,
)
from markweave.composer.pptx_edits import (
    PPTX_EDIT_MEDIA_TYPE,
    apply_pptx_text_edits,
    inspect_pptx_text,
)
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerRevision,
    RevisionSnapshot,
    direct_publish_lineage,
)
from markweave.composer.typed_templates import TypedDocxLimits
from markweave.http.composer_docx_edit_schemas import (
    ComposerDocxEditRequest,
    ComposerDocxReplaceTextOperation,
    ComposerDocxTextTargetListResponse,
    ComposerDocxTextTargetResponse,
    ComposerPptxEditRequest,
    ComposerPptxTextTargetListResponse,
    ComposerPptxTextTargetResponse,
)
from markweave.http.composer_errors import (
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import ComposerRevisionResponse
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer import SqlComposerRepository

from .composer_fill_templates import typed_docx_limits
from .composer_revisions import _require_headers, _revision_response

_MAXIMUM_OFFSET = 2_147_483_647
_REVISION = "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}"


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def office_download(
    store: SqlComposerRepository,
    owner_id: UUID,
    revision: ComposerRevision,
    media_type: str,
) -> tuple[bytes, str]:
    artifact = next(item for item in revision.artifacts if item.kind == "download")
    if artifact.media_type != media_type:
        raise ComposerRequestError("Revision download has another document format")
    content = store.read_artifact(owner_id, revision.draft_id, revision.id, "download")
    return content, artifact.sha256


def _text_edit(
    requested: tuple[ComposerDocxReplaceTextOperation, ...],
    apply: Callable[[bytes, tuple[ReplaceText, ...], TypedDocxLimits], bytes],
) -> tuple[str, Callable[[bytes, TypedDocxLimits], bytes]]:
    operations = tuple(
        ReplaceText(item.target, item.expected_text, item.text) for item in requested
    )
    return operations_record(operations), lambda content, limits: apply(
        content, operations, limits
    )


def publish_office_edit(  # noqa: PLR0913 - one explicit publication contract
    dependencies: HttpDependencies,
    user: User,
    draft_id: UUID,
    revision_id: UUID,
    headers: tuple[str, str],
    *,
    label: str,
    media_type: str,
    record: str,
    edit: Callable[[bytes, TypedDocxLimits], bytes],
) -> ComposerRevision:
    """Edit a private copy of the current revision and publish the exact result.

    ``record`` is the frozen operation record; a retried key must repeat it.
    """

    match, key = headers
    store = _store(dependencies)
    operation = f"{label}:{revision_id}"
    replay = store.find_revision_by_key(user.id, draft_id, key)
    if replay is not None:
        if (
            replay.snapshot.operation != operation
            or replay.snapshot.approved_values != record
        ):
            raise ComposerConflictError("Revision idempotency key was reused")
        return replay
    draft = store.get_draft(user.id, draft_id)
    if draft.current_revision_id != revision_id:
        raise ComposerConflictError("Only the current revision can be edited")
    base = store.get_revision(user.id, draft_id, revision_id)
    content, _digest = office_download(store, user.id, base, media_type)
    edited = edit(content, typed_docx_limits(dependencies))
    return store.publish_revision(
        user.id,
        draft_id,
        actor_id=user.id,
        if_match=match,
        idempotency_key=key,
        snapshot=RevisionSnapshot(
            source=draft.source,
            template_reference=base.snapshot.template_reference,
            approved_values=record,
            render_options=direct_publish_lineage(
                base.id,
                base.snapshot.model_identity,
                base.snapshot.render_options,
            ),
            model_identity=None,
            provenance=f"human:{label}",
            operation=operation,
        ),
        artifacts=(
            ArtifactContent("download", media_type, edited),
            ArtifactContent("preview", media_type, edited),
        ),
    )


def published_response(
    dependencies: HttpDependencies,
    user: User,
    draft_id: UUID,
    revision: ComposerRevision,
    response: Response,
) -> ComposerRevisionResponse:
    response.headers["Location"] = (
        f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
    )
    response.headers["ETag"] = _store(dependencies).get_draft(user.id, draft_id).etag
    response.headers["Cache-Control"] = "private, no-store"
    return _revision_response(revision)


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose the qualified T92 Office text edit families: DOCX body and PPTX slides."""
    router = APIRouter()

    @router.get(
        f"{_REVISION}/docx-text-targets",
        response_model=ComposerDocxTextTargetListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 413, 422, 503),
    )
    def list_text_targets(  # noqa: PLR0913, PLR0917 - explicit FastAPI page fields
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerDocxTextTargetListResponse:
        store = _store(dependencies)
        revision = store.get_revision(user.id, draft_id, revision_id)
        content, digest = office_download(
            store, user.id, revision, DOCX_EDIT_MEDIA_TYPE
        )
        targets = inspect_docx_text(content, typed_docx_limits(dependencies))
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerDocxTextTargetListResponse(
            revision_id=revision.id,
            download_sha256=digest,
            engine_version=OFFICE_EDIT_ENGINE_VERSION,
            targets=tuple(
                ComposerDocxTextTargetResponse(
                    target=item.target,
                    paragraph=item.paragraph,
                    in_table=item.in_table,
                    text=item.text,
                )
                for item in targets[offset : offset + limit]
            ),
            total=len(targets),
            limit=limit,
            offset=offset,
        )

    @router.post(
        f"{_REVISION}/docx-edits",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def edit_docx_text(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        revision_id: UUID,
        payload: ComposerDocxEditRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        """Publish a new revision whose DOCX differs only in the requested text."""

        record, edit = _text_edit(payload.operations, apply_docx_text_edits)
        revision = publish_office_edit(
            dependencies,
            user,
            draft_id,
            revision_id,
            _require_headers(if_match, idempotency_key),
            label="docx_edit",
            media_type=DOCX_EDIT_MEDIA_TYPE,
            record=record,
            edit=edit,
        )
        return published_response(dependencies, user, draft_id, revision, response)

    @router.get(
        f"{_REVISION}/pptx-text-targets",
        response_model=ComposerPptxTextTargetListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 413, 422, 503),
    )
    def list_pptx_text_targets(  # noqa: PLR0913, PLR0917 - explicit FastAPI page fields
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerPptxTextTargetListResponse:
        store = _store(dependencies)
        revision = store.get_revision(user.id, draft_id, revision_id)
        content, digest = office_download(
            store, user.id, revision, PPTX_EDIT_MEDIA_TYPE
        )
        targets = inspect_pptx_text(content, typed_docx_limits(dependencies))
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerPptxTextTargetListResponse(
            revision_id=revision.id,
            download_sha256=digest,
            engine_version=OFFICE_EDIT_ENGINE_VERSION,
            targets=tuple(
                ComposerPptxTextTargetResponse(
                    target=item.target,
                    slide=item.slide,
                    notes=item.notes,
                    paragraph=item.paragraph,
                    in_table=item.in_table,
                    text=item.text,
                )
                for item in targets[offset : offset + limit]
            ),
            total=len(targets),
            limit=limit,
            offset=offset,
        )

    @router.post(
        f"{_REVISION}/pptx-edits",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def edit_pptx_text(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        revision_id: UUID,
        payload: ComposerPptxEditRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        """Publish a new revision whose PPTX differs only in the requested text."""

        record, edit = _text_edit(payload.operations, apply_pptx_text_edits)
        revision = publish_office_edit(
            dependencies,
            user,
            draft_id,
            revision_id,
            _require_headers(if_match, idempotency_key),
            label="pptx_edit",
            media_type=PPTX_EDIT_MEDIA_TYPE,
            record=record,
            edit=edit,
        )
        return published_response(dependencies, user, draft_id, revision, response)

    return router
