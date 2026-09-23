"""Durable owner-bound links between approved Markdown and queued conversion jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.persistence.composer.common import owned_draft, utc, validate_page
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerGenerationRow,
    ComposerRevisionRow,
)
from markweave.persistence.sql import serialize_sqlite_write

_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128


@dataclass(frozen=True, slots=True)
class ComposerGeneration:
    id: UUID
    draft_id: UUID
    owner_id: UUID
    source_revision_id: UUID
    expected_draft_version: int
    approved_markdown_sha256: str
    input_sha256: str
    output: str
    template_id: UUID | None
    template_version_id: UUID | None
    presentation_options: str | None
    component_versions: str
    job_id: UUID | None
    result_revision_id: UUID | None
    publication_key: str | None
    created_at: datetime


def _generation(row: ComposerGenerationRow) -> ComposerGeneration:
    return ComposerGeneration(
        id=UUID(row.id),
        draft_id=UUID(row.draft_id),
        owner_id=UUID(row.owner_id),
        source_revision_id=UUID(row.source_revision_id),
        expected_draft_version=row.expected_draft_version,
        approved_markdown_sha256=row.approved_markdown_sha256,
        input_sha256=row.input_sha256,
        output=row.output,
        template_id=UUID(row.template_id) if row.template_id else None,
        template_version_id=UUID(row.template_version_id)
        if row.template_version_id
        else None,
        presentation_options=row.presentation_options,
        component_versions=row.component_versions,
        job_id=UUID(row.job_id) if row.job_id else None,
        result_revision_id=UUID(row.result_revision_id)
        if row.result_revision_id
        else None,
        publication_key=row.publication_key,
        created_at=utc(row.created_at),
    )


class _SqlComposerGenerations:
    _engine: Engine
    _clock: Callable[[], datetime]
    _new_id: Callable[[], UUID]

    def reserve_generation(  # noqa: PLR0913 - immutable job request identity
        self,
        owner_id: UUID,
        draft_id: UUID,
        source_revision_id: UUID,
        *,
        if_match: str,
        idempotency_key: str,
        request_digest: str,
        approved_markdown_sha256: str,
        input_sha256: str,
        output: str,
        template_id: UUID | None,
        template_version_id: UUID | None,
        presentation_options: str | None,
        component_versions: str,
    ) -> ComposerGeneration:
        if (
            not idempotency_key
            or len(idempotency_key) > _MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS
        ):
            raise ValueError("Generation idempotency key is invalid")
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                existing = database.scalar(
                    select(ComposerGenerationRow).where(
                        ComposerGenerationRow.draft_id == str(draft_id),
                        ComposerGenerationRow.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_digest != request_digest:
                        raise ComposerConflictError(
                            "Generation idempotency key was reused"
                        )
                    return _generation(existing)
                if if_match != f'"{draft.version}"' or draft.current_revision_id != str(
                    source_revision_id
                ):
                    raise ComposerConflictError("Composer draft changed")
                source = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.id == str(source_revision_id),
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.publication_state == "published",
                    )
                )
                artifact = database.scalar(
                    select(ComposerArtifactRow).where(
                        ComposerArtifactRow.revision_id == str(source_revision_id),
                        ComposerArtifactRow.kind == "download",
                    )
                )
                if (
                    source is None
                    or not (
                        source.operation == "publish_draft"
                        or source.operation.startswith("publish_proposal:")
                    )
                    or artifact is None
                    or artifact.media_type != "text/markdown"
                    or artifact.sha256 != approved_markdown_sha256
                ):
                    raise ComposerConflictError(
                        "Generation requires the current approved Markdown revision"
                    )
                row = ComposerGenerationRow(
                    id=str(self._new_id()),
                    draft_id=str(draft_id),
                    owner_id=str(owner_id),
                    source_revision_id=str(source_revision_id),
                    expected_draft_version=draft.version,
                    approved_markdown_sha256=approved_markdown_sha256,
                    input_sha256=input_sha256,
                    output=output,
                    template_id=str(template_id) if template_id else None,
                    template_version_id=str(template_version_id)
                    if template_version_id
                    else None,
                    presentation_options=presentation_options,
                    component_versions=component_versions,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    job_id=None,
                    result_revision_id=None,
                    publication_key=None,
                    created_at=self._clock(),
                )
                database.add(row)
                database.flush()
                return _generation(row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Generation changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_generation(
        self, owner_id: UUID, draft_id: UUID, generation_id: UUID
    ) -> ComposerGeneration:
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                row = database.scalar(
                    select(ComposerGenerationRow).where(
                        ComposerGenerationRow.id == str(generation_id),
                        ComposerGenerationRow.draft_id == str(draft_id),
                        ComposerGenerationRow.owner_id == str(owner_id),
                    )
                )
                if row is None:
                    raise ComposerNotFoundError("Composer generation does not exist")
                return _generation(row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_generations(
        self, owner_id: UUID, draft_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[ComposerGeneration, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                return tuple(
                    _generation(row)
                    for row in database.scalars(
                        select(ComposerGenerationRow)
                        .where(ComposerGenerationRow.draft_id == str(draft_id))
                        .order_by(
                            ComposerGenerationRow.created_at.desc(),
                            ComposerGenerationRow.id.desc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def attach_generation_job(
        self, owner_id: UUID, draft_id: UUID, generation_id: UUID, job_id: UUID
    ) -> ComposerGeneration:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                owned_draft(database, owner_id, draft_id, lock=True)
                row = database.scalar(
                    select(ComposerGenerationRow)
                    .where(
                        ComposerGenerationRow.id == str(generation_id),
                        ComposerGenerationRow.draft_id == str(draft_id),
                        ComposerGenerationRow.owner_id == str(owner_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise ComposerNotFoundError("Composer generation does not exist")
                if row.job_id is not None and row.job_id != str(job_id):
                    raise ComposerConflictError("Generation job changed")
                row.job_id = str(job_id)
                database.flush()
                return _generation(row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Generation job changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def attach_generation_revision(
        self,
        owner_id: UUID,
        draft_id: UUID,
        generation_id: UUID,
        revision_id: UUID,
        publication_key: str,
    ) -> ComposerGeneration:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                owned_draft(database, owner_id, draft_id, lock=True)
                row = database.scalar(
                    select(ComposerGenerationRow)
                    .where(
                        ComposerGenerationRow.id == str(generation_id),
                        ComposerGenerationRow.draft_id == str(draft_id),
                        ComposerGenerationRow.owner_id == str(owner_id),
                    )
                    .with_for_update()
                )
                if row is None:
                    raise ComposerNotFoundError("Composer generation does not exist")
                if row.result_revision_id is not None and row.result_revision_id != str(
                    revision_id
                ):
                    raise ComposerConflictError("Generation revision changed")
                if (
                    row.publication_key is not None
                    and row.publication_key != publication_key
                ):
                    raise ComposerConflictError("Generation publication key changed")
                row.result_revision_id = str(revision_id)
                row.publication_key = publication_key
                database.flush()
                return _generation(row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Generation revision changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None
