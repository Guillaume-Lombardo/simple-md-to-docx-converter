"""Owner-scoped Composer draft, conversation, and revision repository."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, delete, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.drafts import (
    ComposerDraft,
    ComposerMessage,
    ComposerProposal,
    ProposalState,
)
from markweave.composer.revisions import (
    ComposerArtifactError,
    ComposerConflictError,
    ComposerNotFoundError,
    SourceReference,
)
from markweave.persistence.composer.audit import SYSTEM_ACTOR_ID, record_content_audit
from markweave.persistence.composer.common import (
    DEFAULT_PAGE_LIMIT,
    PageOrder,
    draft_from_row,
    message_from_row,
    owned_draft,
    proposal_from_row,
    source_json,
    utc,
    validate_page,
    validate_page_order,
)
from markweave.persistence.composer.generations import _SqlComposerGenerations
from markweave.persistence.composer.revisions import _SqlComposerRevisions
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerDraftRow,
    ComposerGenerationRow,
    ComposerMessageRow,
    ComposerProposalRow,
    ComposerRevisionRow,
    ComposerSourceRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)


class SqlComposerRepository(_SqlComposerRevisions, _SqlComposerGenerations):
    """One contract for SQLite/files and PostgreSQL/S3 Composer records."""

    def __init__(
        self,
        engine: Engine,
        objects: ObjectStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
        publication_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        if publication_lease <= timedelta(0):
            raise ValueError("Publication lease must be positive")
        self._engine = engine
        self._objects = objects
        self._clock = clock
        self._new_id = new_id
        self._publication_lease = publication_lease

    def create_draft(
        self,
        owner_id: UUID,
        source: SourceReference,
        *,
        title: str,
        content: str,
        draft_id: UUID | None = None,
    ) -> ComposerDraft:
        """Record a previously verified immutable source handoff."""

        if source.owner_id != owner_id or source.kind not in {
            "upload",
            "conversion_result",
            "reversion_result",
        }:
            raise ComposerNotFoundError("Composer source does not exist")
        now = self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ComposerSourceRow)
                    .where(ComposerSourceRow.id == str(source.object_id))
                    .with_for_update()
                )
                if (
                    row is None
                    or row.owner_id != str(owner_id)
                    or row.publication_state != "published"
                    or row.sha256 != source.sha256
                    or row.scan_receipt != source.scan_receipt
                    or row.media_type != source.media_type
                    or row.kind != source.kind
                    or row.origin_job_id
                    != (str(source.origin_job_id) if source.origin_job_id else None)
                    or row.origin_result_object_id
                    != (
                        str(source.origin_result_object_id)
                        if source.origin_result_object_id
                        else None
                    )
                    or row.origin_result_sha256 != source.origin_result_sha256
                ):
                    raise ComposerNotFoundError("Composer source does not exist")
                draft = self._draft_row(owner_id, source, title, content, now, draft_id)
                database.add(draft)
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="draft_create",
                    target_kind="composer_draft",
                    target_id=UUID(draft.id),
                    draft_id=UUID(draft.id),
                    draft_version=draft.version,
                    created_at=now,
                )
                database.flush()
                return draft_from_row(draft)
        except ComposerNotFoundError, ComposerConflictError:
            raise
        except IntegrityError:
            raise ComposerConflictError("Composer draft already exists") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def create_draft_with_source(  # noqa: PLR0913 - explicit admission inputs
        self,
        owner_id: UUID,
        source_bytes: bytes,
        scan_receipt: str,
        *,
        title: str,
        content: str,
        media_type: str,
        draft_id: UUID | None = None,
        origin_job_id: UUID | None = None,
        origin_result_object_id: UUID | None = None,
        origin_result_sha256: str | None = None,
        source_kind: str | None = None,
    ) -> ComposerDraft:
        """Reserve a hidden scanned upload, then expose source and draft together."""

        if not source_bytes or not scan_receipt.strip():
            raise ValueError("Scanned source bytes and proof are required")
        source_id, token = self._new_id(), self._new_id()
        now = self._clock()
        sha256 = hashlib.sha256(source_bytes).hexdigest()
        kind = source_kind or (
            "conversion_result" if origin_job_id is not None else "upload"
        )
        source = SourceReference(
            kind,
            source_id,
            owner_id,
            sha256,
            scan_receipt,
            media_type,
            origin_job_id,
            origin_result_object_id,
            origin_result_sha256,
        )
        key = ObjectKey(ObjectScope.COMPOSER_SOURCE, owner_id, source_id)
        wrote_object = False
        try:
            with Session(self._engine) as database, database.begin():
                database.add(
                    ComposerSourceRow(
                        id=str(source_id),
                        kind=kind,
                        owner_id=str(owner_id),
                        sha256=sha256,
                        size=len(source_bytes),
                        scan_receipt=scan_receipt,
                        media_type=media_type,
                        origin_job_id=str(origin_job_id) if origin_job_id else None,
                        origin_result_object_id=(
                            str(origin_result_object_id)
                            if origin_result_object_id
                            else None
                        ),
                        origin_result_sha256=origin_result_sha256,
                        publication_state="pending",
                        publication_token=str(token),
                        lease_expires_at=now + self._publication_lease,
                        created_at=now,
                    )
                )
            self._objects.put(key, source_bytes)
            wrote_object = True
            if hashlib.sha256(self._objects.get(key)).hexdigest() != sha256:
                raise ComposerArtifactError("Composer source integrity check failed")
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                reserved = database.scalar(
                    select(ComposerSourceRow)
                    .where(
                        ComposerSourceRow.id == str(source_id),
                        ComposerSourceRow.publication_state == "pending",
                        ComposerSourceRow.publication_token == str(token),
                    )
                    .with_for_update()
                )
                if reserved is None:
                    raise ComposerConflictError("Composer source publication changed")
                draft = self._draft_row(owner_id, source, title, content, now, draft_id)
                database.add(draft)
                reserved.publication_state = "published"
                reserved.publication_token = None
                reserved.lease_expires_at = None
                for operation, target_kind, target_id in (
                    ("source_publish", "composer_source", source_id),
                    ("draft_create", "composer_draft", UUID(draft.id)),
                ):
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=owner_id,
                        actor_id=owner_id,
                        operation=operation,
                        target_kind=target_kind,
                        target_id=target_id,
                        draft_id=UUID(draft.id),
                        draft_version=draft.version,
                        created_at=now,
                    )
                database.flush()
                return draft_from_row(draft)
        except ComposerConflictError:
            if wrote_object:
                with suppress(ObjectStoreError):
                    self._objects.delete(key)
            raise
        except ComposerArtifactError:
            raise
        except ObjectNotFoundError, ObjectStoreError:
            raise ComposerArtifactError("Composer source publication failed") from None
        except IntegrityError:
            raise ComposerConflictError("Composer draft already exists") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def read_source(self, owner_id: UUID, source_id: UUID) -> bytes:
        try:
            with Session(self._engine) as database:
                row = database.get(ComposerSourceRow, str(source_id))
                if (
                    row is None
                    or row.owner_id != str(owner_id)
                    or row.publication_state != "published"
                ):
                    raise ComposerNotFoundError("Composer source does not exist")
                content = self._objects.get(
                    ObjectKey(ObjectScope.COMPOSER_SOURCE, owner_id, source_id)
                )
                if (
                    len(content) != row.size
                    or hashlib.sha256(content).hexdigest() != row.sha256
                ):
                    raise ComposerArtifactError(
                        "Composer source integrity check failed"
                    )
                return content
        except ObjectNotFoundError, ObjectStoreError:
            raise ComposerArtifactError("Composer source is unavailable") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def recover_stale_sources(self, *, stale_before: datetime) -> int:
        """Remove unreferenced pending uploads after a crashed publication attempt."""

        recovered = 0
        try:
            with Session(self._engine) as database:
                candidates = tuple(
                    database.scalars(
                        select(ComposerSourceRow.id).where(
                            ComposerSourceRow.publication_state == "pending",
                            ComposerSourceRow.lease_expires_at <= stale_before,
                        )
                    )
                )
            for source_id in candidates:
                token = self._new_id()
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerSourceRow)
                        .where(
                            ComposerSourceRow.id == source_id,
                            ComposerSourceRow.publication_state == "pending",
                            ComposerSourceRow.lease_expires_at <= stale_before,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        continue
                    owner_id = UUID(row.owner_id)
                    row.publication_token = str(token)
                    row.lease_expires_at = self._clock() + self._publication_lease
                try:
                    self._objects.delete(
                        ObjectKey(
                            ObjectScope.COMPOSER_SOURCE, owner_id, UUID(source_id)
                        )
                    )
                except ObjectStoreError:
                    continue
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerSourceRow)
                        .where(
                            ComposerSourceRow.id == source_id,
                            ComposerSourceRow.publication_state == "pending",
                            ComposerSourceRow.publication_token == str(token),
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        record_content_audit(
                            database,
                            event_id=self._new_id(),
                            owner_id=owner_id,
                            actor_id=SYSTEM_ACTOR_ID,
                            operation="source_recover",
                            target_kind="composer_source",
                            target_id=UUID(source_id),
                            draft_id=None,
                            draft_version=1,
                            created_at=self._clock(),
                        )
                        database.delete(row)
                        recovered += 1
            return recovered
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_expired_drafts(self, *, cutoff_at: datetime, limit: int) -> int:
        """Hide expired drafts first, then remove their immutable objects and rows."""

        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        completed = 0
        try:
            with Session(self._engine) as database:
                candidate_ids = tuple(
                    database.scalars(
                        select(ComposerDraftRow.id)
                        .where(
                            or_(
                                ComposerDraftRow.state == "deleting",
                                (ComposerDraftRow.state == "active")
                                & (ComposerDraftRow.updated_at < cutoff_at),
                            )
                        )
                        .order_by(ComposerDraftRow.updated_at, ComposerDraftRow.id)
                        .limit(limit)
                    )
                )
            for draft_id in candidate_ids:
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    draft = database.scalar(
                        select(ComposerDraftRow)
                        .where(ComposerDraftRow.id == draft_id)
                        .with_for_update()
                    )
                    if draft is None or (
                        draft.state == "active" and utc(draft.updated_at) >= cutoff_at
                    ):
                        continue
                    if draft.state == "active":
                        draft.state = "deleting"
                        record_content_audit(
                            database,
                            event_id=self._new_id(),
                            owner_id=UUID(draft.owner_id),
                            actor_id=SYSTEM_ACTOR_ID,
                            operation="draft_hide",
                            target_kind="composer_draft",
                            target_id=UUID(draft.id),
                            draft_id=UUID(draft.id),
                            draft_version=draft.version,
                            created_at=self._clock(),
                        )
                    owner_id = UUID(draft.owner_id)
                    artifact_ids = tuple(
                        UUID(value)
                        for value in database.scalars(
                            select(ComposerArtifactRow.id)
                            .join(
                                ComposerRevisionRow,
                                ComposerArtifactRow.revision_id
                                == ComposerRevisionRow.id,
                            )
                            .where(ComposerRevisionRow.draft_id == draft_id)
                        )
                    )
                for artifact_id in artifact_ids:
                    self._objects.delete(
                        ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner_id, artifact_id)
                    )
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    draft = database.scalar(
                        select(ComposerDraftRow)
                        .where(
                            ComposerDraftRow.id == draft_id,
                            ComposerDraftRow.state == "deleting",
                        )
                        .with_for_update()
                    )
                    if draft is None:
                        continue
                    revision_ids = select(ComposerRevisionRow.id).where(
                        ComposerRevisionRow.draft_id == draft_id
                    )
                    database.execute(
                        delete(ComposerArtifactRow).where(
                            ComposerArtifactRow.revision_id.in_(revision_ids)
                        )
                    )
                    database.execute(
                        delete(ComposerGenerationRow).where(
                            ComposerGenerationRow.draft_id == draft_id
                        )
                    )
                    database.execute(
                        delete(ComposerRevisionRow).where(
                            ComposerRevisionRow.draft_id == draft_id
                        )
                    )
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=UUID(draft.owner_id),
                        actor_id=SYSTEM_ACTOR_ID,
                        operation="draft_delete",
                        target_kind="composer_draft",
                        target_id=UUID(draft.id),
                        draft_id=UUID(draft.id),
                        draft_version=draft.version,
                        created_at=self._clock(),
                    )
                    database.delete(draft)
                    completed += 1
            return completed
        except ObjectStoreError:
            raise ComposerArtifactError(
                "Composer retention object cleanup failed"
            ) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def cleanup_orphan_sources(self, *, limit: int) -> int:
        """Remove scanned source objects after no draft still references them."""

        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        completed = 0
        try:
            with Session(self._engine) as database:
                candidates = tuple(
                    database.scalars(
                        select(ComposerSourceRow.id)
                        .where(ComposerSourceRow.publication_state == "published")
                        .order_by(ComposerSourceRow.created_at)
                        .limit(limit)
                    )
                )
            for source_id in candidates:
                token = self._new_id()
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerSourceRow)
                        .where(
                            ComposerSourceRow.id == source_id,
                            ComposerSourceRow.publication_state == "published",
                        )
                        .with_for_update()
                    )
                    if row is None:
                        continue
                    source = SourceReference(
                        row.kind,
                        UUID(row.id),
                        UUID(row.owner_id),
                        row.sha256,
                        row.scan_receipt,
                        row.media_type,
                        UUID(row.origin_job_id) if row.origin_job_id else None,
                        UUID(row.origin_result_object_id)
                        if row.origin_result_object_id
                        else None,
                        row.origin_result_sha256,
                    )
                    still_used = database.scalar(
                        select(ComposerDraftRow.id)
                        .where(ComposerDraftRow.source_reference == source_json(source))
                        .limit(1)
                    )
                    if still_used is not None:
                        continue
                    row.publication_state = "pending"
                    row.publication_token = str(token)
                    row.lease_expires_at = self._clock() + self._publication_lease
                    owner_id = source.owner_id
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=owner_id,
                        actor_id=SYSTEM_ACTOR_ID,
                        operation="source_hide",
                        target_kind="composer_source",
                        target_id=UUID(source_id),
                        draft_id=None,
                        draft_version=1,
                        created_at=self._clock(),
                    )
                self._objects.delete(
                    ObjectKey(ObjectScope.COMPOSER_SOURCE, owner_id, UUID(source_id))
                )
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerSourceRow)
                        .where(
                            ComposerSourceRow.id == source_id,
                            ComposerSourceRow.publication_state == "pending",
                            ComposerSourceRow.publication_token == str(token),
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        record_content_audit(
                            database,
                            event_id=self._new_id(),
                            owner_id=owner_id,
                            actor_id=SYSTEM_ACTOR_ID,
                            operation="source_delete",
                            target_kind="composer_source",
                            target_id=UUID(source_id),
                            draft_id=None,
                            draft_version=1,
                            created_at=self._clock(),
                        )
                        database.delete(row)
                        completed += 1
            return completed
        except ObjectStoreError:
            raise ComposerArtifactError(
                "Composer retention source cleanup failed"
            ) from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_draft(self, owner_id: UUID, draft_id: UUID) -> ComposerDraft:
        try:
            with Session(self._engine) as database:
                return draft_from_row(owned_draft(database, owner_id, draft_id))
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_drafts(
        self, owner_id: UUID, *, limit: int = DEFAULT_PAGE_LIMIT, offset: int = 0
    ) -> tuple[ComposerDraft, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                return tuple(
                    draft_from_row(row)
                    for row in database.scalars(
                        select(ComposerDraftRow)
                        .where(
                            ComposerDraftRow.owner_id == str(owner_id),
                            ComposerDraftRow.state == "active",
                        )
                        .order_by(
                            ComposerDraftRow.updated_at.desc(), ComposerDraftRow.id
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def save_draft(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        if_match: str,
        title: str,
        content: str,
    ) -> ComposerDraft:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = owned_draft(database, owner_id, draft_id, lock=True)
                self._require_etag(row, if_match)
                row.title, row.content = title, content
                row.version += 1
                row.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="draft_update",
                    target_kind="composer_draft",
                    target_id=draft_id,
                    draft_id=draft_id,
                    draft_version=row.version,
                    created_at=row.updated_at,
                )
                database.flush()
                return draft_from_row(row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def add_message(  # noqa: PLR0913 - explicit mutation preconditions
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        role: str,
        content: str,
        message_id: UUID | None = None,
        if_match: str,
    ) -> ComposerMessage:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("Unsupported Composer message role")
        id_ = message_id or self._new_id()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = owned_draft(database, owner_id, draft_id, lock=True)
                existing = database.get(ComposerMessageRow, str(id_))
                if existing is not None:
                    if (
                        existing.draft_id == str(draft_id)
                        and existing.role == role
                        and existing.content == content
                    ):
                        return message_from_row(existing)
                    raise ComposerConflictError("Message idempotency key was reused")
                self._require_etag(row, if_match)
                message = ComposerMessageRow(
                    id=str(id_),
                    draft_id=str(draft_id),
                    role=role,
                    content=content,
                    created_at=self._clock(),
                )
                database.add(message)
                row.version += 1
                row.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="message_add",
                    target_kind="composer_message",
                    target_id=id_,
                    draft_id=draft_id,
                    draft_version=row.version,
                    created_at=row.updated_at,
                )
                database.flush()
                return message_from_row(message)
        except IntegrityError:
            raise ComposerConflictError("Message changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_messages(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        order: PageOrder = "asc",
    ) -> tuple[ComposerMessage, ...]:
        validate_page(limit, offset)
        validate_page_order(order)
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                return tuple(
                    message_from_row(row)
                    for row in database.scalars(
                        select(ComposerMessageRow)
                        .where(ComposerMessageRow.draft_id == str(draft_id))
                        .order_by(
                            ComposerMessageRow.created_at.desc()
                            if order == "desc"
                            else ComposerMessageRow.created_at.asc(),
                            ComposerMessageRow.id.desc()
                            if order == "desc"
                            else ComposerMessageRow.id.asc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def create_proposal(  # noqa: PLR0913 - explicit proposal provenance
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        base_version: int,
        proposed_value: str,
        provenance: str,
        proposal_id: UUID | None = None,
    ) -> ComposerProposal:
        id_ = proposal_id or self._new_id()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                existing = database.get(ComposerProposalRow, str(id_))
                if existing is not None:
                    if (
                        existing.draft_id == str(draft_id)
                        and existing.base_version == base_version
                        and existing.proposed_value == proposed_value
                        and existing.provenance == provenance
                    ):
                        return proposal_from_row(existing)
                    raise ComposerConflictError("Proposal idempotency key was reused")
                if draft.version != base_version:
                    raise ComposerConflictError("Composer draft changed")
                proposal = ComposerProposalRow(
                    id=str(id_),
                    draft_id=str(draft_id),
                    base_version=base_version,
                    state="pending",
                    proposed_value=proposed_value,
                    decided_value=None,
                    provenance=provenance,
                    created_at=self._clock(),
                    decided_at=None,
                    decided_by=None,
                )
                database.add(proposal)
                draft.version += 1
                draft.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation="proposal_create",
                    target_kind="composer_proposal",
                    target_id=id_,
                    draft_id=draft_id,
                    draft_version=draft.version,
                    created_at=draft.updated_at,
                )
                database.flush()
                return proposal_from_row(proposal)
        except IntegrityError:
            raise ComposerConflictError("Proposal changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def decide_proposal(  # noqa: PLR0913 - explicit review decision
        self,
        owner_id: UUID,
        draft_id: UUID,
        proposal_id: UUID,
        *,
        if_match: str,
        state: ProposalState,
        decided_value: str | None,
    ) -> ComposerProposal:
        if state is ProposalState.PENDING or (
            state is ProposalState.EDITED and decided_value is None
        ):
            raise ValueError("Proposal decision is invalid")
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                proposal = database.scalar(
                    select(ComposerProposalRow)
                    .where(
                        ComposerProposalRow.id == str(proposal_id),
                        ComposerProposalRow.draft_id == str(draft_id),
                    )
                    .with_for_update()
                )
                if proposal is None:
                    raise ComposerNotFoundError("Composer proposal does not exist")
                final_value = (
                    decided_value
                    if state is ProposalState.EDITED
                    else proposal.proposed_value
                    if state is ProposalState.ACCEPTED
                    else None
                )
                if proposal.state != "pending":
                    if (
                        proposal.state == state.value
                        and proposal.decided_value == final_value
                    ):
                        return proposal_from_row(proposal)
                    raise ComposerConflictError("Proposal was already decided")
                self._require_etag(draft, if_match)
                if draft.version != proposal.base_version + 1:
                    raise ComposerConflictError(
                        "Composer proposal requires review again"
                    )
                proposal.state = state.value
                proposal.decided_value = final_value
                proposal.decided_at = self._clock()
                proposal.decided_by = str(owner_id)
                draft.version += 1
                draft.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=owner_id,
                    operation=f"proposal_{state.value}",
                    target_kind="composer_proposal",
                    target_id=proposal_id,
                    draft_id=draft_id,
                    draft_version=draft.version,
                    created_at=draft.updated_at,
                )
                database.flush()
                return proposal_from_row(proposal)
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_proposal(
        self, owner_id: UUID, draft_id: UUID, proposal_id: UUID
    ) -> ComposerProposal:
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                row = database.scalar(
                    select(ComposerProposalRow).where(
                        ComposerProposalRow.id == str(proposal_id),
                        ComposerProposalRow.draft_id == str(draft_id),
                    )
                )
                if row is None:
                    raise ComposerNotFoundError("Composer proposal does not exist")
                return proposal_from_row(row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_proposals(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        order: PageOrder = "asc",
    ) -> tuple[ComposerProposal, ...]:
        validate_page(limit, offset)
        validate_page_order(order)
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                return tuple(
                    proposal_from_row(row)
                    for row in database.scalars(
                        select(ComposerProposalRow)
                        .where(ComposerProposalRow.draft_id == str(draft_id))
                        .order_by(
                            ComposerProposalRow.created_at.desc()
                            if order == "desc"
                            else ComposerProposalRow.created_at.asc(),
                            ComposerProposalRow.id.desc()
                            if order == "desc"
                            else ComposerProposalRow.id.asc(),
                        )
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _require_etag(row: ComposerDraftRow, if_match: str) -> None:
        if if_match != f'"{row.version}"':
            raise ComposerConflictError("Composer draft changed")

    def _draft_row(  # noqa: PLR0913, PLR0917 - fixed row fields
        self,
        owner_id: UUID,
        source: SourceReference,
        title: str,
        content: str,
        now: datetime,
        draft_id: UUID | None,
    ) -> ComposerDraftRow:
        return ComposerDraftRow(
            id=str(draft_id or self._new_id()),
            owner_id=str(owner_id),
            title=title,
            source_reference=source_json(source),
            content=content,
            state="active",
            version=1,
            current_revision_id=None,
            created_at=now,
            updated_at=now,
        )
