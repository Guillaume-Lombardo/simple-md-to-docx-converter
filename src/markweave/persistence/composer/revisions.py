"""Two-phase immutable Composer revision publication and recovery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.revisions import (
    ArtifactContent,
    ArtifactReference,
    ComposerArtifactError,
    ComposerConflictError,
    ComposerNotFoundError,
    ComposerRevision,
    RevisionSnapshot,
)
from markweave.persistence.composer.audit import SYSTEM_ACTOR_ID, record_content_audit
from markweave.persistence.composer.common import (
    DEFAULT_PAGE_LIMIT,
    owned_draft,
    source_from_json,
    source_json,
    utc,
    validate_page,
)
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerDraftRow,
    ComposerRevisionRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.storage import (
    ObjectKey,
    ObjectNotFoundError,
    ObjectScope,
    ObjectStore,
    ObjectStoreError,
)

_MAX_IDEMPOTENCY_KEY_LENGTH = 128


def _snapshot(row: ComposerRevisionRow) -> RevisionSnapshot:
    return RevisionSnapshot(
        source=source_from_json(row.source_reference),
        template_reference=row.template_reference,
        approved_values=row.approved_values,
        render_options=row.render_options,
        model_identity=row.model_identity,
        provenance=row.provenance,
        operation=row.operation,
    )


def _revision(database: Session, row: ComposerRevisionRow) -> ComposerRevision:
    artifacts = tuple(
        ArtifactReference(
            UUID(artifact.id),
            artifact.kind,
            artifact.sha256,
            artifact.size,
            artifact.media_type,
        )
        for artifact in database.scalars(
            select(ComposerArtifactRow)
            .where(ComposerArtifactRow.revision_id == row.id)
            .order_by(ComposerArtifactRow.kind)
        )
    )
    return ComposerRevision(
        UUID(row.id),
        UUID(row.draft_id),
        row.number,
        UUID(row.actor_id),
        _snapshot(row),
        artifacts,
        UUID(row.restored_from_revision_id) if row.restored_from_revision_id else None,
        utc(row.created_at),
    )


def _request_digest(
    snapshot: RevisionSnapshot,
    artifacts: tuple[ArtifactContent, ...],
    restored_from: UUID | None,
) -> str:
    data = {
        "source": json.loads(source_json(snapshot.source)),
        "template_reference": snapshot.template_reference,
        "approved_values": snapshot.approved_values,
        "render_options": snapshot.render_options,
        "model_identity": snapshot.model_identity,
        "provenance": snapshot.provenance,
        "operation": snapshot.operation,
        "restored_from": str(restored_from) if restored_from else None,
        "artifacts": [
            {
                "kind": item.kind,
                "media_type": item.media_type,
                "sha256": hashlib.sha256(item.content).hexdigest(),
            }
            for item in sorted(artifacts, key=lambda item: item.kind)
        ],
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _SqlComposerRevisions:
    """Mixin for exact revision reads, publication, restore, and stale cleanup."""

    _engine: Engine
    _objects: ObjectStore
    _clock: Callable[[], datetime]
    _new_id: Callable[[], UUID]
    _publication_lease: timedelta

    def publish_revision(  # noqa: PLR0913, PLR0912, PLR0915 - two-phase publication
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        actor_id: UUID,
        if_match: str,
        idempotency_key: str,
        snapshot: RevisionSnapshot,
        artifacts: tuple[ArtifactContent, ...],
        restored_from_revision_id: UUID | None = None,
    ) -> ComposerRevision:
        if not idempotency_key or len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError("Revision idempotency key is invalid")
        kinds = {artifact.kind for artifact in artifacts}
        if kinds not in ({"download", "preview"}, {"source", "download", "preview"}):
            raise ValueError("Matching download and preview artifacts are required")
        if len(kinds) != len(artifacts):
            raise ValueError("Duplicate Composer artifact kind")
        if snapshot.source.owner_id != owner_id:
            raise ComposerNotFoundError("Composer source does not exist")
        digest = _request_digest(snapshot, artifacts, restored_from_revision_id)
        now = self._clock()
        token = self._new_id()
        pairs: list[tuple[ArtifactContent, UUID]] = []
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                if source_json(snapshot.source) != draft.source_reference:
                    raise ComposerConflictError(
                        "Revision source does not match the draft"
                    )
                existing = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    if existing.request_digest != digest:
                        raise ComposerConflictError(
                            "Revision idempotency key was reused"
                        )
                    if existing.publication_state == "published":
                        return _revision(database, existing)
                    raise ComposerConflictError("Revision publication is in progress")
                self._require_etag(draft, if_match)
                pending = database.scalar(
                    select(ComposerRevisionRow.id).where(
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.publication_state == "pending",
                    )
                )
                if pending is not None:
                    raise ComposerConflictError("Revision publication is in progress")
                number = (
                    int(
                        database.scalar(
                            select(
                                func.coalesce(func.max(ComposerRevisionRow.number), 0)
                            ).where(ComposerRevisionRow.draft_id == str(draft_id))
                        )
                        or 0
                    )
                    + 1
                )
                revision_id = self._new_id()
                row = ComposerRevisionRow(
                    id=str(revision_id),
                    draft_id=str(draft_id),
                    number=number,
                    actor_id=str(actor_id),
                    expected_draft_version=draft.version,
                    source_reference=source_json(snapshot.source),
                    template_reference=snapshot.template_reference,
                    approved_values=snapshot.approved_values,
                    render_options=snapshot.render_options,
                    model_identity=snapshot.model_identity,
                    provenance=snapshot.provenance,
                    operation=snapshot.operation,
                    restored_from_revision_id=(
                        str(restored_from_revision_id)
                        if restored_from_revision_id
                        else None
                    ),
                    idempotency_key=idempotency_key,
                    request_digest=digest,
                    publication_state="pending",
                    publication_token=str(token),
                    lease_expires_at=now + self._publication_lease,
                    created_at=now,
                )
                database.add(row)
                database.flush()
                for artifact in artifacts:
                    artifact_id = self._new_id()
                    pairs.append((artifact, artifact_id))
                    database.add(
                        ComposerArtifactRow(
                            id=str(artifact_id),
                            revision_id=str(revision_id),
                            kind=artifact.kind,
                            sha256=hashlib.sha256(artifact.content).hexdigest(),
                            size=len(artifact.content),
                            media_type=artifact.media_type,
                        )
                    )
            for artifact, artifact_id in pairs:
                key = ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner_id, artifact_id)
                self._objects.put(key, artifact.content)
                checked = self._objects.get(key)
                if checked != artifact.content:
                    raise ComposerArtifactError(
                        "Composer artifact integrity check failed"
                    )
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                reserved = database.scalar(
                    select(ComposerRevisionRow)
                    .where(
                        ComposerRevisionRow.id == str(revision_id),
                        ComposerRevisionRow.publication_state == "pending",
                        ComposerRevisionRow.publication_token == str(token),
                    )
                    .with_for_update()
                )
                if reserved is None or draft.version != reserved.expected_draft_version:
                    raise ComposerConflictError(
                        "Composer draft changed during publication"
                    )
                reserved.publication_state = "published"
                reserved.publication_token = None
                reserved.lease_expires_at = None
                draft.current_revision_id = reserved.id
                draft.version += 1
                draft.updated_at = self._clock()
                record_content_audit(
                    database,
                    event_id=self._new_id(),
                    owner_id=owner_id,
                    actor_id=actor_id,
                    operation=(
                        "revision_restore"
                        if restored_from_revision_id is not None
                        else "revision_publish"
                    ),
                    target_kind="composer_revision",
                    target_id=revision_id,
                    draft_id=draft_id,
                    draft_version=draft.version,
                    created_at=draft.updated_at,
                )
                database.flush()
                return _revision(database, reserved)
        except ComposerConflictError:
            for _artifact, artifact_id in pairs:
                with suppress(ObjectStoreError):
                    self._objects.delete(
                        ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner_id, artifact_id)
                    )
            raise
        except ComposerNotFoundError, ComposerArtifactError:
            raise
        except ObjectNotFoundError, ObjectStoreError:
            raise ComposerArtifactError(
                "Composer artifact publication failed"
            ) from None
        except IntegrityError:
            raise ComposerConflictError("Composer revision changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_revision(
        self, owner_id: UUID, draft_id: UUID, revision_id: UUID
    ) -> ComposerRevision:
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                row = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.id == str(revision_id),
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.publication_state == "published",
                    )
                )
                if row is None:
                    raise ComposerNotFoundError("Composer revision does not exist")
                return _revision(database, row)
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_revisions(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> tuple[ComposerRevision, ...]:
        validate_page(limit, offset)
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                return tuple(
                    _revision(database, row)
                    for row in database.scalars(
                        select(ComposerRevisionRow)
                        .where(
                            ComposerRevisionRow.draft_id == str(draft_id),
                            ComposerRevisionRow.publication_state == "published",
                        )
                        .order_by(ComposerRevisionRow.number, ComposerRevisionRow.id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
        except SQLAlchemyError:
            raise PersistenceError from None

    def read_artifact(
        self, owner_id: UUID, draft_id: UUID, revision_id: UUID, kind: str
    ) -> bytes:
        revision = self.get_revision(owner_id, draft_id, revision_id)
        artifact = next(
            (item for item in revision.artifacts if item.kind == kind), None
        )
        if artifact is None:
            raise ComposerNotFoundError("Composer artifact does not exist")
        try:
            content = self._objects.get(
                ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner_id, artifact.id)
            )
        except ObjectNotFoundError, ObjectStoreError:
            raise ComposerArtifactError("Composer artifact is unavailable") from None
        if (
            len(content) != artifact.size
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise ComposerArtifactError("Composer artifact integrity check failed")
        return content

    def restore_revision(  # noqa: PLR0913 - optimistic restore inputs
        self,
        owner_id: UUID,
        draft_id: UUID,
        revision_id: UUID,
        *,
        actor_id: UUID,
        if_match: str,
        idempotency_key: str,
    ) -> ComposerRevision:
        prior = self.get_revision(owner_id, draft_id, revision_id)
        copies = tuple(
            ArtifactContent(
                item.kind,
                item.media_type,
                self.read_artifact(owner_id, draft_id, revision_id, item.kind),
            )
            for item in prior.artifacts
        )
        snapshot = replace(prior.snapshot, operation="restore")
        return self.publish_revision(
            owner_id,
            draft_id,
            actor_id=actor_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            snapshot=snapshot,
            artifacts=copies,
            restored_from_revision_id=revision_id,
        )

    def recover_stale_publications(self, *, stale_before: datetime) -> int:
        """Claim expired attempts, remove orphan bytes, then remove hidden metadata."""

        recovered = 0
        with Session(self._engine) as database:
            candidates = tuple(
                database.scalars(
                    select(ComposerRevisionRow.id).where(
                        ComposerRevisionRow.publication_state == "pending",
                        ComposerRevisionRow.lease_expires_at <= stale_before,
                    )
                )
            )
        for revision_id in candidates:
            token = self._new_id()
            try:
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerRevisionRow)
                        .where(
                            ComposerRevisionRow.id == revision_id,
                            ComposerRevisionRow.publication_state == "pending",
                            ComposerRevisionRow.lease_expires_at <= stale_before,
                        )
                        .with_for_update()
                    )
                    if row is None:
                        continue
                    draft = database.get(ComposerDraftRow, row.draft_id)
                    if draft is None:
                        raise PersistenceError
                    owner_id = UUID(draft.owner_id)
                    artifact_ids = tuple(
                        UUID(value)
                        for value in database.scalars(
                            select(ComposerArtifactRow.id).where(
                                ComposerArtifactRow.revision_id == revision_id
                            )
                        )
                    )
                    row.publication_token = str(token)
                    row.lease_expires_at = self._clock() + self._publication_lease
                for artifact_id in artifact_ids:
                    self._objects.delete(
                        ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner_id, artifact_id)
                    )
                with Session(self._engine) as database, database.begin():
                    serialize_sqlite_write(database, self._engine)
                    row = database.scalar(
                        select(ComposerRevisionRow)
                        .where(
                            ComposerRevisionRow.id == revision_id,
                            ComposerRevisionRow.publication_state == "pending",
                            ComposerRevisionRow.publication_token == str(token),
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        draft = database.get(ComposerDraftRow, row.draft_id)
                        if draft is None:
                            raise PersistenceError from None
                        record_content_audit(
                            database,
                            event_id=self._new_id(),
                            owner_id=UUID(draft.owner_id),
                            actor_id=SYSTEM_ACTOR_ID,
                            operation="revision_recover",
                            target_kind="composer_revision",
                            target_id=UUID(revision_id),
                            draft_id=UUID(draft.id),
                            draft_version=draft.version,
                            created_at=self._clock(),
                        )
                        database.delete(row)
                        recovered += 1
            except ObjectStoreError:
                continue
            except SQLAlchemyError:
                raise PersistenceError from None
        return recovered

    @staticmethod
    def _require_etag(row: ComposerDraftRow, if_match: str) -> None:
        if if_match != f'"{row.version}"':
            raise ComposerConflictError("Composer draft changed")
