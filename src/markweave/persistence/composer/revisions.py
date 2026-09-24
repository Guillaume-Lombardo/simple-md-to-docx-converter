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
    direct_publish_lineage,
)
from markweave.persistence.composer.audit import SYSTEM_ACTOR_ID, record_content_audit
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.common import (
    DEFAULT_PAGE_LIMIT,
    PageOrder,
    owned_draft,
    source_from_json,
    source_json,
    utc,
    validate_page,
    validate_page_order,
)
from markweave.persistence.composer.typed_templates import SqlTypedTemplateRepository
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerDraftRow,
    ComposerFillPlanRow,
    ComposerModelStepRow,
    ComposerProposalRow,
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
_SHA256_HEX_LENGTH = 64
_MATCHING_ARTIFACT_COUNT = 2


def _snapshot(row: ComposerRevisionRow) -> RevisionSnapshot:
    return RevisionSnapshot(
        source=source_from_json(row.source_reference),
        template_reference=row.template_reference,
        approved_values=row.approved_values,
        render_options=row.render_options,
        model_identity=row.model_identity,
        provenance=row.provenance,
        operation=row.operation,
        typed_fill_snapshot=row.typed_fill_snapshot,
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
        "typed_fill_snapshot": snapshot.typed_fill_snapshot,
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

    @staticmethod
    def _require_proposal_author_access(
        database: Session,
        owner_id: UUID,
        draft_id: UUID,
        proposal: ComposerProposalRow,
    ) -> None:
        """Fence the model step's frozen author grants in the publication transaction."""

        if not proposal.provenance.startswith("model-step:"):
            return
        try:
            step_id = UUID(proposal.provenance.removeprefix("model-step:"))
        except ValueError:
            raise ComposerConflictError("Composer proposal origin changed") from None
        step = database.scalar(
            select(ComposerModelStepRow)
            .where(
                ComposerModelStepRow.id == str(step_id),
                ComposerModelStepRow.draft_id == str(draft_id),
                ComposerModelStepRow.owner_id == str(owner_id),
                ComposerModelStepRow.proposal_id == proposal.id,
                ComposerModelStepRow.state == "completed",
            )
            .with_for_update()
        )
        if step is None:
            raise ComposerConflictError("Composer proposal origin changed")
        try:
            refs = json.loads(step.author_refs)
            if not isinstance(refs, list):
                raise ValueError
            seen: set[UUID] = set()
            for ref in refs:
                if (
                    not isinstance(ref, dict)
                    or set(ref) != {"id", "version"}
                    or type(ref["version"]) is not int
                    or ref["version"] <= 0
                ):
                    raise ValueError
                author_id = UUID(ref["id"])
                if author_id in seen:
                    raise ValueError
                seen.add(author_id)
                SqlAuthorKnowledgeRepository.require_access(
                    database,
                    owner_id,
                    author_id,
                    expected_version=ref["version"],
                    for_update=True,
                )
        except ValueError, TypeError, LookupError, RuntimeError:
            raise ComposerConflictError("Author access changed") from None

    @staticmethod
    def _validate_typed_fill(  # noqa: PLR0912, PLR0913, PLR0915, PLR0917 - publication fences
        database: Session,
        owner_id: UUID,
        draft_id: UUID,
        draft: ComposerDraftRow,
        snapshot: RevisionSnapshot,
        artifacts: tuple[ArtifactContent, ...] | None = None,
    ) -> None:
        if snapshot.typed_fill_snapshot is None:
            if snapshot.operation in {"fill_template", "regenerate_fill"}:
                raise ComposerConflictError("Typed fill snapshot is missing")
            return
        if snapshot.operation not in {"fill_template", "regenerate_fill", "restore"}:
            raise ComposerConflictError("Typed fill operation is invalid")
        try:
            data = json.loads(snapshot.typed_fill_snapshot)
            template_version_id = UUID(data["template_version_id"])
            template_sha256 = data["template_docx_sha256"]
            schema_sha256 = data["template_schema_sha256"]
            approved_values = data["approved_values"]
            author_refs = data["author_refs"]
            source_revision_id = UUID(data["source_revision_id"])
            source_sha256 = data["source_sha256"]
            result_sha256 = data["result_sha256"]
        except KeyError, ValueError, TypeError, AttributeError:
            raise ComposerConflictError("Typed fill snapshot is invalid") from None
        if (
            not isinstance(data, dict)
            or not isinstance(approved_values, dict)
            or not isinstance(author_refs, list)
            or not all(
                isinstance(value, str) and len(value) == _SHA256_HEX_LENGTH
                for value in (
                    template_sha256,
                    schema_sha256,
                    source_sha256,
                    result_sha256,
                )
            )
        ):
            raise ComposerConflictError("Typed fill snapshot is invalid")
        if snapshot.approved_values != "{}" or snapshot.template_reference is not None:
            raise ComposerConflictError("Typed fill metadata is inconsistent")
        if snapshot.source.sha256 != source_sha256:
            raise ComposerConflictError("Typed fill source changed")
        if artifacts is not None:
            downloads = [
                item for item in artifacts if item.kind in {"download", "preview"}
            ]
            if (
                len(downloads) != _MATCHING_ARTIFACT_COUNT
                or any(
                    item.media_type
                    != "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    or hashlib.sha256(item.content).hexdigest() != result_sha256
                    for item in downloads
                )
                or downloads[0].content != downloads[1].content
            ):
                raise ComposerConflictError("Typed fill artifacts do not match")
        if snapshot.operation == "restore":
            return
        try:
            SqlTypedTemplateRepository.require_version_access(
                database,
                owner_id,
                template_version_id,
                expected_docx_sha256=template_sha256,
                expected_schema_sha256=schema_sha256,
                for_update=True,
            )
        except LookupError, RuntimeError:
            raise ComposerConflictError("Typed template access changed") from None
        if snapshot.operation == "regenerate_fill":
            try:
                parent_id = UUID(data["parent_revision_id"])
            except KeyError, ValueError, TypeError:
                raise ComposerConflictError("Regeneration parent is invalid") from None
            parent = database.scalar(
                select(ComposerRevisionRow).where(
                    ComposerRevisionRow.id == str(parent_id),
                    ComposerRevisionRow.draft_id == str(draft_id),
                    ComposerRevisionRow.publication_state == "published",
                )
            )
            if parent is None or parent.typed_fill_snapshot is None:
                raise ComposerConflictError("Regeneration source is unavailable")
            try:
                frozen = json.loads(parent.typed_fill_snapshot)
            except ValueError:
                raise ComposerConflictError("Regeneration source is invalid") from None
            if any(
                frozen.get(key) != data.get(key)
                for key in (
                    "template_version_id",
                    "template_docx_sha256",
                    "template_schema_sha256",
                    "approved_values",
                    "provenance",
                    "author_refs",
                    "source_revision_id",
                    "source_sha256",
                    "result_sha256",
                    "component_versions",
                )
            ):
                raise ComposerConflictError("Regeneration inputs changed")
            return
        try:
            plan_id = UUID(data["fill_plan_id"])
            plan_version = int(data["fill_plan_version"])
        except KeyError, ValueError, TypeError:
            raise ComposerConflictError("Fill plan reference is invalid") from None
        plan = database.scalar(
            select(ComposerFillPlanRow)
            .where(
                ComposerFillPlanRow.id == str(plan_id),
                ComposerFillPlanRow.draft_id == str(draft_id),
                ComposerFillPlanRow.owner_id == str(owner_id),
            )
            .with_for_update()
        )
        if (
            plan is None
            or plan.state != "approved"
            or plan.version != plan_version
            or plan.draft_version != draft.version
            or draft.current_revision_id != str(source_revision_id)
            or plan.source_revision_id != str(source_revision_id)
            or plan.template_version_id != str(template_version_id)
            or plan.template_docx_sha256 != template_sha256
            or plan.template_schema_sha256 != schema_sha256
            or json.loads(plan.values_json) != approved_values
            or json.loads(plan.provenance_json) != data.get("provenance")
            or json.loads(plan.author_refs) != author_refs
            or json.loads(plan.questions_json)
        ):
            raise ComposerConflictError("Fill plan changed before publication")
        try:
            for ref in author_refs:
                SqlAuthorKnowledgeRepository.require_access(
                    database,
                    owner_id,
                    UUID(ref["id"]),
                    expected_version=int(ref["version"]),
                    for_update=True,
                )
        except LookupError, RuntimeError, KeyError, ValueError, TypeError:
            raise ComposerConflictError("Author access changed") from None

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
        approved_proposal_id: UUID | None = None,
    ) -> ComposerRevision:
        if not idempotency_key or len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError("Revision idempotency key is invalid")
        kinds = {artifact.kind for artifact in artifacts}
        if kinds not in (
            {"download", "preview"},
            {"source", "download", "preview"},
            {"download", "preview", "traceability"},
            {"source", "download", "preview", "traceability"},
        ):
            raise ValueError("Matching download and preview artifacts are required")
        if len(kinds) != len(artifacts):
            raise ValueError("Duplicate Composer artifact kind")
        if snapshot.source.owner_id != owner_id:
            raise ComposerNotFoundError("Composer source does not exist")
        digest = _request_digest(snapshot, artifacts, restored_from_revision_id)
        now = self._clock()
        token = self._new_id()
        pairs: list[tuple[ArtifactContent, UUID]] = []
        approved_draft_content: str | None = None
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
                self._validate_typed_fill(
                    database, owner_id, draft_id, draft, snapshot, artifacts
                )
                if snapshot.operation == "publish_draft":
                    parent = (
                        database.get(ComposerRevisionRow, draft.current_revision_id)
                        if draft.current_revision_id is not None
                        else None
                    )
                    try:
                        approved_content = draft.content.encode("utf-8")
                    except UnicodeEncodeError:
                        raise ComposerConflictError(
                            "Current Markdown content is invalid"
                        ) from None
                    asset_source = next(
                        (
                            artifact
                            for artifact in artifacts
                            if artifact.kind == "source"
                        ),
                        None,
                    )
                    if (
                        draft.source_reference != source_json(snapshot.source)
                        or snapshot.source.media_type
                        not in {"text/markdown", "application/zip"}
                        or not draft.content.strip()
                        or snapshot.approved_values
                        != json.dumps({"content": draft.content}, ensure_ascii=False)
                        or snapshot.provenance != "human:direct"
                        or snapshot.template_reference is not None
                        or snapshot.model_identity is not None
                        or snapshot.render_options
                        != direct_publish_lineage(
                            UUID(parent.id) if parent else None,
                            parent.model_identity if parent else None,
                            parent.render_options if parent else None,
                        )
                        or approved_proposal_id is not None
                        or restored_from_revision_id is not None
                        or any(
                            artifact.media_type != "text/markdown"
                            or artifact.content != approved_content
                            for artifact in artifacts
                            if artifact.kind in {"download", "preview"}
                        )
                        or (
                            snapshot.source.media_type == "application/zip"
                            and (
                                asset_source is None
                                or asset_source.media_type != "application/zip"
                                or hashlib.sha256(asset_source.content).hexdigest()
                                != snapshot.source.sha256
                            )
                        )
                        or (
                            snapshot.source.media_type == "text/markdown"
                            and asset_source is not None
                        )
                    ):
                        raise ComposerConflictError(
                            "Current Markdown draft changed before publication"
                        )
                if approved_proposal_id is not None:
                    proposal_asset = next(
                        (
                            artifact
                            for artifact in artifacts
                            if artifact.kind == "source"
                        ),
                        None,
                    )
                    proposal = database.scalar(
                        select(ComposerProposalRow)
                        .where(
                            ComposerProposalRow.id == str(approved_proposal_id),
                            ComposerProposalRow.draft_id == str(draft_id),
                        )
                        .with_for_update()
                    )
                    if (
                        proposal is None
                        or proposal.state not in {"accepted", "edited"}
                        or proposal.decided_value is None
                        or draft.version != proposal.base_version + 2
                        or snapshot.approved_values
                        != json.dumps(
                            {"content": proposal.decided_value}, ensure_ascii=False
                        )
                        or snapshot.operation
                        != f"publish_proposal:{approved_proposal_id}"
                        or any(
                            artifact.media_type != "text/markdown"
                            or artifact.content
                            != proposal.decided_value.encode("utf-8")
                            for artifact in artifacts
                            if artifact.kind in {"download", "preview"}
                        )
                        or (
                            snapshot.source.media_type == "application/zip"
                            and (
                                proposal_asset is None
                                or proposal_asset.media_type != "application/zip"
                                or hashlib.sha256(proposal_asset.content).hexdigest()
                                != snapshot.source.sha256
                            )
                        )
                        or (
                            snapshot.source.media_type == "text/markdown"
                            and proposal_asset is not None
                        )
                    ):
                        raise ComposerConflictError(
                            "Composer proposal requires review again"
                        )
                    self._require_proposal_author_access(
                        database, owner_id, draft_id, proposal
                    )
                    approved_draft_content = proposal.decided_value
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
                    typed_fill_snapshot=snapshot.typed_fill_snapshot,
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
                self._validate_typed_fill(
                    database,
                    owner_id,
                    draft_id,
                    draft,
                    _snapshot(reserved),
                    artifacts,
                )
                if approved_proposal_id is not None:
                    proposal = database.scalar(
                        select(ComposerProposalRow)
                        .where(
                            ComposerProposalRow.id == str(approved_proposal_id),
                            ComposerProposalRow.draft_id == str(draft_id),
                        )
                        .with_for_update()
                    )
                    if proposal is None:
                        raise ComposerConflictError("Composer proposal origin changed")
                    self._require_proposal_author_access(
                        database, owner_id, draft_id, proposal
                    )
                reserved.publication_state = "published"
                reserved.publication_token = None
                reserved.lease_expires_at = None
                draft.current_revision_id = reserved.id
                if approved_draft_content is not None:
                    draft.content = approved_draft_content
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

    def find_revision_by_key(
        self, owner_id: UUID, draft_id: UUID, idempotency_key: str
    ) -> ComposerRevision | None:
        """Recover a committed publication after its linking response was lost."""

        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                row = database.scalar(
                    select(ComposerRevisionRow).where(
                        ComposerRevisionRow.draft_id == str(draft_id),
                        ComposerRevisionRow.idempotency_key == idempotency_key,
                        ComposerRevisionRow.publication_state == "published",
                    )
                )
                return _revision(database, row) if row is not None else None
        except SQLAlchemyError:
            raise PersistenceError from None

    def list_revisions(
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        order: PageOrder = "asc",
    ) -> tuple[ComposerRevision, ...]:
        validate_page(limit, offset)
        validate_page_order(order)
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
                        .order_by(
                            ComposerRevisionRow.number.desc()
                            if order == "desc"
                            else ComposerRevisionRow.number.asc(),
                            ComposerRevisionRow.id.desc()
                            if order == "desc"
                            else ComposerRevisionRow.id.asc(),
                        )
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
