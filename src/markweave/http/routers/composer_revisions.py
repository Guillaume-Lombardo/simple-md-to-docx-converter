"""Exact, owner-scoped Composer revision and artifact routes."""

import json
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import User
from markweave.composer.diff import markdown_changes
from markweave.composer.drafts import ComposerProposal, ProposalState
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerNotFoundError,
    ComposerRevision,
    RevisionSnapshot,
    direct_publish_lineage,
)
from markweave.composer.sources import (
    markdown_from_archive,
    markdown_from_reversion_result,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerArtifactResponse,
    ComposerRevisionDiffChange,
    ComposerRevisionDiffResponse,
    ComposerRevisionListResponse,
    ComposerRevisionResponse,
    ComposerRevisionSummaryResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer import SqlComposerRepository

_EXTENSIONS = {
    "application/json": "json",
    "text/markdown": "md",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/zip": "zip",
}
_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126
_MAXIMUM_OFFSET = 2_147_483_647
_MAXIMUM_DIFF_TEXT_BYTES = 131_072


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
        typed_fill_snapshot=snapshot.typed_fill_snapshot,
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


def _approved_markdown(revision: ComposerRevision) -> bytes | None:
    try:
        values = json.loads(revision.snapshot.approved_values)
        if isinstance(values, dict) and isinstance(values.get("content"), str):
            return values["content"].encode("utf-8")
    except UnicodeEncodeError, ValueError, TypeError:
        pass
    return None


def _render_metadata(revision: ComposerRevision) -> tuple[object, object, object]:
    try:
        options = json.loads(revision.snapshot.render_options)
    except ValueError, TypeError:
        options = {}
    if not isinstance(options, dict):
        options = {}
    return (
        options.get("output"),
        options.get("presentation_options"),
        options.get("component_versions"),
    )


def _metadata_changes(old: ComposerRevision, new: ComposerRevision) -> tuple[str, ...]:
    changes = []
    if old.snapshot.template_reference != new.snapshot.template_reference:
        changes.append("template_reference")
    before, after = _render_metadata(old), _render_metadata(new)
    for index, name in enumerate(
        ("output", "presentation_options", "component_versions")
    ):
        if before[index] != after[index]:
            changes.append(name)
    return tuple(changes)


def _proposal_model_identity(
    dependencies: HttpDependencies,
    owner_id: UUID,
    draft_id: UUID,
    proposal: ComposerProposal,
) -> str | None:
    if not proposal.provenance.startswith("model-step:"):
        return None
    try:
        step_id = UUID(proposal.provenance.removeprefix("model-step:"))
    except ValueError:
        raise ComposerConflictError("Composer proposal origin is invalid") from None
    steps = dependencies.components.composer_model_step_repository
    if steps is None:
        raise ComposerUnavailableError
    step = steps.get_model_step(owner_id, draft_id, step_id)
    if step.status != "completed" or step.proposal_id != proposal.id:
        raise ComposerConflictError("Composer proposal origin changed")
    return step.model_identity


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
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
        preview_content = source
        preview_media_type = draft.source.media_type
        if draft.source.media_type == "application/zip":
            markdown = (
                markdown_from_reversion_result(source, dependencies.settings)
                if draft.source.kind == "reversion_result"
                else markdown_from_archive(source, dependencies.settings)
            )
            source_content = {"content": markdown}
            preview_content = markdown.encode("utf-8")
            preview_media_type = "text/markdown"
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
            ArtifactContent("preview", preview_media_type, preview_content),
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
    def list_revisions(  # noqa: PLR0913, PLR0917 - explicit FastAPI page fields
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
        order: Literal["asc", "desc"] = "asc",
    ) -> ComposerRevisionListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerRevisionListResponse(
            revisions=tuple(
                _revision_summary(revision)
                for revision in _store(dependencies).list_revisions(
                    user.id, draft_id, limit=limit, offset=offset, order=order
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

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}/publish",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def publish_proposal(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        proposal_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        match, key = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        draft = store.get_draft(user.id, draft_id)
        proposal = store.get_proposal(user.id, draft_id, proposal_id)
        if draft.source.media_type not in {"text/markdown", "application/zip"}:
            raise ComposerRequestError(
                "Proposal publication supports Markdown sources only"
            )
        if proposal.state not in {ProposalState.ACCEPTED, ProposalState.EDITED}:
            raise ComposerRequestError("Proposal has not been approved")
        value = proposal.decided_value
        if value is None or not value.strip():
            raise ComposerRequestError("Approved Markdown content is empty")
        try:
            content = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ComposerRequestError("Approved Markdown content is invalid") from None
        maximum = dependencies.settings.composer_upload_max_bytes
        if maximum is None or len(content) > maximum:
            raise ComposerRequestError("Approved Markdown content exceeds the limit")
        snapshot = RevisionSnapshot(
            source=draft.source,
            template_reference=None,
            approved_values=json.dumps({"content": value}, ensure_ascii=False),
            render_options="{}",
            model_identity=_proposal_model_identity(
                dependencies, user.id, draft_id, proposal
            ),
            provenance=f"human:{proposal.state.value}:{proposal.provenance}",
            operation=f"publish_proposal:{proposal_id}",
        )
        artifacts = [
            ArtifactContent("download", "text/markdown", content),
            ArtifactContent("preview", "text/markdown", content),
        ]
        if draft.source.media_type == "application/zip":
            artifacts.insert(
                0,
                ArtifactContent(
                    "source",
                    "application/zip",
                    store.read_source(user.id, draft.source.object_id),
                ),
            )
        revision = store.publish_revision(
            user.id,
            draft_id,
            actor_id=user.id,
            if_match=match,
            idempotency_key=key,
            snapshot=snapshot,
            artifacts=tuple(artifacts),
            approved_proposal_id=proposal_id,
        )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/revisions/from-draft",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def publish_draft(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        """Freeze the exact saved Markdown draft, including its scanned asset source."""

        match, key = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        replay = store.find_revision_by_key(user.id, draft_id, key)
        if replay is not None:
            if replay.snapshot.operation != "publish_draft":
                raise ComposerConflictError("Revision idempotency key was reused")
            response.headers["Location"] = (
                f"/api/v1/composer/drafts/{draft_id}/revisions/{replay.id}"
            )
            response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
            response.headers["Cache-Control"] = "private, no-store"
            return _revision_response(replay)
        draft = store.get_draft(user.id, draft_id)
        if draft.source.media_type not in {"text/markdown", "application/zip"}:
            raise ComposerRequestError("Current draft publication requires Markdown")
        if not draft.content.strip():
            raise ComposerRequestError("Current Markdown content is empty")
        try:
            content = draft.content.encode("utf-8")
        except UnicodeEncodeError:
            raise ComposerRequestError("Current Markdown content is invalid") from None
        maximum = dependencies.settings.composer_upload_max_bytes
        if maximum is None or len(content) > maximum:
            raise ComposerRequestError("Current Markdown content exceeds the limit")
        artifacts = [
            ArtifactContent("download", "text/markdown", content),
            ArtifactContent("preview", "text/markdown", content),
        ]
        if draft.source.media_type == "application/zip":
            artifacts.insert(
                0,
                ArtifactContent(
                    "source",
                    "application/zip",
                    store.read_source(user.id, draft.source.object_id),
                ),
            )
        parent = (
            store.get_revision(user.id, draft_id, draft.current_revision_id)
            if draft.current_revision_id is not None
            else None
        )
        revision = store.publish_revision(
            user.id,
            draft_id,
            actor_id=user.id,
            if_match=match,
            idempotency_key=key,
            snapshot=RevisionSnapshot(
                source=draft.source,
                template_reference=None,
                approved_values=json.dumps(
                    {"content": draft.content}, ensure_ascii=False
                ),
                render_options=direct_publish_lineage(
                    parent.id if parent else None,
                    parent.snapshot.model_identity if parent else None,
                    parent.snapshot.render_options if parent else None,
                ),
                model_identity=None,
                provenance="human:direct",
                operation="publish_draft",
            ),
            artifacts=tuple(artifacts),
        )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/diff",
        response_model=ComposerRevisionDiffResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 422, 503),
    )
    def diff_revision(
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        from_revision_id: UUID,
    ) -> ComposerRevisionDiffResponse:
        store = _store(dependencies)
        old = store.get_revision(user.id, draft_id, from_revision_id)
        new = store.get_revision(user.id, draft_id, revision_id)
        old_artifact = next(item for item in old.artifacts if item.kind == "download")
        new_artifact = next(item for item in new.artifacts if item.kind == "download")
        response.headers["Cache-Control"] = "private, no-store"
        metadata_changes = _metadata_changes(old, new)
        old_text = _approved_markdown(old)
        new_text = _approved_markdown(new)
        markdown_scope = (
            old_text is not None
            and new_text is not None
            and (
                old_artifact.media_type != "text/markdown"
                or new_artifact.media_type != "text/markdown"
            )
        )
        if old_artifact.sha256 == new_artifact.sha256 and not markdown_scope:
            return ComposerRevisionDiffResponse(
                from_revision_id=from_revision_id,
                to_revision_id=revision_id,
                status="unchanged",
                reason=None,
                changes=(),
                metadata_changes=metadata_changes,
            )
        if markdown_scope:
            before, after = old_text, new_text
        elif (
            old_artifact.media_type != "text/markdown"
            or new_artifact.media_type != "text/markdown"
        ):
            return ComposerRevisionDiffResponse(
                from_revision_id=from_revision_id,
                to_revision_id=revision_id,
                status="unavailable",
                reason="native_semantic_diff_unsupported",
                changes=(),
                metadata_changes=metadata_changes,
            )
        else:
            before = store.read_artifact(
                user.id, draft_id, from_revision_id, "download"
            )
            after = store.read_artifact(user.id, draft_id, revision_id, "download")
        if (
            len(before) > _MAXIMUM_DIFF_TEXT_BYTES
            or len(after) > _MAXIMUM_DIFF_TEXT_BYTES
        ):
            changes, reason = (), "text_too_large"
        else:
            changes, reason = markdown_changes(before, after)
        return ComposerRevisionDiffResponse(
            from_revision_id=from_revision_id,
            to_revision_id=revision_id,
            status="unavailable" if reason else "available" if changes else "unchanged",
            reason=reason,
            changes=tuple(
                ComposerRevisionDiffChange(**asdict(item)) for item in changes
            ),
            scope="approved_markdown" if markdown_scope else "artifact",
            metadata_changes=metadata_changes,
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
        if kind not in {"source", "download", "preview", "traceability"}:
            raise ComposerRequestError
        store = _store(dependencies)
        revision = store.get_revision(user.id, draft_id, revision_id)
        artifact = next(
            (item for item in revision.artifacts if item.kind == kind), None
        )
        if artifact is None:
            raise ComposerNotFoundError("Composer artifact does not exist")
        content = store.read_artifact(user.id, draft_id, revision_id, kind)
        extension = _EXTENSIONS.get(artifact.media_type, "bin")
        disposition = "inline" if kind == "preview" else "attachment"
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
