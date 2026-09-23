"""Exact, owner-scoped Composer revision and artifact routes."""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import User
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerRevision,
    RevisionSnapshot,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerArtifactResponse,
    ComposerRevisionListResponse,
    ComposerRevisionResponse,
    ComposerRevisionSummaryResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer import SqlComposerRepository

_EXTENSIONS = {
    "text/markdown": "md",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126
_MAXIMUM_OFFSET = 2_147_483_647


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def _require_headers(
    if_match: str | None, idempotency_key: str | None
) -> tuple[str, str]:
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
    return if_match, idempotency_key


def _revision_response(revision: ComposerRevision) -> ComposerRevisionResponse:
    snapshot = revision.snapshot
    return ComposerRevisionResponse(
        id=revision.id,
        draft_id=revision.draft_id,
        number=revision.number,
        operation=snapshot.operation,
        provenance=snapshot.provenance,
        source_sha256=snapshot.source.sha256,
        template_reference=snapshot.template_reference,
        approved_values=snapshot.approved_values,
        render_options=snapshot.render_options,
        model_identity=snapshot.model_identity,
        artifacts=tuple(
            ComposerArtifactResponse(
                kind=item.kind,
                sha256=item.sha256,
                size=item.size,
                media_type=item.media_type,
            )
            for item in revision.artifacts
        ),
        restored_from_revision_id=revision.restored_from_revision_id,
        created_at=revision.created_at,
    )


def _revision_summary(revision: ComposerRevision) -> ComposerRevisionSummaryResponse:
    snapshot = revision.snapshot
    return ComposerRevisionSummaryResponse(
        id=revision.id,
        draft_id=revision.draft_id,
        number=revision.number,
        operation=snapshot.operation,
        provenance=snapshot.provenance,
        model_identity=snapshot.model_identity,
        restored_from_revision_id=revision.restored_from_revision_id,
        created_at=revision.created_at,
    )


def build_router(dependencies: HttpDependencies) -> APIRouter:
    """Publish source snapshots and expose immutable revision bytes."""
    router = APIRouter()

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/revisions/from-source",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def capture_source(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        match, key = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        draft = store.get_draft(user.id, draft_id)
        source = store.read_source(user.id, draft.source.object_id)
        try:
            source_content = (
                {"content": source.decode("utf-8")}
                if draft.source.media_type == "text/markdown"
                else {}
            )
        except UnicodeDecodeError:
            raise ComposerRequestError from None
        snapshot = RevisionSnapshot(
            source=draft.source,
            template_reference=None,
            approved_values=json.dumps(source_content, ensure_ascii=False),
            render_options="{}",
            model_identity=None,
            provenance="human:source",
            operation="capture_source",
        )
        artifacts = (
            ArtifactContent("download", draft.source.media_type, source),
            ArtifactContent("preview", draft.source.media_type, source),
        )
        revision = store.publish_revision(
            user.id,
            draft_id,
            actor_id=user.id,
            if_match=match,
            idempotency_key=key,
            snapshot=snapshot,
            artifacts=artifacts,
        )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/revisions",
        response_model=ComposerRevisionListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def list_revisions(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerRevisionListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerRevisionListResponse(
            revisions=tuple(
                _revision_summary(revision)
                for revision in _store(dependencies).list_revisions(
                    user.id, draft_id, limit=limit, offset=offset
                )
            ),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}",
        response_model=ComposerRevisionResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_revision(
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerRevisionResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(
            _store(dependencies).get_revision(user.id, draft_id, revision_id)
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/artifacts/{kind}",
        tags=["composer"],
        responses=error_responses(401, 404, 422, 503),
    )
    def read_artifact(
        draft_id: UUID,
        revision_id: UUID,
        kind: str,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> Response:
        if kind not in {"download", "preview"}:
            raise ComposerRequestError
        store = _store(dependencies)
        revision = store.get_revision(user.id, draft_id, revision_id)
        artifact = next(item for item in revision.artifacts if item.kind == kind)
        content = store.read_artifact(user.id, draft_id, revision_id, kind)
        extension = _EXTENSIONS.get(artifact.media_type, "bin")
        disposition = "attachment" if kind == "download" else "inline"
        return Response(
            content=content,
            media_type=artifact.media_type,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'{disposition}; filename="composer-{revision.number}.{extension}"'
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Composer-Revision": str(revision.id),
            },
        )

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/restore",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def restore_revision(  # noqa: PLR0913, PLR0917 - explicit FastAPI request fields
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        match, key = _require_headers(if_match, idempotency_key)
        revision = _store(dependencies).restore_revision(
            user.id,
            draft_id,
            revision_id,
            actor_id=user.id,
            if_match=match,
            idempotency_key=key,
        )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = (
            _store(dependencies).get_draft(user.id, draft_id).etag
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    return router
