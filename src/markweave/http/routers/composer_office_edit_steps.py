"""Model-proposed Office text edits: exact preview, bounded step and reviewed publication."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from markweave.auth.models import User
from markweave.composer.docx_edits import (
    DOCX_EDIT_MEDIA_TYPE,
    DOCX_PACKAGE,
    apply_docx_text_edits,
)
from markweave.composer.drafts import ComposerProposal, ProposalState
from markweave.composer.office_edit_proposals import (
    OFFICE_EDIT_INTENT,
    OfficeEditContext,
    OfficeEditProposal,
    OfficeEditProposalError,
    compose_office_prompt,
    parse_proposal,
    reviewed_decision,
)
from markweave.composer.office_edits import inspect_text
from markweave.composer.pptx_edits import (
    PPTX_EDIT_MEDIA_TYPE,
    PPTX_PACKAGE,
    apply_pptx_text_edits,
)
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerRevision,
    RevisionSnapshot,
    direct_publish_lineage,
)
from markweave.http.composer_docx_edit_schemas import (
    ComposerOfficeEditStepCreateRequest,
    ComposerOfficeEditStepPreviewRequest,
    ComposerOfficeEditStepPreviewResponse,
    ComposerOfficeEditTargetText,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerModelStepResponse,
    ComposerRevisionResponse,
)
from markweave.http.composer_step_runner import ModelStepInput
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import capacity_error_responses, error_responses
from markweave.persistence.composer import ComposerModelStep, SqlComposerRepository

from .composer_fill_templates import typed_docx_limits
from .composer_model_steps import _actor, _payload_digest
from .composer_model_steps import _require_headers as _step_headers
from .composer_model_steps import _response as _step_response
from .composer_revisions import _require_headers, _revision_response

_FORMATS = {
    DOCX_EDIT_MEDIA_TYPE: ("docx", DOCX_PACKAGE, apply_docx_text_edits),
    PPTX_EDIT_MEDIA_TYPE: ("pptx", PPTX_PACKAGE, apply_pptx_text_edits),
}
_MEDIA_TYPES = {name: media for media, (name, _, _) in _FORMATS.items()}


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def office_edit_step(
    dependencies: HttpDependencies,
    owner_id: UUID,
    draft_id: UUID,
    proposal: ComposerProposal,
) -> ComposerModelStep | None:
    """Return the completed Office edit step behind a proposal, if it has one."""

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
    if step.intent != OFFICE_EDIT_INTENT:
        return None
    if step.status != "completed" or step.proposal_id != proposal.id:
        raise ComposerConflictError("Composer proposal origin changed")
    return step


def reviewed_office_decision(
    dependencies: HttpDependencies,
    owner_id: UUID,
    draft_id: UUID,
    proposal: ComposerProposal,
    decided_value: str | None,
) -> str | None:
    """Canonicalize a human edit of an Office proposal; other values pass through."""

    if (
        decided_value is None
        or office_edit_step(dependencies, owner_id, draft_id, proposal) is None
    ):
        return decided_value
    limit = typed_docx_limits(dependencies).max_text_characters
    try:
        return reviewed_decision(
            proposal.proposed_value, decided_value, limit
        ).canonical()
    except OfficeEditProposalError:
        raise ComposerRequestError("Office edit decision is invalid") from None


def _office_context(  # noqa: PLR0913, PLR0917 - exact disclosure inputs
    dependencies: HttpDependencies,
    user: User,
    draft_id: UUID,
    revision_id: UUID,
    targets: tuple[str, ...],
    if_match: str,
) -> OfficeEditContext:
    store = _store(dependencies)
    draft = store.get_draft(user.id, draft_id)
    if draft.etag != if_match:
        raise ComposerConflictError("Composer draft changed")
    if draft.current_revision_id != revision_id:
        raise ComposerConflictError("Only the current revision can be edited")
    revision = store.get_revision(user.id, draft_id, revision_id)
    artifact = next(item for item in revision.artifacts if item.kind == "download")
    if artifact.media_type not in _FORMATS:
        raise ComposerRequestError("Revision download is not an Office document")
    name, kind, _apply = _FORMATS[artifact.media_type]
    limits = typed_docx_limits(dependencies)
    content = store.read_artifact(user.id, draft_id, revision_id, "download")
    eligible = {item.target: item.text for item in inspect_text(content, limits, kind)}
    if len(set(targets)) != len(targets):
        raise ComposerRequestError("A target was selected more than once")
    missing = tuple(target for target in targets if target not in eligible)
    if missing:
        raise kind.error("unsupported_target", missing[:1])
    return OfficeEditContext(
        name,
        revision_id,
        artifact.sha256,
        tuple((target, eligible[target]) for target in targets),
        limits.max_text_characters,
    )


def _replayed_step(  # noqa: PLR0913, PLR0917 - exact idempotency identity
    dependencies: HttpDependencies,
    user: User,
    draft_id: UUID,
    payload: ComposerOfficeEditStepCreateRequest,
    if_match: str,
    key: str,
    digest: str,
) -> ComposerModelStep | None:
    """Answer a retried start from durable state, before the draft has moved on."""

    steps = dependencies.components.composer_model_step_repository
    if steps is None:
        raise ComposerUnavailableError
    step = steps.replay_model_step(
        user.id,
        draft_id,
        idempotency_key=key,
        connection_id=payload.connection_id,
        approved_endpoint=payload.approved_endpoint,
        approved_model=payload.approved_model,
        if_match=if_match,
        payload_digest=digest,
        intent=OFFICE_EDIT_INTENT,
    )
    if step is not None:
        connections = dependencies.components.composer_connections
        if connections is None:
            raise ComposerUnavailableError
        connections.get_visible(_actor(dependencies, user), payload.connection_id)
    return step


def _authorized_connection(
    dependencies: HttpDependencies,
    user: User,
    payload: ComposerOfficeEditStepPreviewRequest,
) -> None:
    connections = dependencies.components.composer_connections
    if connections is None:
        raise ComposerUnavailableError
    record = connections.get_visible(_actor(dependencies, user), payload.connection_id)
    if (
        not record.enabled
        or record.endpoint != payload.approved_endpoint
        or record.selected_model != payload.approved_model
        or payload.approved_model not in record.permitted_models
    ):
        raise ComposerConflictError("Connection changed before preview")


def _prompt(
    dependencies: HttpDependencies, instruction: str, context: OfficeEditContext
) -> tuple[str, str]:
    try:
        transmitted, digest = compose_office_prompt(instruction, context)
    except OfficeEditProposalError:
        raise ComposerRequestError("Model instruction is empty") from None
    limit = dependencies.settings.composer_maximum_request_bytes
    if limit is None or len(transmitted.encode("utf-8")) > limit:
        raise ComposerRequestError("Model prompt exceeds the configured limit")
    return transmitted, digest


def _decided(
    dependencies: HttpDependencies, proposal: ComposerProposal
) -> OfficeEditProposal:
    if (
        proposal.state not in {ProposalState.ACCEPTED, ProposalState.EDITED}
        or proposal.decided_value is None
    ):
        raise ComposerRequestError("Proposal has not been approved")
    try:
        return parse_proposal(
            proposal.decided_value,
            typed_docx_limits(dependencies).max_text_characters,
        )
    except OfficeEditProposalError:
        raise ComposerConflictError("Composer proposal requires review again") from None


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915 - three explicit routes
    """Expose exact Office disclosure, bounded model steps and reviewed publication."""

    router = APIRouter()
    revision_path = "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}"

    @router.post(
        f"{revision_path}/office-edit-steps/preview",
        response_model=ComposerOfficeEditStepPreviewResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    def preview_office_edit_step(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        revision_id: UUID,
        payload: ComposerOfficeEditStepPreviewRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ComposerOfficeEditStepPreviewResponse:
        """Show the exact text a model step would transmit, without sending it."""

        if if_match is None:
            raise ComposerPreconditionRequiredError
        _authorized_connection(dependencies, user, payload)
        context = _office_context(
            dependencies, user, draft_id, revision_id, payload.targets, if_match
        )
        transmitted, digest = _prompt(dependencies, payload.instruction, context)
        response.headers["Cache-Control"] = "private, no-store"
        return ComposerOfficeEditStepPreviewResponse(
            format="pptx" if context.format == "pptx" else "docx",
            base_revision_id=context.base_revision_id,
            base_sha256=context.base_sha256,
            transmitted_content=transmitted,
            targets=tuple(
                ComposerOfficeEditTargetText(target=target, text=text)
                for target, text in context.targets
            ),
            preview_digest=digest,
        )

    @router.post(
        f"{revision_path}/office-edit-steps",
        response_model=ComposerModelStepResponse,
        status_code=202,
        tags=["composer"],
        responses=capacity_error_responses(401, 403, 404, 412, 413, 422, 428, 503),
    )
    def start_office_edit_step(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        revision_id: UUID,
        payload: ComposerOfficeEditStepCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerModelStepResponse:
        """Transmit the previewed text once; the reply becomes a pending proposal."""

        runner = dependencies.components.composer_steps
        request_limit = dependencies.settings.composer_maximum_request_bytes
        output_limit = dependencies.settings.composer_maximum_output_tokens
        if runner is None or request_limit is None or output_limit is None:
            raise ComposerUnavailableError
        if payload.max_output_tokens > output_limit:
            raise ComposerRequestError
        etag, key = _step_headers(if_match, idempotency_key)
        digest = _payload_digest(payload, request_limit)
        response.headers["Cache-Control"] = "private, no-store"
        replayed = _replayed_step(
            dependencies, user, draft_id, payload, etag, key, digest
        )
        if replayed is not None:
            return _step_response(replayed)
        try:
            _authorized_connection(dependencies, user, payload)
            context = _office_context(
                dependencies, user, draft_id, revision_id, payload.targets, etag
            )
            transmitted, preview_digest = _prompt(
                dependencies, payload.instruction, context
            )
            if payload.preview_digest != preview_digest:
                raise ComposerConflictError("Office edit preview changed")
            step = runner.start(
                _actor(dependencies, user),
                draft_id,
                ModelStepInput(
                    connection_id=payload.connection_id,
                    if_match=etag,
                    idempotency_key=key,
                    approved_endpoint=payload.approved_endpoint,
                    approved_model=payload.approved_model,
                    content=transmitted,
                    max_output_tokens=payload.max_output_tokens,
                    payload_digest=digest,
                    intent=OFFICE_EDIT_INTENT,
                    office=context,
                ),
            )
        except ComposerConflictError:
            # An identical request may have been admitted, and even completed,
            # after the lookup above and moved the draft on. Answer from its
            # durable step; a different request under this key still fails.
            replayed = _replayed_step(
                dependencies, user, draft_id, payload, etag, key, digest
            )
            if replayed is None:
                raise
            return _step_response(replayed)
        return _step_response(step)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}/office-edits",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 413, 422, 428, 503),
    )
    def publish_office_edit_proposal(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        proposal_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        """Apply a human-approved model proposal to the exact revision it reviewed."""

        match, key = _require_headers(if_match, idempotency_key)
        store = _store(dependencies)
        operation = f"office_edit_proposal:{proposal_id}"
        replay = store.find_revision_by_key(user.id, draft_id, key)
        if replay is not None:
            if replay.snapshot.operation != operation:
                raise ComposerConflictError("Revision idempotency key was reused")
            revision: ComposerRevision = replay
        else:
            proposal = store.get_proposal(user.id, draft_id, proposal_id)
            step = office_edit_step(dependencies, user.id, draft_id, proposal)
            if step is None:
                raise ComposerRequestError("Proposal is not an Office edit")
            decided = _decided(dependencies, proposal)
            draft = store.get_draft(user.id, draft_id)
            if draft.current_revision_id != decided.base_revision_id:
                raise ComposerConflictError("Composer proposal requires review again")
            base = store.get_revision(user.id, draft_id, decided.base_revision_id)
            artifact = next(item for item in base.artifacts if item.kind == "download")
            media_type = _MEDIA_TYPES[decided.format]
            if (
                artifact.media_type != media_type
                or artifact.sha256 != decided.base_sha256
            ):
                raise ComposerConflictError("Composer proposal requires review again")
            _name, _kind, apply = _FORMATS[media_type]
            edited = apply(
                store.read_artifact(user.id, draft_id, base.id, "download"),
                decided.operations,
                typed_docx_limits(dependencies),
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
                    approved_values=proposal.decided_value or "",
                    render_options=direct_publish_lineage(
                        base.id,
                        base.snapshot.model_identity,
                        base.snapshot.render_options,
                    ),
                    model_identity=step.model_identity,
                    provenance=f"human:{proposal.state.value}:{proposal.provenance}",
                    operation=operation,
                ),
                artifacts=(
                    ArtifactContent("download", media_type, edited),
                    ArtifactContent("preview", media_type, edited),
                ),
                approved_proposal_id=proposal_id,
            )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    return router
