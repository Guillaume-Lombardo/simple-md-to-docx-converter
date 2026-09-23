"""Queue approved Composer Markdown through the existing conversion workers."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response

from markweave.auth.models import User
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerRevision,
    RevisionSnapshot,
)
from markweave.composer.sources import repack_approved_markdown
from markweave.http.composer_errors import (
    ComposerPreconditionRequiredError,
    ComposerRequestError,
    ComposerUnavailableError,
)
from markweave.http.composer_schemas import (
    ComposerArtifactResponse,
    ComposerGenerationCreateRequest,
    ComposerGenerationListResponse,
    ComposerGenerationResponse,
    ComposerRevisionResponse,
)
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import error_responses
from markweave.jobs.errors import JobConflictError, JobNotFoundError
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobRequest,
    JobState,
    SourceKind,
)
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.composer.generations import ComposerGeneration
from markweave.presentations.models import PresentationDialect, PresentationOptions

from .conversions import COMPONENT_VERSIONS

_RESULT_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126
_MAXIMUM_OFFSET = 2_147_483_647
_COMPONENT_PAIR_LENGTH = 2


def _store(dependencies: HttpDependencies) -> SqlComposerRepository:
    store = dependencies.components.composer_store
    if store is None:
        raise ComposerUnavailableError
    return store


def _headers(if_match: str | None, key: str | None) -> tuple[str, str]:
    if if_match is None or key is None:
        raise ComposerPreconditionRequiredError
    if (
        not key
        or len(key) > _MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS
        or any(
            not _FIRST_VISIBLE_ASCII <= ord(character) <= _LAST_VISIBLE_ASCII
            for character in key
        )
    ):
        raise ComposerRequestError
    return if_match, key


def _options(payload: ComposerGenerationCreateRequest) -> PresentationOptions | None:
    if payload.output != "pptx":
        return None
    return PresentationOptions(
        payload.presentation_dialect or PresentationDialect.AUTO,
        payload.slide_level if payload.slide_level is not None else 2,
    )


def _generation_response(
    dependencies: HttpDependencies,
    owner_id: UUID,
    generation: ComposerGeneration,
) -> ComposerGenerationResponse:
    jobs = dependencies.components.jobs
    store = _store(dependencies)
    if jobs is None:
        raise ComposerUnavailableError
    status = "submitting"
    if generation.job_id is not None:
        try:
            job = jobs.get_visible(
                generation.job_id, actor_id=owner_id, actor_is_admin=False
            )
            status = job.state.value
        except JobNotFoundError:
            status = "expired"
    draft = store.get_draft(owner_id, generation.draft_id)
    publishable = (
        status == JobState.SUCCEEDED.value
        and generation.result_revision_id is None
        and draft.version == generation.expected_draft_version
        and draft.current_revision_id == generation.source_revision_id
    )
    if generation.result_revision_id is not None:
        status = "published"
    options = (
        PresentationOptions.from_json(generation.presentation_options)
        if generation.presentation_options
        else None
    )
    return ComposerGenerationResponse(
        id=generation.id,
        draft_id=generation.draft_id,
        source_revision_id=generation.source_revision_id,
        job_id=generation.job_id,
        status=status,
        output=(
            "docx"
            if generation.output == "docx"
            else "pdf"
            if generation.output == "pdf"
            else "pptx"
        ),
        template_id=generation.template_id,
        template_version_id=generation.template_version_id,
        presentation_dialect=options.dialect if options else None,
        slide_level=options.slide_level if options else None,
        result_revision_id=generation.result_revision_id,
        publishable=publishable,
        created_at=generation.created_at,
    )


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


def _input_source(
    dependencies: HttpDependencies,
    owner_id: UUID,
    draft_id: UUID,
    revision: ComposerRevision,
) -> tuple[bytes, str, SourceKind, bytes]:
    store = _store(dependencies)
    artifact = next(
        (item for item in revision.artifacts if item.kind == "download"), None
    )
    if artifact is None or artifact.media_type != "text/markdown":
        raise ComposerRequestError("Generation requires approved Markdown")
    markdown = store.read_artifact(owner_id, draft_id, revision.id, "download")
    if (
        not markdown
        or len(markdown) > dependencies.settings.conversion_upload_max_bytes
    ):
        raise ComposerRequestError("Approved Markdown exceeds the conversion limit")
    if revision.snapshot.source.media_type != "application/zip":
        return markdown, "source.md", SourceKind.MARKDOWN, markdown
    asset = next((item for item in revision.artifacts if item.kind == "source"), None)
    if asset is None or asset.media_type != "application/zip":
        raise ComposerRequestError("Approved source assets are unavailable")
    source_archive = store.read_artifact(owner_id, draft_id, revision.id, "source")
    frozen = repack_approved_markdown(
        markdown,
        source_archive,
        dependencies.settings,
        reversion_result=revision.snapshot.source.kind == "reversion_result",
    )
    return frozen, "source.zip", SourceKind.ARCHIVE, markdown


def _generation_render_options(
    generation: ComposerGeneration,
    source: ComposerRevision,
    job_id: UUID,
    content: bytes,
) -> str:
    try:
        source_options = json.loads(source.snapshot.render_options)
    except ValueError, TypeError:
        source_options = {}
    if not isinstance(source_options, dict):
        source_options = {}
    return json.dumps(
        {
            "source_revision_id": str(source.id),
            "job_id": str(job_id),
            "input_sha256": generation.input_sha256,
            "result_sha256": hashlib.sha256(content).hexdigest(),
            "output": generation.output,
            "presentation_options": generation.presentation_options,
            "component_versions": generation.component_versions,
            "parent_approved_revision_id": source_options.get("parent_revision_id"),
            "lineage_model_identity": source_options.get("lineage_model_identity"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _frozen_components(value: str) -> tuple[tuple[str, str], ...]:
    try:
        decoded = json.loads(value)
    except ValueError, TypeError:
        raise ComposerConflictError(
            "Generation component versions are invalid"
        ) from None
    if (
        not isinstance(decoded, list)
        or not decoded
        or any(
            not isinstance(item, list)
            or len(item) != _COMPONENT_PAIR_LENGTH
            or not all(isinstance(part, str) and part for part in item)
            for item in decoded
        )
    ):
        raise ComposerConflictError("Generation component versions are invalid")
    return tuple((item[0], item[1]) for item in decoded)


def _frozen_options(generation: ComposerGeneration) -> PresentationOptions | None:
    if generation.presentation_options is None:
        return None
    try:
        return PresentationOptions.from_json(generation.presentation_options)
    except ValueError:
        raise ComposerConflictError(
            "Generation presentation options are invalid"
        ) from None


def _verify_job_identity(
    job: ConversionJob,
    generation: ComposerGeneration,
    *,
    source_kind: SourceKind,
) -> None:
    if (
        job.owner_id != generation.owner_id
        or job.source_sha256 != generation.input_sha256
        or job.source_kind != source_kind
        or job.source_filename
        != ("source.zip" if source_kind is SourceKind.ARCHIVE else "source.md")
        or job.output.value != generation.output
        or job.template_id != generation.template_id
        or job.template_version_id != generation.template_version_id
        or job.component_versions != _frozen_components(generation.component_versions)
        or job.presentation_options != _frozen_options(generation)
    ):
        raise ComposerConflictError("Generation job identity changed")


def build_router(dependencies: HttpDependencies) -> APIRouter:  # noqa: PLR0915
    router = APIRouter()

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/revisions/{source_revision_id}/generations",
        response_model=ComposerGenerationResponse,
        status_code=202,
        tags=["composer"],
        responses=error_responses(401, 404, 409, 412, 413, 422, 428, 503),
    )
    def create_generation(  # noqa: PLR0913, PLR0917 - explicit HTTP preconditions
        draft_id: UUID,
        source_revision_id: UUID,
        payload: ComposerGenerationCreateRequest,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerGenerationResponse:
        match, key = _headers(if_match, idempotency_key)
        store = _store(dependencies)
        jobs = dependencies.components.jobs
        scanner = dependencies.components.scanner
        if jobs is None or scanner is None:
            raise ComposerUnavailableError
        revision = store.get_revision(user.id, draft_id, source_revision_id)
        source, filename, source_kind, markdown = _input_source(
            dependencies, user.id, draft_id, revision
        )
        scanner.scan(source)
        options = _options(payload)
        source_digest = hashlib.sha256(source).hexdigest()
        markdown_digest = hashlib.sha256(markdown).hexdigest()
        request_digest = hashlib.sha256(
            json.dumps(
                {
                    "source_revision_id": str(source_revision_id),
                    "markdown_sha256": markdown_digest,
                    "input_sha256": source_digest,
                    "options": payload.model_dump(mode="json"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        generation = store.reserve_generation(
            user.id,
            draft_id,
            source_revision_id,
            if_match=match,
            idempotency_key=key,
            request_digest=request_digest,
            approved_markdown_sha256=markdown_digest,
            input_sha256=source_digest,
            output=payload.output,
            template_id=payload.template_id,
            template_version_id=payload.template_version_id,
            presentation_options=options.canonical_json() if options else None,
            component_versions=json.dumps(COMPONENT_VERSIONS),
        )
        if (
            generation.input_sha256 != source_digest
            or generation.approved_markdown_sha256 != markdown_digest
        ):
            raise ComposerConflictError("Approved generation input changed")
        if generation.job_id is not None:
            response.headers["Location"] = (
                f"/api/v1/composer/drafts/{draft_id}/generations/{generation.id}"
            )
            response.headers["Retry-After"] = str(
                dependencies.settings.conversion_retry_after_seconds
            )
            response.headers["Cache-Control"] = "private, no-store"
            return _generation_response(dependencies, user.id, generation)
        component_versions = _frozen_components(generation.component_versions)
        if component_versions != COMPONENT_VERSIONS:
            raise ComposerConflictError("Generation engine versions changed")
        frozen_options = _frozen_options(generation)
        job, _replayed = jobs.submit(
            JobRequest(
                owner_id=user.id,
                source=source,
                template_id=generation.template_id,
                template_version_id=generation.template_version_id,
                output=JobOutput(generation.output),
                component_versions=component_versions,
                now=datetime.now(UTC),
                source_filename=filename,
                source_kind=source_kind,
                presentation_options=frozen_options,
            ),
            f"composer-generation:{generation.id}",
        )
        _verify_job_identity(
            job,
            generation,
            source_kind=source_kind,
        )
        generation = store.attach_generation_job(
            user.id, draft_id, generation.id, job.id
        )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/generations/{generation.id}"
        )
        response.headers["Retry-After"] = str(
            dependencies.settings.conversion_retry_after_seconds
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _generation_response(dependencies, user.id, generation)

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/generations",
        response_model=ComposerGenerationListResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 422, 503),
    )
    def list_generations(
        draft_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=_MAXIMUM_OFFSET)] = 0,
    ) -> ComposerGenerationListResponse:
        response.headers["Cache-Control"] = "private, no-store"
        generations = _store(dependencies).list_generations(
            user.id, draft_id, limit=limit, offset=offset
        )
        return ComposerGenerationListResponse(
            generations=tuple(
                _generation_response(dependencies, user.id, item)
                for item in generations
            ),
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/v1/composer/drafts/{draft_id}/generations/{generation_id}",
        response_model=ComposerGenerationResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 503),
    )
    def get_generation(
        draft_id: UUID,
        generation_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.current_user)],
    ) -> ComposerGenerationResponse:
        response.headers["Cache-Control"] = "private, no-store"
        generation = _store(dependencies).get_generation(
            user.id, draft_id, generation_id
        )
        return _generation_response(dependencies, user.id, generation)

    @router.delete(
        "/api/v1/composer/drafts/{draft_id}/generations/{generation_id}",
        response_model=ComposerGenerationResponse,
        tags=["composer"],
        responses=error_responses(401, 404, 409, 503),
    )
    def cancel_generation(
        draft_id: UUID,
        generation_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
    ) -> ComposerGenerationResponse:
        generation = _store(dependencies).get_generation(
            user.id, draft_id, generation_id
        )
        jobs = dependencies.components.jobs
        if jobs is None:
            raise ComposerUnavailableError
        if generation.job_id is None:
            raise JobConflictError("Generation job is not available")
        jobs.cancel(
            generation.job_id,
            actor_id=user.id,
            actor_is_admin=False,
            now=datetime.now(UTC),
        )
        response.headers["Cache-Control"] = "private, no-store"
        return _generation_response(dependencies, user.id, generation)

    @router.post(
        "/api/v1/composer/drafts/{draft_id}/generations/{generation_id}/publish",
        response_model=ComposerRevisionResponse,
        status_code=201,
        tags=["composer"],
        responses=error_responses(401, 404, 409, 412, 422, 428, 503),
    )
    def publish_generation(  # noqa: PLR0912, PLR0913, PLR0917 - explicit HTTP preconditions and publication fences
        draft_id: UUID,
        generation_id: UUID,
        response: Response,
        user: Annotated[User, Depends(dependencies.mutation_actor)],
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ComposerRevisionResponse:
        match, key = _headers(if_match, idempotency_key)
        store = _store(dependencies)
        jobs = dependencies.components.jobs
        if jobs is None:
            raise ComposerUnavailableError
        generation = store.get_generation(user.id, draft_id, generation_id)
        if generation.result_revision_id is not None:
            if generation.publication_key != key:
                raise ComposerConflictError("Generation was already published")
            revision = store.get_revision(
                user.id, draft_id, generation.result_revision_id
            )
        else:
            recovered = store.find_revision_by_key(user.id, draft_id, key)
            if recovered is not None:
                if recovered.snapshot.operation != f"generate:{generation_id}":
                    raise ComposerConflictError("Generation publication key was reused")
                revision = recovered
            else:
                draft = store.get_draft(user.id, draft_id)
                if (
                    match != f'"{generation.expected_draft_version}"'
                    or draft.version != generation.expected_draft_version
                    or draft.current_revision_id != generation.source_revision_id
                ):
                    raise ComposerConflictError("Composer draft changed")
                if generation.job_id is None:
                    raise JobConflictError("Generation job is not available")
                job, content = jobs.download(
                    generation.job_id, actor_id=user.id, actor_is_admin=False
                )
                source_kind = (
                    SourceKind.ARCHIVE
                    if store.get_revision(
                        user.id, draft_id, generation.source_revision_id
                    ).snapshot.source.media_type
                    == "application/zip"
                    else SourceKind.MARKDOWN
                )
                _verify_job_identity(
                    job,
                    generation,
                    source_kind=source_kind,
                )
                if job.state is not JobState.SUCCEEDED or not content:
                    raise ComposerConflictError("Generation result identity changed")
                if generation.output == "pdf":
                    if not content.startswith(b"%PDF-"):
                        raise ComposerConflictError("Generation result is invalid")
                elif not content.startswith(b"PK"):
                    raise ComposerConflictError("Generation result is invalid")
                source = store.get_revision(
                    user.id, draft_id, generation.source_revision_id
                )
                approved = store.read_artifact(user.id, draft_id, source.id, "download")
                if (
                    hashlib.sha256(approved).hexdigest()
                    != generation.approved_markdown_sha256
                ):
                    raise ComposerConflictError("Approved Markdown changed")
                artifacts = [
                    ArtifactContent(
                        "download", _RESULT_MEDIA[generation.output], content
                    ),
                    ArtifactContent(
                        "preview", _RESULT_MEDIA[generation.output], content
                    ),
                ]
                if any(item.kind == "source" for item in source.artifacts):
                    artifacts.insert(
                        0,
                        ArtifactContent(
                            "source",
                            "application/zip",
                            store.read_artifact(user.id, draft_id, source.id, "source"),
                        ),
                    )
                if generation.output == "pdf":
                    _job, manifest = jobs.download_manifest(
                        generation.job_id, actor_id=user.id, actor_is_admin=False
                    )
                    artifacts.append(
                        ArtifactContent("traceability", "application/json", manifest)
                    )
                template_reference = (
                    json.dumps(
                        {
                            "template_id": str(generation.template_id),
                            "template_version_id": str(generation.template_version_id),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if generation.template_id is not None
                    else None
                )
                revision = store.publish_revision(
                    user.id,
                    draft_id,
                    actor_id=user.id,
                    if_match=match,
                    idempotency_key=key,
                    snapshot=RevisionSnapshot(
                        source=source.snapshot.source,
                        template_reference=template_reference,
                        approved_values=source.snapshot.approved_values,
                        render_options=_generation_render_options(
                            generation, source, job.id, content
                        ),
                        model_identity=source.snapshot.model_identity,
                        provenance=f"generation:{generation.id}:source:{source.id}",
                        operation=f"generate:{generation.id}",
                    ),
                    artifacts=tuple(artifacts),
                )
            store.attach_generation_revision(
                user.id, draft_id, generation_id, revision.id, key
            )
        response.headers["Location"] = (
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision.id}"
        )
        response.headers["ETag"] = store.get_draft(user.id, draft_id).etag
        response.headers["Cache-Control"] = "private, no-store"
        return _revision_response(revision)

    return router
