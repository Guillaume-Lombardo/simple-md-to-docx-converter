"""Composer content mutations leave immutable, content-free audit evidence."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.drafts import ProposalState
from markweave.composer.revisions import RevisionSnapshot
from markweave.persistence.composer import (
    SYSTEM_ACTOR_ID,
    SqlComposerAuditRepository,
    SqlConnectionRepository,
)
from markweave.persistence.errors import PersistenceError
from markweave.persistence.observability import SqlAuditReader
from markweave.persistence.schema import (
    ComposerConnectionAuditRow,
    ComposerContentAuditRow,
    ComposerPermissionAuditRow,
    RetentionCleanupRunRow,
)
from tests.integration.sqlite.test_composer_foundations import _artifacts, _repo

pytestmark = pytest.mark.integration


def test_composer_mutations_are_visible_to_admin_without_values(tmp_path: Path) -> None:
    engine, _objects, repository, owner, other, admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"private-document-marker",
            "scan-approved",
            title="private-title-marker",
            content="private-content-marker",
            media_type="text/markdown",
        )
        draft = repository.save_draft(
            owner,
            draft.id,
            if_match=draft.etag,
            title="private-title-marker",
            content="private-correction-marker",
        )
        repository.add_message(
            owner,
            draft.id,
            role="user",
            content="private-message-marker",
            if_match=draft.etag,
        )
        draft = repository.get_draft(owner, draft.id)
        proposal = repository.create_proposal(
            owner,
            draft.id,
            base_version=draft.version,
            proposed_value="private-proposal-marker",
            provenance="human",
        )
        draft = repository.get_draft(owner, draft.id)
        repository.decide_proposal(
            owner,
            draft.id,
            proposal.id,
            if_match=draft.etag,
            state=ProposalState.REJECTED,
            decided_value=None,
        )
        draft = repository.get_draft(owner, draft.id)
        revision = repository.publish_revision(
            owner,
            draft.id,
            actor_id=admin,
            if_match=draft.etag,
            idempotency_key="audit-revision",
            snapshot=RevisionSnapshot(
                draft.source, None, "{}", "{}", None, "human", "generate"
            ),
            artifacts=_artifacts(),
        )
        repository.restore_revision(
            owner,
            draft.id,
            revision.id,
            actor_id=owner,
            if_match=repository.get_draft(owner, draft.id).etag,
            idempotency_key="audit-restore",
        )
        audit = SqlComposerAuditRepository(engine)
        events = audit.list_content_audit(owner)
        operations = {event.operation for event in events}
        assert operations == {
            "source_publish",
            "draft_create",
            "draft_update",
            "message_add",
            "proposal_create",
            "proposal_rejected",
            "revision_publish",
            "revision_restore",
        }
        assert len(events) == 8
        assert audit.list_content_audit(other) == ()
        assert audit.list_content_audit(owner, limit=2, offset=1) == events[1:3]
        assert all(
            event.actor_id in {owner, admin} and event.draft_version > 0
            for event in events
        )
        admin_records = SqlAuditReader(engine).list_recent(offset=0, limit=100)
        composer_records = tuple(
            record
            for record in admin_records
            if record.id in {event.id for event in events}
        )
        assert len(composer_records) == len(events)
        published = next(
            record
            for record in composer_records
            if record.operation == "revision_publish"
        )
        assert published.actor_id == admin
        assert published.administrator_intervention
        assert {record.target_type for record in composer_records} == {
            "composer_source",
            "composer_draft",
            "composer_message",
            "composer_proposal",
            "composer_revision",
        }
        serialized = repr((events, composer_records))
        for private in (
            "private-document-marker",
            "private-title-marker",
            "private-content-marker",
            "private-correction-marker",
            "private-message-marker",
            "private-proposal-marker",
        ):
            assert private not in serialized
    finally:
        engine.dispose()


def test_audit_is_immutable_and_old_rows_have_bounded_cleanup_evidence(
    tmp_path: Path,
) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-approved",
            title="Draft",
            content="text",
            media_type="text/markdown",
        )
        with Session(engine) as database:
            event_id = database.scalar(select(ComposerContentAuditRow.id))
        assert event_id is not None
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                update(ComposerContentAuditRow)
                .where(ComposerContentAuditRow.id == event_id)
                .values(operation="forged")
            )
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                text("DELETE FROM composer_content_audit WHERE id = :id"),
                {"id": event_id},
            )
        audit = SqlComposerAuditRepository(engine)
        assert (
            audit.cleanup_content_audit(
                cutoff_at=datetime.now(UTC) - timedelta(days=1), limit=1
            )
            == 0
        )
        assert (
            audit.cleanup_content_audit(
                cutoff_at=datetime.now(UTC) + timedelta(days=1), limit=1
            )
            == 1
        )
        assert len(audit.list_content_audit(owner)) == 1
        with Session(engine) as database:
            runs = tuple(
                database.scalars(
                    select(RetentionCleanupRunRow).where(
                        RetentionCleanupRunRow.kind == "composer_content_audit"
                    )
                )
            )
        assert sorted(run.removed_count for run in runs) == [0, 1]
        assert repository.get_draft(owner, draft.id).id == draft.id
    finally:
        engine.dispose()


def test_failed_audit_insert_rolls_back_draft_change(tmp_path: Path) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-approved",
            title="Draft",
            content="original",
            media_type="text/markdown",
        )
        with Session(engine) as database, database.begin():
            database.execute(
                text(
                    "CREATE TRIGGER reject_test_composer_audit "
                    "BEFORE INSERT ON composer_content_audit "
                    "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
                )
            )
        with pytest.raises(PersistenceError):
            repository.save_draft(
                owner,
                draft.id,
                if_match=draft.etag,
                title="Draft",
                content="must-roll-back",
            )
        assert repository.get_draft(owner, draft.id) == draft
    finally:
        engine.dispose()


def test_admin_reader_includes_connection_and_permission_audit(tmp_path: Path) -> None:
    engine, _objects, _repository, owner, _other, admin = _repo(tmp_path)
    connection_id = uuid4()
    try:
        with Session(engine) as database, database.begin():
            database.add(
                ComposerConnectionAuditRow(
                    id=str(uuid4()),
                    connection_id=str(connection_id),
                    actor_id=str(admin),
                    operation="revoke",
                    scope="instance",
                    owner_id=None,
                    target_user_id=None,
                    version=3,
                    generation=4,
                    created_at=datetime.now(UTC),
                )
            )
            database.add(
                ComposerPermissionAuditRow(
                    id=str(uuid4()),
                    user_id=str(owner),
                    actor_id=str(admin),
                    enabled=False,
                    version=2,
                    created_at=datetime.now(UTC),
                )
            )
        records = SqlAuditReader(engine).list_recent(offset=0, limit=10)
        connection = next(
            record for record in records if record.target_type == "composer_connection"
        )
        assert connection.actor_id == admin
        assert connection.owner_id == SYSTEM_ACTOR_ID
        assert connection.target_id == connection_id
        assert connection.target_version == "3"
        assert connection.administrator_intervention
        permission = next(
            record
            for record in records
            if record.target_type == "composer_personal_permission"
        )
        assert permission.actor_id == admin
        assert permission.owner_id == owner
        assert permission.operation == "personal_permission_revoke"
        assert permission.target_version == "2"
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(text("DELETE FROM composer_connection_audit"))
        connections = SqlConnectionRepository(engine, maximum_allowed_users=10)
        cutoff = datetime.now(UTC) + timedelta(days=1)
        assert connections.cleanup_connection_audit(cutoff_at=cutoff, limit=1) == 1
        assert connections.cleanup_connection_audit(cutoff_at=cutoff, limit=1) == 1
        with Session(engine) as database:
            evidence = tuple(
                database.scalars(
                    select(RetentionCleanupRunRow).where(
                        RetentionCleanupRunRow.kind == "composer_connection_audit"
                    )
                )
            )
        assert len(evidence) == 2
        assert all(run.removed_count == 1 for run in evidence)
    finally:
        engine.dispose()
