"""Durable, owner-scoped typed filling reviews and idempotent decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.persistence.composer.audit import record_content_audit
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.common import owned_draft, utc, validate_page
from markweave.persistence.composer.typed_templates import SqlTypedTemplateRepository
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerFillPlanDecisionRow,
    ComposerFillPlanRow,
    ComposerRevisionRow,
    TypedTemplateVersionRow,
)
from markweave.persistence.sql import serialize_sqlite_write

_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126
_LIST_SCAN_BATCH_SIZE = 100


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _valid_key(key: str) -> bool:
    return (
        bool(key)
        and len(key) <= _MAX_IDEMPOTENCY_KEY_LENGTH
        and all(_FIRST_VISIBLE_ASCII <= ord(c) <= _LAST_VISIBLE_ASCII for c in key)
    )


@dataclass(frozen=True, slots=True)
class FillPlan:
    id: UUID
    draft_id: UUID
    source_revision_id: UUID
    template_id: UUID
    template_version_id: UUID
    template_docx_sha256: str
    template_schema_sha256: str
    author_refs: tuple[tuple[UUID, int], ...]
    values: dict[str, Any]
    provenance: dict[str, Any]
    questions: tuple[dict[str, str], ...]
    state: str
    version: int
    draft_version: int
    result_revision_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @property
    def etag(self) -> str:
        return f'"{self.version}"'


def _result(database: Session, row: ComposerFillPlanRow) -> FillPlan:
    template = database.get(TypedTemplateVersionRow, row.template_version_id)
    if template is None:
        raise ComposerNotFoundError("Fill template version does not exist")
    return FillPlan(
        id=UUID(row.id),
        draft_id=UUID(row.draft_id),
        source_revision_id=UUID(row.source_revision_id),
        template_id=UUID(template.template_id),
        template_version_id=UUID(row.template_version_id),
        template_docx_sha256=row.template_docx_sha256,
        template_schema_sha256=row.template_schema_sha256,
        author_refs=tuple(
            (UUID(item["id"]), int(item["version"]))
            for item in json.loads(row.author_refs)
        ),
        values=json.loads(row.values_json),
        provenance=json.loads(row.provenance_json),
        questions=tuple(json.loads(row.questions_json)),
        state=row.state,
        version=row.version,
        draft_version=row.draft_version,
        result_revision_id=UUID(row.result_revision_id)
        if row.result_revision_id
        else None,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
    )


class SqlFillPlanRepository:
    """The same SQL transaction contract for standalone and distributed profiles."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._new_id = new_id

    @staticmethod
    def require_access(
        database: Session, owner_id: UUID, row: ComposerFillPlanRow, *, lock: bool
    ) -> None:
        """Fence cached knowledge and template bytes on every plan read/decision."""

        for item in json.loads(row.author_refs):
            try:
                SqlAuthorKnowledgeRepository.require_access(
                    database,
                    owner_id,
                    UUID(item["id"]),
                    expected_version=int(item["version"]),
                    for_update=lock,
                )
            except LookupError, RuntimeError:
                raise ComposerConflictError("Author access changed") from None
        try:
            SqlTypedTemplateRepository.require_version_access(
                database,
                owner_id,
                UUID(row.template_version_id),
                expected_docx_sha256=row.template_docx_sha256,
                expected_schema_sha256=row.template_schema_sha256,
                for_update=lock,
            )
        except LookupError, RuntimeError:
            raise ComposerConflictError("Typed template access changed") from None

    @staticmethod
    def _owned(
        database: Session,
        owner_id: UUID,
        draft_id: UUID,
        plan_id: UUID,
        *,
        lock: bool = False,
    ) -> ComposerFillPlanRow:
        statement = select(ComposerFillPlanRow).where(
            ComposerFillPlanRow.id == str(plan_id),
            ComposerFillPlanRow.draft_id == str(draft_id),
            ComposerFillPlanRow.owner_id == str(owner_id),
        )
        if lock:
            statement = statement.with_for_update()
        row = database.scalar(statement)
        if row is None:
            raise ComposerNotFoundError("Fill plan does not exist")
        return row

    def create(  # noqa: PLR0913 - explicit frozen plan identity and review content
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        source_revision_id: UUID,
        template_version_id: UUID,
        author_refs: tuple[tuple[UUID, int], ...],
        values: dict[str, Any],
        provenance: dict[str, Any],
        questions: tuple[dict[str, str], ...],
        if_match: str,
        idempotency_key: str,
    ) -> FillPlan:
        if not _valid_key(idempotency_key):
            raise ValueError("Fill plan idempotency key is invalid")
        refs_json = _json(
            [{"id": str(id_), "version": version} for id_, version in author_refs]
        )
        request_digest = _digest(
            {
                "source_revision_id": str(source_revision_id),
                "template_version_id": str(template_version_id),
                "author_refs": json.loads(refs_json),
                "values": values,
                "provenance": provenance,
                "questions": questions,
                "if_match": if_match,
            }
        )
        now = self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                existing = database.scalar(
                    select(ComposerFillPlanRow).where(
                        ComposerFillPlanRow.draft_id == str(draft_id),
                        ComposerFillPlanRow.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_digest != request_digest:
                        raise ComposerConflictError("Fill plan key was reused")
                    self.require_access(database, owner_id, existing, lock=False)
                    return _result(database, existing)
                if if_match != f'"{draft.version}"':
                    raise ComposerConflictError("Composer draft changed")
                if draft.current_revision_id != str(source_revision_id):
                    raise ComposerConflictError("Fill source revision changed")
                source = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.id == str(source_revision_id),
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.publication_state == "published",
                    )
                )
                if source is None:
                    raise ComposerNotFoundError("Fill source revision does not exist")
                template = SqlTypedTemplateRepository.require_version_access(
                    database, owner_id, template_version_id, for_update=True
                )
                for author_id, version in author_refs:
                    SqlAuthorKnowledgeRepository.require_access(
                        database,
                        owner_id,
                        author_id,
                        expected_version=version,
                        for_update=True,
                    )
                draft.version += 1
                draft.updated_at = now
                row = ComposerFillPlanRow(
                    id=str(self._new_id()),
                    draft_id=str(draft_id),
                    owner_id=str(owner_id),
                    draft_version=draft.version,
                    source_revision_id=str(source_revision_id),
                    template_version_id=str(template_version_id),
                    template_docx_sha256=template.sha256,
                    template_schema_sha256=template.schema_sha256,
                    author_refs=refs_json,
                    values_json=_json(values),
                    provenance_json=_json(provenance),
                    questions_json=_json(questions),
                    version=1,
                    state="pending",
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    result_revision_id=None,
                    created_at=now,
                    updated_at=now,
                )
                database.add(row)
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="fill_plan_create",
                    target_kind="composer_fill_plan",
                    target_id=UUID(row.id),
                    draft_id=draft_id,
                    draft_version=draft.version,
                    created_at=now,
                )
                database.flush()
                return _result(database, row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Fill plan changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get(self, owner_id: UUID, draft_id: UUID, plan_id: UUID) -> FillPlan:
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                row = self._owned(database, owner_id, draft_id, plan_id)
                self.require_access(database, owner_id, row, lock=False)
                return _result(database, row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_visible(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[FillPlan, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                query = (
                    select(ComposerFillPlanRow)
                    .where(
                        ComposerFillPlanRow.owner_id == str(owner_id),
                        ComposerFillPlanRow.draft_id == str(draft_id),
                    )
                    .order_by(
                        ComposerFillPlanRow.created_at.desc(),
                        ComposerFillPlanRow.id.desc(),
                    )
                )
                visible: list[FillPlan] = []
                scanned = 0
                authorized = 0
                while len(visible) < limit:
                    rows = database.scalars(
                        query.limit(_LIST_SCAN_BATCH_SIZE).offset(scanned)
                    ).all()
                    if not rows:
                        break
                    scanned += len(rows)
                    for row in rows:
                        try:
                            self.require_access(database, owner_id, row, lock=False)
                        except ComposerConflictError:
                            continue
                        authorized += 1
                        if authorized > offset:
                            visible.append(_result(database, row))
                        if len(visible) == limit:
                            break
                return tuple(visible)
        except SQLAlchemyError:
            raise PersistenceError from None

    def decide(  # noqa: PLR0913 - explicit review and idempotency data
        self,
        owner_id: UUID,
        draft_id: UUID,
        plan_id: UUID,
        *,
        if_match: str,
        idempotency_key: str,
        values: dict[str, Any],
        provenance: dict[str, Any],
        questions: tuple[dict[str, str], ...],
        approve: bool,
    ) -> FillPlan:
        if not _valid_key(idempotency_key):
            raise ValueError("Fill decision idempotency key is invalid")
        digest = _digest(
            {
                "if_match": if_match,
                "values": values,
                "provenance": provenance,
                "questions": questions,
                "approve": approve,
            }
        )
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                row = self._owned(database, owner_id, draft_id, plan_id, lock=True)
                prior = database.scalar(
                    select(ComposerFillPlanDecisionRow).where(
                        ComposerFillPlanDecisionRow.plan_id == str(plan_id),
                        ComposerFillPlanDecisionRow.idempotency_key == idempotency_key,
                    )
                )
                if prior is not None:
                    if (
                        prior.request_digest != digest
                        or row.version != prior.resulting_version
                    ):
                        raise ComposerConflictError("Fill decision key was reused")
                    self.require_access(database, owner_id, row, lock=False)
                    return _result(database, row)
                if if_match != f'"{row.version}"' or row.state != "pending":
                    raise ComposerConflictError("Fill plan changed")
                if (
                    draft.version != row.draft_version
                    or draft.current_revision_id != row.source_revision_id
                ):
                    raise ComposerConflictError("Composer draft changed")
                self.require_access(database, owner_id, row, lock=True)
                if approve and questions:
                    raise ComposerConflictError("Fill plan has unanswered questions")
                now = self._clock()
                row.values_json = _json(values)
                row.provenance_json = _json(provenance)
                row.questions_json = _json(questions)
                row.version += 1
                row.updated_at = now
                if approve:
                    row.state = "approved"
                database.add(
                    ComposerFillPlanDecisionRow(
                        id=str(self._new_id()),
                        plan_id=row.id,
                        idempotency_key=idempotency_key,
                        request_digest=digest,
                        resulting_version=row.version,
                        created_at=now,
                    )
                )
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="fill_plan_approve" if approve else "fill_plan_edit",
                    target_kind="composer_fill_plan",
                    target_id=plan_id,
                    draft_id=draft_id,
                    draft_version=draft.version,
                    created_at=now,
                )
                database.flush()
                return _result(database, row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Fill plan changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def mark_published(
        self, owner_id: UUID, draft_id: UUID, plan_id: UUID, revision_id: UUID
    ) -> FillPlan:
        """Idempotently link an already atomically published exact revision."""

        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._owned(database, owner_id, draft_id, plan_id, lock=True)
                if row.state == "published" and row.result_revision_id == str(
                    revision_id
                ):
                    return _result(database, row)
                if row.state != "approved" or row.result_revision_id is not None:
                    raise ComposerConflictError("Fill plan publication changed")
                revision = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.id == str(revision_id),
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.publication_state == "published",
                    )
                )
                if (
                    revision is None
                    or revision.operation != "fill_template"
                    or revision.expected_draft_version != row.draft_version
                    or revision.actor_id != row.owner_id
                    or revision.typed_fill_snapshot is None
                ):
                    raise ComposerConflictError("Exact fill revision is not published")
                try:
                    frozen = json.loads(revision.typed_fill_snapshot)
                except ValueError, TypeError:
                    raise ComposerConflictError(
                        "Exact fill revision is invalid"
                    ) from None
                if not isinstance(frozen, dict) or any(
                    frozen.get(name) != expected
                    for name, expected in (
                        ("fill_plan_id", row.id),
                        ("fill_plan_version", row.version),
                        ("source_revision_id", row.source_revision_id),
                        ("template_version_id", row.template_version_id),
                        ("template_docx_sha256", row.template_docx_sha256),
                        ("template_schema_sha256", row.template_schema_sha256),
                        ("approved_values", json.loads(row.values_json)),
                        ("provenance", json.loads(row.provenance_json)),
                        ("author_refs", json.loads(row.author_refs)),
                    )
                ):
                    raise ComposerConflictError(
                        "Exact fill revision does not match plan"
                    )
                artifact_rows = tuple(
                    database.scalars(
                        select(ComposerArtifactRow).where(
                            ComposerArtifactRow.revision_id == str(revision_id),
                            ComposerArtifactRow.kind.in_(("download", "preview")),
                        )
                    )
                )
                if {artifact.kind for artifact in artifact_rows} != {
                    "download",
                    "preview",
                } or any(
                    artifact.sha256 != frozen.get("result_sha256")
                    or artifact.media_type
                    != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    for artifact in artifact_rows
                ):
                    raise ComposerConflictError("Exact fill artifacts do not match")
                row.state = "published"
                row.result_revision_id = str(revision_id)
                row.version += 1
                row.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="fill_plan_publish",
                    target_kind="composer_fill_plan",
                    target_id=plan_id,
                    draft_id=draft_id,
                    draft_version=revision.expected_draft_version + 1,
                    created_at=row.updated_at,
                )
                database.flush()
                return _result(database, row)
        except ComposerConflictError, ComposerNotFoundError:
            raise
        except SQLAlchemyError:
            raise PersistenceError from None
