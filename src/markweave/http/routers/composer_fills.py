"""Reviewed typed DOCX fill plans and frozen, model-free regeneration."""

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.concurrency import run_in_threadpool

from markweave.auth.models import User
from markweave.composer.author_knowledge import (
    AuthorKnowledgeConflictError,
    AuthorKnowledgeNotFoundError,
)
from markweave.composer.docx_fill import (
    TYPED_DOCX_FILL_ENGINE_VERSION,
    fill_docx,
    validate_template,
)
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    RevisionSnapshot,
)
from markweave.composer.typed_templates import (
    TYPED_DOCX_SCHEMA_VERSION,
    TypedTemplateError,
    ValidatedTemplate,
    validate_values,
)
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import ComposerRevisionResponse
from markweave.http.composer_t91_schemas import (
    FillPlanCreateRequest,
    FillPlanListResponse,
    FillPlanResponse,
    FillPlanUpdateRequest,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.persistence.composer.fill_plans import FillPlan, SqlFillPlanRepository
from markweave.version import VERSION

from .composer_fill_templates import typed_docx_limits
from .composer_revisions import _revision_response

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126


def _store(dependencies: HttpDependencies) -> SqlFillPlanRepository:
    store = dependencies.components.composer_fill_plans
    if store is None:
        raise ComposerUnavailableError
    return store


def _header(value: str | None) -> str:
    if value is None:
        raise ComposerPreconditionRequiredError
    return value


def _key(value: str | None) -> str:
    if value is None:
        raise ComposerPreconditionRequiredError
    if (
        not value
        or len(value) > _MAX_IDEMPOTENCY_KEY_LENGTH
        or any(
            not _FIRST_VISIBLE_ASCII <= ord(item) <= _LAST_VISIBLE_ASCII
            for item in value
        )
    ):
        raise ComposerRequestError("Idempotency key is invalid")
    return value


def _response(plan: FillPlan) -> FillPlanResponse:
    return FillPlanResponse(
        id=plan.id,
        draft_id=plan.draft_id,
        source_revision_id=plan.source_revision_id,
        template_id=plan.template_id,
        template_version_id=plan.template_version_id,
        author_refs=tuple(
            {"id": str(id_), "version": version} for id_, version in plan.author_refs
        ),
        values=plan.values,
        provenance=plan.provenance,
        questions=plan.questions,
        state=plan.state,
        version=plan.version,
        etag=plan.etag,
        result_revision_id=plan.result_revision_id,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _template(
    dependencies: HttpDependencies,
    actor_id: UUID,
    template_id: UUID,
    version_id: UUID,
) -> ValidatedTemplate:
    templates = dependencies.components.composer_fill_templates
    if templates is None:
        raise ComposerUnavailableError
    version = templates.get_version(actor_id, template_id, version_id)
    if version.schema_version != TYPED_DOCX_SCHEMA_VERSION:
        raise TypedTemplateError("unsupported_schema_version")
    content = templates.download(actor_id, template_id, version_id)
    validated = validate_template(
        json.loads(version.schema_json), content, typed_docx_limits(dependencies)
    )
    if (
        validated.sha256 != version.docx_sha256
        or validated.schema.sha256 != version.schema_sha256
    ):
        raise ComposerConflictError("Typed template version changed")
    return validated


def _value_paths(values: dict[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, list):
            for index, row in enumerate(value):
                if isinstance(row, dict):
                    paths.update(
                        (f"{key}[{index}].{field}", item) for field, item in row.items()
                    )
        else:
            paths[key] = value
    return paths


def _paths(values: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_value_paths(values))


def _questions(
    missing: tuple[str, ...], ambiguous: tuple[str, ...]
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "path": path,
            "text": f"What value should be used for {path}?",
            "reason": reason,
        }
        for reason, paths in (("missing", missing), ("ambiguous", ambiguous))
        for path in paths
    )


def _propose_author_values(  # noqa: PLR0912 - bounded field and repeat proposals
    dependencies: HttpDependencies,
    actor_id: UUID,
    author_ids: tuple[UUID, ...],
    template: ValidatedTemplate,
    submitted: dict[str, Any],
) -> tuple[
    dict[str, Any], dict[str, Any], tuple[tuple[UUID, int], ...], tuple[str, ...]
]:
    authors = dependencies.components.composer_authors
    if authors is None:
        raise ComposerUnavailableError
    if len(set(author_ids)) != len(author_ids):
        raise ComposerRequestError("An author was selected more than once")
    try:
        records = tuple(authors.get(actor_id, author_id) for author_id in author_ids)
    except AuthorKnowledgeConflictError, AuthorKnowledgeNotFoundError:
        raise ComposerConflictError("Author access changed") from None
    values = dict(submitted)
    provenance = {path: {"kind": "supplied"} for path in _paths(values)}
    ambiguous: list[str] = []

    def fact(record: Any, key: str) -> tuple[object | None, dict[str, Any]]:
        if key == "name":
            return record.name, {"kind": "supplied"}
        field = record.fields.get(key)
        if field is None:
            return None, {"kind": "unresolved"}
        return field.value, {
            "kind": field.provenance,
            "source_reference": field.source_reference,
        }

    for field in template.schema.fields:
        if field.name in values or not field.name.startswith("author."):
            continue
        if len(records) != 1:
            if records:
                values[field.name] = None
                ambiguous.append(field.name)
            continue
        value, origin = fact(records[0], field.name.removeprefix("author."))
        if value is not None:
            values[field.name] = value
            provenance[field.name] = origin
            if origin["kind"] == "model_suggested":
                ambiguous.append(field.name)
        elif origin["kind"] == "unresolved":
            values[field.name] = None
            ambiguous.append(field.name)
    author_section = next(
        (section for section in template.schema.repeats if section.name == "authors"),
        None,
    )
    if author_section is not None and "authors" not in values and records:
        rows = []
        for index, record in enumerate(records):
            row: dict[str, Any] = {}
            for field in author_section.fields:
                value, origin = fact(record, field.name)
                if value is not None:
                    row[field.name] = value
                    path = f"authors[{index}].{field.name}"
                    provenance[path] = origin
                    if origin["kind"] == "model_suggested":
                        ambiguous.append(path)
            rows.append(row)
        values["authors"] = rows
    return (
        values,
        provenance,
        tuple((item.id, item.version) for item in records),
        tuple(ambiguous),
    )


def _require_complete_provenance(
    values: dict[str, Any], provenance: dict[str, Any]
) -> None:
    if set(_paths(values)) != set(provenance):
        raise ComposerRequestError("Fill provenance does not match values")


def _with_template_defaults(
    values: dict[str, Any], provenance: dict[str, Any], submitted: dict[str, Any]
) -> dict[str, Any]:
    """Attribute normalized schema defaults to the exact selected template."""

    submitted_paths = set(_paths(submitted))
    return {
        **{
            path: {
                "kind": "template_default",
                "source_reference": "typed_template_schema",
            }
            for path in _paths(values)
            if path not in provenance and path not in submitted_paths
        },
        **provenance,
    }


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
    """Bind reviewed values to immutable source and template versions."""

    router = APIRouter()

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/fill-plans",
        response_model=FillPlanResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def create_plan(  # noqa: PLR0913, PLR0917 - explicit revision and request inputs
        draft_id: UUID,
        payload: FillPlanCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> FillPlanResponse:
        composer = dependencies.components.composer_store
        if composer is None:
            raise ComposerUnavailableError
        source = composer.get_revision(user.id, draft_id, payload.source_revision_id)
        template = _template(
            dependencies, user.id, payload.template_id, payload.template_version_id
        )
        values, provenance, author_refs, extra_ambiguous = _propose_author_values(
            dependencies, user.id, payload.author_ids, template, payload.values
        )
        reviewed = validate_values(template, values)
        questions = _questions(
            reviewed.missing_paths,
            tuple(dict.fromkeys((*reviewed.ambiguous_paths, *extra_ambiguous))),
        )
        provenance = _with_template_defaults(reviewed.normalized, provenance, values)
        _require_complete_provenance(reviewed.normalized, provenance)
        if source.snapshot.source.owner_id != user.id:
            raise ComposerConflictError("Fill source changed")
        plan = _store(dependencies).create(
            user.id,
            draft_id,
            source_revision_id=payload.source_revision_id,
            template_version_id=payload.template_version_id,
            author_refs=author_refs,
            values=reviewed.normalized,
            provenance=provenance,
            questions=questions,
            if_match=_header(if_match),
            idempotency_key=_key(idempotency_key),
        )
        response.headers["ETag"] = plan.etag
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/fill-plans/{plan.id}"
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _response(plan)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/fill-plans",
        response_model=FillPlanListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def list_plans(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> FillPlanListResponse:
        plans = _store(dependencies).list_visible(
            user.id, draft_id, limit=limit, offset=offset
        )
        response.headers["Cache-Control"] = "private, no-store"
        return FillPlanListResponse(
            plans=tuple(_response(item) for item in plans), limit=limit, offset=offset
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/fill-plans/{plan_id}",
        response_model=FillPlanResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 412, 503),
    )
    def get_plan(
        draft_id: UUID,
        plan_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> FillPlanResponse:
        plan = _store(dependencies).get(user.id, draft_id, plan_id)
        response.headers["ETag"] = plan.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(plan)

    @router.patch(
        "/api/v1/composer/drafts/{draft_id}/fill-plans/{plan_id}",
        response_model=FillPlanResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def update_plan(  # noqa: PLR0913, PLR0917 - explicit decision inputs
        draft_id: UUID,
        plan_id: UUID,
        payload: FillPlanUpdateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> FillPlanResponse:
        old = _store(dependencies).get(user.id, draft_id, plan_id)
        template = _template(
            dependencies, user.id, old.template_id, old.template_version_id
        )
        reviewed = validate_values(template, payload.values)
        provenance = {
            path: item.model_dump(mode="json")
            for path, item in payload.provenance.items()
        }
        provenance = _with_template_defaults(
            reviewed.normalized, provenance, payload.values
        )
        previous_values = _value_paths(old.values)
        next_values = _value_paths(reviewed.normalized)
        for path, origin in old.provenance.items():
            if origin.get("kind") in {"human_approved", "human_edited"}:
                if path not in next_values or path not in provenance:
                    raise ComposerConflictError("Human-approved value requires review")
                if provenance[path]["kind"] not in {
                    "human_approved",
                    "human_edited",
                }:
                    raise ComposerConflictError("Human-approved value requires review")
                if (
                    next_values[path] != previous_values[path]
                    or provenance[path] != origin
                ) and provenance[path]["kind"] != "human_edited":
                    raise ComposerConflictError("Changed human value requires an edit")
        _require_complete_provenance(reviewed.normalized, provenance)
        questions = _questions(reviewed.missing_paths, reviewed.ambiguous_paths)
        plan = _store(dependencies).decide(
            user.id,
            draft_id,
            plan_id,
            if_match=_header(if_match),
            idempotency_key=_key(idempotency_key),
            values=reviewed.normalized,
            provenance=provenance,
            questions=questions,
            approve=False,
        )
        response.headers["ETag"] = plan.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(plan)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/fill-plans/{plan_id}/approve",
        response_model=FillPlanResponse,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    def approve_plan(  # noqa: PLR0913, PLR0917 - explicit decision inputs
        draft_id: UUID,
        plan_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> FillPlanResponse:
        current = _store(dependencies).get(user.id, draft_id, plan_id)
        template = _template(
            dependencies, user.id, current.template_id, current.template_version_id
        )
        reviewed = validate_values(template, current.values)
        if current.questions or reviewed.missing_paths or reviewed.ambiguous_paths:
            raise ComposerConflictError("Fill plan has unanswered questions")
        provenance = {
            path: {
                **origin,
                "kind": "human_edited"
                if origin.get("kind") == "human_edited"
                else "human_approved",
            }
            for path, origin in current.provenance.items()
        }
        _require_complete_provenance(reviewed.normalized, provenance)
        plan = _store(dependencies).decide(
            user.id,
            draft_id,
            plan_id,
            if_match=_header(if_match),
            idempotency_key=_key(idempotency_key),
            values=current.values,
            provenance=provenance,
            questions=(),
            approve=True,
        )
        response.headers["ETag"] = plan.etag
        response.headers["Cache-Control"] = "private, no-store"
        return _response(plan)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/fill-plans/{plan_id}/publish",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    async def publish_plan(  # noqa: PLR0913, PLR0917 - explicit publication inputs
        draft_id: UUID,
        plan_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        submitted_etag = _header(if_match)
        key = _key(idempotency_key)
        composer = dependencies.components.composer_store
        if composer is None:
            raise ComposerUnavailableError

        def committed_receipt() -> ComposerRevisionResponse:
            committed = composer.find_revision_by_key(user.id, draft_id, key)
            if committed is None or committed.snapshot.operation != "fill_template":
                raise ComposerConflictError("Fill plan changed")
            if committed.snapshot.typed_fill_snapshot is None:
                raise ComposerConflictError("Fill plan changed")
            frozen_receipt = json.loads(committed.snapshot.typed_fill_snapshot)
            if (
                frozen_receipt.get("fill_plan_id") != str(plan_id)
                or submitted_etag != f'"{frozen_receipt.get("fill_plan_version")}"'
            ):
                raise ComposerConflictError("Fill plan changed")
            _store(dependencies).mark_published(
                user.id, draft_id, plan_id, committed.id
            )
            response.headers["ETag"] = composer.get_draft(user.id, draft_id).etag
            response.headers["Location"] = (
                f"/api/v1/composer/drafts/{draft_id}/revisions/{committed.id}"
            )
            response.headers["Cache-Control"] = "private, no-store"
            return _revision_response(committed)

        try:
            plan = _store(dependencies).get(user.id, draft_id, plan_id)
        except ComposerConflictError:
            # A revision may have committed before the separate plan-link transaction.
            # Recover its exact receipt without disclosing a newly revoked author record.
            return committed_receipt()
        if plan.state == "published":
            return committed_receipt()
        if submitted_etag != plan.etag or plan.state != "approved":
            raise ComposerConflictError("Fill plan changed")
        source = composer.get_revision(user.id, draft_id, plan.source_revision_id)
        template = _template(
            dependencies, user.id, plan.template_id, plan.template_version_id
        )
        reviewed = validate_values(template, plan.values)
        if plan.questions or reviewed.missing_paths or reviewed.ambiguous_paths:
            raise ComposerConflictError("Fill plan has unanswered questions")
        content = await run_in_threadpool(
            fill_docx, template, reviewed, typed_docx_limits(dependencies)
        )
        result_digest = hashlib.sha256(content).hexdigest()
        frozen = {
            "schema_version": TYPED_DOCX_SCHEMA_VERSION,
            "source_revision_id": str(plan.source_revision_id),
            "source_sha256": source.snapshot.source.sha256,
            "template_id": str(plan.template_id),
            "template_version_id": str(plan.template_version_id),
            "template_docx_sha256": plan.template_docx_sha256,
            "template_schema_sha256": plan.template_schema_sha256,
            "approved_values": reviewed.normalized,
            "provenance": plan.provenance,
            "author_refs": [
                {"id": str(id_), "version": version}
                for id_, version in plan.author_refs
            ],
            "actor_id": str(user.id),
            "component_versions": {
                "markweave": VERSION,
                "typed_docx_schema": TYPED_DOCX_SCHEMA_VERSION,
                "typed_docx_fill_engine": TYPED_DOCX_FILL_ENGINE_VERSION,
            },
            "result_sha256": result_digest,
            "fill_plan_id": str(plan.id),
            "fill_plan_version": plan.version,
        }
        revision = composer.publish_revision(
            user.id,
            draft_id,
            actor_id=user.id,
            if_match=f'"{plan.draft_version}"',
            idempotency_key=key,
            snapshot=RevisionSnapshot(
                source=source.snapshot.source,
                template_reference=None,
                approved_values="{}",
                render_options=json.dumps(
                    frozen["component_versions"], sort_keys=True, separators=(",", ":")
                ),
                model_identity=None,
                provenance="human:typed_fill",
                operation="fill_template",
                typed_fill_snapshot=json.dumps(
                    frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ),
            ),
            artifacts=(
                ArtifactContent("download", _DOCX_MEDIA, content),
                ArtifactContent("preview", _DOCX_MEDIA, content),
            ),
        )
        _store(dependencies).mark_published(user.id, draft_id, plan_id, revision.id)
        response.headers["ETag"] = composer.get_draft(user.id, draft_id).etag
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/regenerations",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 403, 404, 412, 422, 428, 503),
    )
    async def regenerate(  # noqa: PLR0913, PLR0917 - exact immutable source inputs
        draft_id: UUID,
        revision_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        composer = dependencies.components.composer_store
        if composer is None:
            raise ComposerUnavailableError
        prior = composer.get_revision(user.id, draft_id, revision_id)
        if prior.snapshot.typed_fill_snapshot is None:
            raise ComposerRequestError("Revision has no frozen typed fill")
        frozen = json.loads(prior.snapshot.typed_fill_snapshot)
        if frozen.get("component_versions") != {
            "markweave": VERSION,
            "typed_docx_schema": TYPED_DOCX_SCHEMA_VERSION,
            "typed_docx_fill_engine": TYPED_DOCX_FILL_ENGINE_VERSION,
        }:
            raise ComposerConflictError("Frozen rendering components changed")
        template = _template(
            dependencies,
            user.id,
            UUID(frozen["template_id"]),
            UUID(frozen["template_version_id"]),
        )
        reviewed = validate_values(template, frozen["approved_values"])
        if reviewed.missing_paths or reviewed.ambiguous_paths:
            raise ComposerConflictError("Frozen values are incomplete")
        content = await run_in_threadpool(
            fill_docx, template, reviewed, typed_docx_limits(dependencies)
        )
        if hashlib.sha256(content).hexdigest() != frozen["result_sha256"]:
            raise ComposerConflictError("Frozen rendering components changed")
        regenerated = {
            **frozen,
            "parent_revision_id": str(revision_id),
            "actor_id": str(user.id),
        }
        revision = composer.publish_revision(
            user.id,
            draft_id,
            actor_id=user.id,
            if_match=_header(if_match),
            idempotency_key=_key(idempotency_key),
            snapshot=RevisionSnapshot(
                source=prior.snapshot.source,
                template_reference=None,
                approved_values="{}",
                render_options=prior.snapshot.render_options,
                model_identity=None,
                provenance="human:frozen_regeneration",
                operation="regenerate_fill",
                typed_fill_snapshot=json.dumps(
                    regenerated,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            ),
            artifacts=(
                ArtifactContent("download", _DOCX_MEDIA, content),
                ArtifactContent("preview", _DOCX_MEDIA, content),
            ),
        )
        response.headers["ETag"] = composer.get_draft(user.id, draft_id).etag
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    return router
