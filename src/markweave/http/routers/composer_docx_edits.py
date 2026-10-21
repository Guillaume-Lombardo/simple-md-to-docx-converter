"""Read and apply bounded DOCX text edits on exact, owner-scoped revisions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import User
from markweave.composer.docx_edits import (
    DOCX_EDIT_ENGINE_VERSION,
    DOCX_EDIT_MEDIA_TYPE,
    ReplaceText,
    apply_docx_text_edits,
    inspect_docx_text,
    operations_record,
)
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerRevision,
    RevisionSnapshot,
    direct_publish_lineage,
)
from markweave.http.composer_docx_edit_schemas import (
    ComposerDocxEditRequest,
    ComposerDocxTextTargetListResponse,
    ComposerDocxTextTargetResponse,
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


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def _docx_download(
    store: SqlComposerRepository, owner_id: UUID, revision: ComposerRevision
) -> tuple[bytes, str]:
    artifact = next(item for item in revision.artifacts if item.kind == "download")
    if artifact.media_type != DOCX_EDIT_MEDIA_TYPE:
        raise ComposerRequestError("Revision download is not a DOCX document")
    content = store.read_artifact(owner_id, revision.draft_id, revision.id, "download")
    return content, artifact.sha256


def _operation(revision_id: UUID) -> str:
    return f"docx_edit:{revision_id}"


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Expose the first qualified T92 Office edit family: DOCX body text."""
    router = APIRouter()

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/docx-text-targets",
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
        content, digest = _docx_download(store, user.id, revision)
        targets = inspect_docx_text(content, typed_docx_limits(dependencies))
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerDocxTextTargetListResponse(
            revision_id=revision.id,
            download_sha256=digest,
            engine_version=DOCX_EDIT_ENGINE_VERSION,
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
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/docx-edits",
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

        match, key = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        operations = tuple(
            ReplaceText(item.target, item.expected_text, item.text)
            for item in payload.operations
        )
        record = operations_record(operations)
        replay = store.find_revision_by_key(user.id, draft_id, key)
        if replay is not None:
            if (
                replay.snapshot.operation != _operation(revision_id)
                or replay.snapshot.approved_values != record
            ):
                raise ComposerConflictError("Revision idempotency key was reused")
            revision = replay
        else:
            draft = store.get_draft(user.id, draft_id)
            if draft.current_revision_id != revision_id:
                raise ComposerConflictError("Only the current revision can be edited")
            base = store.get_revision(user.id, draft_id, revision_id)
            content, _digest = _docx_download(store, user.id, base)
            edited = apply_docx_text_edits(
                content, operations, typed_docx_limits(dependencies)
            )
            revision = store.publish_revision(
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
                    provenance="human:docx_edit",
                    operation=_operation(revision_id),
                ),
                artifacts=(
                    ArtifactContent("download", DOCX_EDIT_MEDIA_TYPE, edited),
                    ArtifactContent("preview", DOCX_EDIT_MEDIA_TYPE, edited),
                ),
            )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    return router
