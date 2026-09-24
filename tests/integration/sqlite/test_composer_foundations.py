"""Real SQLite/filesystem Composer ownership and atomic publication behavior."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.connections import (
    ConnectionActor,
    ConnectionConfigurationError,
    ConnectionNotFoundError,
    ConnectionRecord,
    ConnectionScope,
    ConnectionService,
    IdentityMode,
)
from markweave.composer.drafts import ProposalState
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerArtifactError,
    ComposerConflictError,
    ComposerNotFoundError,
    RevisionSnapshot,
    SourceReference,
)
from markweave.composer.secrets import EncryptedCredentials, SecretCipher
from markweave.persistence.composer import (
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerArtifactRow,
    ComposerConnectionAuditRow,
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerDraftRow,
    ComposerMessageRow,
    ComposerPersonalPermissionRow,
    ComposerProposalRow,
    ComposerRevisionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStoreError,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _users(engine: Engine) -> tuple:
    owner, other, admin = uuid4(), uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id, role in ((owner, "user"), (other, "user"), (admin, "admin")):
            database.add(
                UserRow(
                    id=str(user_id),
                    username=str(user_id),
                    normalized_username=str(user_id),
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role=role,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
    return owner, other, admin


def _repo(tmp_path: Path) -> tuple:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner, other, admin = _users(engine)
    objects = FilesystemObjectStore(tmp_path)
    return engine, objects, SqlComposerRepository(engine, objects), owner, other, admin


def _artifacts() -> tuple[ArtifactContent, ...]:
    return (
        ArtifactContent(
            "download",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"download",
        ),
        ArtifactContent("preview", "text/html", b"preview"),
    )


def test_reversion_source_kind_survives_storage_and_orphan_cleanup(
    tmp_path: Path,
) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        source = b"# Reverse result\n"
        job_id, result_id = uuid4(), uuid4()
        digest = hashlib.sha256(source).hexdigest()
        draft = repository.create_draft_with_source(
            owner,
            source,
            "clean-reversion",
            title="Reverse result",
            content=source.decode(),
            media_type="text/markdown",
            source_kind="reversion_result",
            origin_job_id=job_id,
            origin_result_object_id=result_id,
            origin_result_sha256=digest,
        )
        assert draft.source.kind == "reversion_result"
        assert draft.source.origin_job_id == job_id
        assert repository.get_draft(owner, draft.id).source == draft.source
        assert repository.cleanup_orphan_sources(limit=10) == 0
        assert repository.read_source(owner, draft.source.object_id) == source
    finally:
        engine.dispose()


def test_scanned_source_draft_conversation_and_copy_forward_restore(
    tmp_path: Path,
) -> None:
    engine, _objects, repository, owner, other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-approved",
            title="Draft",
            content="hello",
            media_type="text/markdown",
        )
        assert repository.read_source(owner, draft.source.object_id) == b"source"
        with pytest.raises(ComposerNotFoundError):
            repository.create_draft(other, draft.source, title="Stolen", content="")
        with pytest.raises(ComposerNotFoundError):
            repository.create_draft(
                owner,
                SourceReference(
                    "upload",
                    uuid4(),
                    owner,
                    draft.source.sha256,
                    draft.source.scan_receipt,
                    draft.source.media_type,
                ),
                title="Unattested",
                content="",
            )
        with pytest.raises(ComposerNotFoundError):
            repository.get_draft(other, draft.id)
        with pytest.raises(ComposerNotFoundError):
            repository.read_source(other, draft.source.object_id)
        message = repository.add_message(
            owner,
            draft.id,
            role="user",
            content="question",
            message_id=uuid4(),
            if_match=draft.etag,
        )
        assert (
            repository.add_message(
                owner,
                draft.id,
                role="user",
                content="question",
                message_id=message.id,
                if_match=draft.etag,
            )
            == message
        )
        fresh = repository.get_draft(owner, draft.id)
        proposal = repository.create_proposal(
            owner,
            draft.id,
            base_version=fresh.version,
            proposed_value="guess",
            provenance="model",
        )
        repository.add_message(
            owner,
            draft.id,
            role="user",
            content="A human correction",
            message_id=uuid4(),
            if_match=repository.get_draft(owner, draft.id).etag,
        )
        with pytest.raises(ComposerConflictError):
            repository.decide_proposal(
                owner,
                draft.id,
                proposal.id,
                if_match=repository.get_draft(owner, draft.id).etag,
                state=ProposalState.ACCEPTED,
                decided_value=None,
            )
        fresh = repository.get_draft(owner, draft.id)
        proposal = repository.create_proposal(
            owner,
            draft.id,
            base_version=fresh.version,
            proposed_value="guess again",
            provenance="model",
        )
        rejected = repository.decide_proposal(
            owner,
            draft.id,
            proposal.id,
            if_match=repository.get_draft(owner, draft.id).etag,
            state=ProposalState.REJECTED,
            decided_value=None,
        )
        assert rejected.state is ProposalState.REJECTED
        with pytest.raises(ComposerConflictError):
            repository.save_draft(
                owner, draft.id, if_match=draft.etag, title="stale", content="stale"
            )
        fresh = repository.get_draft(owner, draft.id)
        snapshot = RevisionSnapshot(
            draft.source,
            None,
            '{"approved":"human"}',
            "{}",
            "model-a",
            "human",
            "generate",
        )
        first = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=fresh.etag,
            idempotency_key="generate-1",
            snapshot=snapshot,
            artifacts=_artifacts(),
        )
        assert (
            repository.publish_revision(
                owner,
                draft.id,
                actor_id=owner,
                if_match=fresh.etag,
                idempotency_key="generate-1",
                snapshot=snapshot,
                artifacts=_artifacts(),
            )
            == first
        )
        with pytest.raises(ComposerNotFoundError):
            repository.read_artifact(other, draft.id, first.id, "download")
        assert (
            repository.read_artifact(owner, draft.id, first.id, "preview") == b"preview"
        )
        restored = repository.restore_revision(
            owner,
            draft.id,
            first.id,
            actor_id=owner,
            if_match=repository.get_draft(owner, draft.id).etag,
            idempotency_key="restore-1",
        )
        assert restored.number == first.number + 1
        assert restored.restored_from_revision_id == first.id
        assert restored.artifacts[0].id != first.artifacts[0].id
        assert (
            repository.read_artifact(owner, draft.id, first.id, "download")
            == b"download"
        )
        assert [item.id for item in repository.list_revisions(owner, draft.id)] == [
            first.id,
            restored.id,
        ]
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                update(ComposerRevisionRow)
                .where(ComposerRevisionRow.id == str(first.id))
                .values(approved_values='{"tampered":true}')
            )
    finally:
        engine.dispose()


class _FailingStore(FilesystemObjectStore):
    def put(self, key: ObjectKey, content: bytes) -> None:
        if key.scope is ObjectScope.COMPOSER_ARTIFACT:
            raise ObjectStoreError("Simulated object failure")
        super().put(key, content)


def test_failed_artifact_publication_is_hidden_and_recoverable(tmp_path: Path) -> None:
    engine, objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-ok",
            title="Draft",
            content="",
            media_type="text/markdown",
        )
        failing = SqlComposerRepository(engine, _FailingStore(tmp_path))
        snapshot = RevisionSnapshot(
            draft.source, None, "{}", "{}", None, "human", "generate"
        )
        with pytest.raises(ComposerArtifactError):
            failing.publish_revision(
                owner,
                draft.id,
                actor_id=owner,
                if_match=draft.etag,
                idempotency_key="failed",
                snapshot=snapshot,
                artifacts=_artifacts(),
            )
        assert repository.list_revisions(owner, draft.id) == ()
        assert repository.get_draft(owner, draft.id).current_revision_id is None
        with Session(engine) as database:
            row = database.scalar(
                select(ComposerRevisionRow).where(
                    ComposerRevisionRow.draft_id == str(draft.id)
                )
            )
            assert row is not None and row.publication_state == "pending"
            with pytest.raises(ComposerNotFoundError):
                repository.get_revision(owner, draft.id, UUID(row.id))
        recovered = repository.recover_stale_publications(
            stale_before=datetime.now(UTC) + timedelta(hours=1)
        )
        assert recovered == 1
        assert repository.list_revisions(owner, draft.id) == ()
        published = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="failed",
            snapshot=snapshot,
            artifacts=_artifacts(),
        )
        assert objects.exists(
            ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner, published.artifacts[0].id)
        )
    finally:
        engine.dispose()


def test_connection_grants_encrypted_bytes_permissions_and_generation_cas(
    tmp_path: Path,
) -> None:
    engine, _objects, _repository, owner, other, admin = _repo(tmp_path)
    try:
        connections = SqlConnectionRepository(engine, maximum_allowed_users=1000)
        record = ConnectionRecord(
            uuid4(),
            ConnectionScope.INSTANCE,
            None,
            IdentityMode.SHARED,
            "https://example.internal/v1",
            "model-a",
            ("model-a",),
            True,
            frozenset({owner}),
            0,
            0,
            name="Internal provider",
        )
        encrypted = EncryptedCredentials(api_key=b"encrypted-envelope")
        saved = connections.save_connection(
            record, expected_version=None, actor_id=admin, credentials=encrypted
        )
        assert saved.version == saved.generation == 1
        assert saved.allowed_user_ids == frozenset({owner})
        assert other not in saved.allowed_user_ids
        assert saved.has_api_key
        assert connections.get_credentials(saved.id, None) == encrypted
        assert connections.set_outage(
            saved.id, None, True, expected_generation=saved.generation
        )
        changed = connections.save_connection(
            saved, expected_version=saved.version, actor_id=admin
        )
        assert changed.generation == 2 and not connections.get_outage(saved.id, None)
        assert not connections.set_outage(
            saved.id, None, True, expected_generation=saved.generation
        )
        with pytest.raises(Exception, match="changed"):
            connections.save_connection(
                saved, expected_version=saved.version, actor_id=admin
            )
        with Session(engine) as database:
            operations = tuple(
                database.scalars(
                    select(ComposerConnectionAuditRow.operation)
                    .where(ComposerConnectionAuditRow.connection_id == str(saved.id))
                    .order_by(
                        ComposerConnectionAuditRow.created_at,
                        ComposerConnectionAuditRow.id,
                    )
                )
            )
        assert set(operations) >= {
            "create",
            "grants_update",
            "credential_rotate",
            "update",
        }
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                update(ComposerConnectionAuditRow)
                .where(ComposerConnectionAuditRow.connection_id == str(saved.id))
                .values(operation="tampered")
            )
        assert not connections.can_manage_personal(owner)
        assert (
            connections.set_personal_permission(
                owner, True, expected_version=0, actor_id=admin
            )
            == 1
        )
        assert connections.can_manage_personal(owner)
        assert (
            connections.set_personal_permission(
                owner, False, expected_version=1, actor_id=admin
            )
            == 2
        )
        assert not connections.can_manage_personal(owner)
        assert (
            connections.cleanup_connection_audit(
                cutoff_at=datetime.now(UTC) + timedelta(hours=1), limit=100
            )
            >= 4
        )
    finally:
        engine.dispose()


def test_individual_provider_outage_does_not_hide_other_users(tmp_path: Path) -> None:
    engine, _objects, _repository, owner, other, admin = _repo(tmp_path)
    try:
        connections = SqlConnectionRepository(engine, maximum_allowed_users=1000)
        record = ConnectionRecord(
            uuid4(),
            ConnectionScope.INSTANCE,
            None,
            IdentityMode.INDIVIDUAL,
            "https://example.internal/v1",
            "model-a",
            ("model-a",),
            True,
            frozenset({owner, other}),
            0,
            0,
            name="Individual provider",
        )
        created = connections.save_connection(
            record, expected_version=None, actor_id=admin
        )
        first = connections.save_connection(
            created,
            expected_version=created.version,
            actor_id=admin,
            credential_user_id=owner,
            credentials=EncryptedCredentials(api_key=b"cipher-one"),
        )
        second = connections.save_connection(
            first,
            expected_version=first.version,
            actor_id=admin,
            credential_user_id=other,
            credentials=EncryptedCredentials(api_key=b"cipher-two"),
        )
        assert connections.set_outage(
            second.id, owner, True, expected_generation=second.generation
        )
        assert connections.get_outage(second.id, owner)
        assert not connections.get_outage(second.id, other)
        assert not connections.get_outage(second.id, None)
        rotated = connections.save_connection(
            second,
            expected_version=second.version,
            actor_id=admin,
            credential_user_id=owner,
            credentials=EncryptedCredentials(api_key=b"cipher-one-rotated"),
        )
        assert not connections.get_outage(rotated.id, owner)
        assert not connections.set_outage(
            second.id, other, True, expected_generation=second.generation
        )
        revoked = connections.revoke_connection(
            rotated, expected_version=rotated.version, actor_id=admin
        )
        assert not revoked.enabled and not revoked.allowed_user_ids
        assert connections.get_credentials(revoked.id, owner) is None
        assert connections.get_credentials(revoked.id, other) is None
        with pytest.raises(Exception, match="changed"):
            connections.revoke_connection(
                rotated, expected_version=rotated.version, actor_id=admin
            )
        reenabled = connections.save_connection(
            replace(revoked, enabled=True, allowed_user_ids=frozenset({owner, other})),
            expected_version=revoked.version,
            actor_id=admin,
        )
        assert reenabled.enabled
        assert connections.get_credentials(reenabled.id, owner) is None
        assert connections.get_credentials(reenabled.id, other) is None
        with Session(engine) as database:
            assert "revoke" in tuple(
                database.scalars(
                    select(ComposerConnectionAuditRow.operation).where(
                        ComposerConnectionAuditRow.connection_id == str(revoked.id)
                    )
                )
            )
    finally:
        engine.dispose()


def test_stale_model_proposal_and_reused_idempotency_cannot_replace_human_edit(
    tmp_path: Path,
) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-ok",
            title="Draft",
            content="first",
            media_type="text/markdown",
        )
        edited = repository.save_draft(
            owner,
            draft.id,
            if_match=draft.etag,
            title="Draft",
            content="human correction",
        )
        with pytest.raises(ComposerConflictError):
            repository.create_proposal(
                owner,
                draft.id,
                base_version=draft.version,
                proposed_value="stale model text",
                provenance="model",
            )
        proposal = repository.create_proposal(
            owner,
            draft.id,
            base_version=edited.version,
            proposed_value="model text",
            provenance="model",
            proposal_id=uuid4(),
        )
        assert (
            repository.create_proposal(
                owner,
                draft.id,
                base_version=edited.version,
                proposed_value="model text",
                provenance="model",
                proposal_id=proposal.id,
            )
            == proposal
        )
        with pytest.raises(ComposerConflictError, match="idempotency"):
            repository.create_proposal(
                owner,
                draft.id,
                base_version=edited.version + 1,
                proposed_value="model text",
                provenance="model",
                proposal_id=proposal.id,
            )
        current = repository.get_draft(owner, draft.id)
        accepted = repository.decide_proposal(
            owner,
            draft.id,
            proposal.id,
            if_match=current.etag,
            state=ProposalState.ACCEPTED,
            decided_value=None,
        )
        assert accepted.decided_value == "model text"
        assert (
            repository.decide_proposal(
                owner,
                draft.id,
                proposal.id,
                if_match=current.etag,
                state=ProposalState.ACCEPTED,
                decided_value=None,
            )
            == accepted
        )
        with pytest.raises(ComposerConflictError):
            repository.decide_proposal(
                owner,
                draft.id,
                proposal.id,
                if_match=current.etag,
                state=ProposalState.REJECTED,
                decided_value=None,
            )
        current = repository.get_draft(owner, draft.id)
        snapshot = RevisionSnapshot(
            draft.source,
            None,
            '{"field":"human correction"}',
            "{}",
            "model-a",
            "human",
            "generate",
        )
        revision = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=current.etag,
            idempotency_key="approved",
            snapshot=snapshot,
            artifacts=_artifacts(),
        )
        assert revision.snapshot.approved_values == '{"field":"human correction"}'
        with pytest.raises(ComposerConflictError):
            repository.publish_revision(
                owner,
                draft.id,
                actor_id=owner,
                if_match=current.etag,
                idempotency_key="approved",
                snapshot=RevisionSnapshot(
                    draft.source,
                    None,
                    '{"field":"model text"}',
                    "{}",
                    "model-a",
                    "model",
                    "generate",
                ),
                artifacts=_artifacts(),
            )
    finally:
        engine.dispose()


class _FailingSourceStore(FilesystemObjectStore):
    def put(self, key: ObjectKey, content: bytes) -> None:
        if key.scope is ObjectScope.COMPOSER_SOURCE:
            raise ObjectStoreError("Simulated source failure")
        super().put(key, content)


def test_failed_scanned_source_is_hidden_and_recovered(tmp_path: Path) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        failing = SqlComposerRepository(engine, _FailingSourceStore(tmp_path))
        with pytest.raises(ComposerArtifactError):
            failing.create_draft_with_source(
                owner,
                b"scanned",
                "scan-ok",
                title="Draft",
                content="",
                media_type="text/markdown",
            )
        assert repository.list_drafts(owner) == ()
        assert (
            repository.recover_stale_sources(
                stale_before=datetime.now(UTC) + timedelta(hours=1)
            )
            == 1
        )
        assert (
            repository.create_draft_with_source(
                owner,
                b"scanned",
                "scan-ok",
                title="Draft",
                content="",
                media_type="text/markdown",
            ).version
            == 1
        )
    finally:
        engine.dispose()


def test_handoff_origin_is_frozen_and_changed_copy_cannot_reuse_proof(
    tmp_path: Path,
) -> None:
    engine, _objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        job_id, result_id = uuid4(), uuid4()
        original = b"verified conversion result"
        digest = hashlib.sha256(original).hexdigest()
        draft = repository.create_draft_with_source(
            owner,
            original,
            "scan-approved",
            title="Converted",
            content="",
            media_type="application/pdf",
            origin_job_id=job_id,
            origin_result_object_id=result_id,
            origin_result_sha256=digest,
        )
        assert draft.source.origin_job_id == job_id
        assert draft.source.kind == "conversion_result"
        assert draft.source.origin_result_object_id == result_id
        assert draft.source.origin_result_sha256 == draft.source.sha256
        assert repository.read_source(owner, draft.source.object_id) == original
        with pytest.raises(ValueError, match="differ"):
            repository.create_draft_with_source(
                owner,
                b"changed copy",
                "scan-approved",
                title="Changed",
                content="",
                media_type="application/pdf",
                origin_job_id=job_id,
                origin_result_object_id=result_id,
                origin_result_sha256=digest,
            )
        assert len(repository.list_drafts(owner)) == 1
    finally:
        engine.dispose()


def test_owner_history_lists_are_sql_bounded_and_stably_paged(tmp_path: Path) -> None:
    engine, _objects, repository, owner, other, _admin = _repo(tmp_path)
    try:
        anchor = repository.create_draft_with_source(
            owner,
            b"source",
            "scan-ok",
            title="Anchor",
            content="",
            media_type="text/markdown",
        )
        now = datetime.now(UTC)
        with Session(engine) as database, database.begin():
            anchor_row = database.get(ComposerDraftRow, str(anchor.id))
            assert anchor_row is not None
            source_reference = anchor_row.source_reference
            artifacts = []
            for number in range(1, 111):
                database.add(
                    ComposerDraftRow(
                        id=str(UUID(int=number)),
                        owner_id=str(owner),
                        title=str(number),
                        source_reference=source_reference,
                        content="",
                        state="active",
                        version=1,
                        current_revision_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                database.add(
                    ComposerMessageRow(
                        id=str(UUID(int=number + 1000)),
                        draft_id=str(anchor.id),
                        role="user",
                        content=str(number),
                        created_at=now,
                    )
                )
                database.add(
                    ComposerProposalRow(
                        id=str(UUID(int=number + 2000)),
                        draft_id=str(anchor.id),
                        base_version=1,
                        state="pending",
                        proposed_value=str(number),
                        decided_value=None,
                        provenance="test",
                        created_at=now,
                        decided_at=None,
                        decided_by=None,
                    )
                )
                revision_id = str(UUID(int=number + 3000))
                database.add(
                    ComposerRevisionRow(
                        id=revision_id,
                        draft_id=str(anchor.id),
                        number=number,
                        actor_id=str(owner),
                        expected_draft_version=1,
                        source_reference=source_reference,
                        template_reference=None,
                        approved_values="{}",
                        render_options="{}",
                        model_identity=None,
                        provenance="test",
                        operation="capture_source",
                        restored_from_revision_id=None,
                        idempotency_key=f"history-{number}",
                        request_digest="a" * 64,
                        publication_state="published",
                        publication_token=None,
                        lease_expires_at=None,
                        created_at=now,
                    )
                )
                for kind, offset in (("download", 4000), ("preview", 5000)):
                    artifacts.append(
                        ComposerArtifactRow(
                            id=str(UUID(int=number + offset)),
                            revision_id=revision_id,
                            kind=kind,
                            sha256="a" * 64,
                            size=1,
                            media_type="text/markdown",
                        )
                    )
            database.flush()
            database.add_all(artifacts)
        drafts = repository.list_drafts(owner, limit=100)
        assert len(repository.list_drafts(owner)) == 50
        assert len(drafts) == 100
        assert len(repository.list_drafts(owner, limit=100, offset=100)) == 11
        assert {item.id for item in drafts}.isdisjoint(
            item.id for item in repository.list_drafts(owner, limit=100, offset=100)
        )
        for method in (
            repository.list_messages,
            repository.list_proposals,
            repository.list_revisions,
        ):
            first = method(owner, anchor.id, limit=100)
            second = method(owner, anchor.id, limit=100, offset=100)
            assert len(method(owner, anchor.id)) == 50
            assert len(first) == 100 and len(second) == 10
            assert {item.id for item in first}.isdisjoint(item.id for item in second)
            assert first == method(owner, anchor.id, limit=100)
            newest = method(owner, anchor.id, limit=100, order="desc")
            older = method(owner, anchor.id, limit=100, offset=100, order="desc")
            assert len(newest) == 100 and len(older) == 10
            assert [item.id for item in newest + older] == [
                item.id for item in reversed(first + second)
            ]
            assert newest == method(owner, anchor.id, limit=100, order="desc")
            with pytest.raises(ComposerNotFoundError):
                method(other, anchor.id, limit=1, offset=100)
            with pytest.raises(ComposerNotFoundError):
                method(other, anchor.id, limit=1, order="desc")
            with pytest.raises(ValueError, match="order"):
                method(owner, anchor.id, order="invalid")
        assert [
            item.number
            for item in repository.list_revisions(owner, anchor.id, limit=3, offset=50)
        ] == [51, 52, 53]
        assert [
            item.number
            for item in repository.list_revisions(
                owner, anchor.id, limit=3, offset=50, order="desc"
            )
        ] == [60, 59, 58]
        for limit, offset in ((0, 0), (101, 0), (1, -1), (True, 0)):
            with pytest.raises(ValueError, match="page"):
                repository.list_drafts(owner, limit=limit, offset=offset)
    finally:
        engine.dispose()


def test_connection_and_permission_pages_filter_before_sql_limit(  # noqa: PLR0915 - long-history fixture and all page assertions
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    engine, _objects, _repository, owner, other, admin = _repo(tmp_path)
    try:
        connections = SqlConnectionRepository(engine, maximum_allowed_users=1000)
        with Session(engine) as database, database.begin():
            permissions = []
            grants = []
            for number in range(1, 111):
                user_id = str(UUID(int=number + 10000))
                database.add(
                    UserRow(
                        id=user_id,
                        username=f"user-{number}",
                        normalized_username=f"user-{number}",
                        password_hash="hash",  # noqa: S106 - isolated fixture
                        role="user",
                        active=True,
                        auth_version=0,
                        password_change_required=False,
                    )
                )
                if number % 2 == 0:
                    permissions.append(
                        ComposerPersonalPermissionRow(
                            user_id=user_id, enabled=number % 4 == 0, version=1
                        )
                    )
                scope = "personal" if number % 3 == 0 else "instance"
                connection_id = str(UUID(int=number + 20000))
                database.add(
                    ComposerConnectionRow(
                        id=connection_id,
                        scope=scope,
                        owner_id=str(owner) if scope == "personal" else None,
                        identity_mode="individual" if scope == "personal" else "shared",
                        name=f"Connection {number}",
                        endpoint="https://example.internal/v1",
                        selected_model=None,
                        permitted_models="[]",
                        enabled=True,
                        version=1,
                        generation=1,
                        outage=False,
                    )
                )
                if number % 3 == 1:
                    grants.append(
                        ComposerConnectionGrantRow(
                            connection_id=connection_id, user_id=str(owner)
                        )
                    )
            database.flush()
            database.add_all((*permissions, *grants))
        assert len(connections.list_connections()) == 50
        assert len(connections.list_connections(limit=100, offset=100)) == 10
        visible = connections.list_visible_connections(owner, False, limit=100)
        assert len(visible) == 73
        assert [record.id for record in visible] == [
            UUID(int=number + 20000) for number in range(1, 111) if number % 3 in (0, 1)
        ]
        assert len(connections.list_visible_connections(owner, False)) == 50
        assert (
            len(
                connections.list_visible_connections(owner, False, limit=100, offset=50)
            )
            == 23
        )
        assert connections.list_visible_connections(other, False) == ()
        assert len(connections.list_visible_connections(admin, True, limit=100)) == 74
        actor = ConnectionActor(owner, is_admin=False, can_manage_personal=False)
        service = ConnectionService(
            connections, SecretCipher(b"s" * 32), mocker.Mock(), mocker.Mock()
        )
        assert len(service.list_visible(actor)) == 50
        assert service.get_visible(actor, visible[60].id).id == visible[60].id
        with pytest.raises(ConnectionNotFoundError):
            service.get_visible(actor, UUID(int=20002))
        assert len(connections.list_personal_permissions()) == 50
        assert len(connections.list_personal_permissions(limit=100, offset=100)) == 13
        assert connections.get_personal_permission_details(owner) == (
            owner,
            str(owner),
            False,
            0,
        )
        assert connections.get_personal_permission_details(UUID(int=10004)) == (
            UUID(int=10004),
            "user-4",
            True,
            1,
        )
        assert connections.get_personal_permission_details(UUID(int=999999)) is None
        assert connections.list_personal_permissions(
            limit=5
        ) == connections.list_personal_permissions(limit=5)
        for limit, offset in ((0, 0), (101, 0), (1, -1)):
            with pytest.raises(ValueError, match="page"):
                connections.list_visible_connections(
                    owner, False, limit=limit, offset=offset
                )
            with pytest.raises(ValueError, match="page"):
                connections.list_personal_permissions(limit=limit, offset=offset)
        healthy = connections.get_connection(UUID(int=20002))
        assert healthy is not None
        oversized_record = replace(
            healthy,
            allowed_user_ids=frozenset(
                UUID(int=30000 + number) for number in range(1001)
            ),
        )
        with pytest.raises(ConnectionConfigurationError, match="grant limit"):
            connections.save_connection(
                oversized_record, expected_version=healthy.version, actor_id=admin
            )
        unchanged = connections.get_connection(healthy.id)
        assert unchanged is not None and unchanged.version == healthy.version
        with Session(engine) as database, database.begin():
            for number in range(1000):
                user_id = str(UUID(int=30000 + number))
                database.add(
                    UserRow(
                        id=user_id,
                        username=f"overflow-{number}",
                        normalized_username=f"overflow-{number}",
                        password_hash="hash",  # noqa: S106 - isolated fixture
                        role="user",
                        active=True,
                        auth_version=0,
                        password_change_required=False,
                    )
                )
            database.flush()
            database.add_all(
                ComposerConnectionGrantRow(
                    connection_id=str(UUID(int=20001)),
                    user_id=str(UUID(int=30000 + number)),
                )
                for number in range(999)
            )
        assert len(connections.list_connections(limit=1)[0].allowed_user_ids) == 1000
        with Session(engine) as database, database.begin():
            database.add(
                ComposerConnectionGrantRow(
                    connection_id=str(UUID(int=20001)),
                    user_id=str(UUID(int=30999)),
                )
            )
        with pytest.raises(PersistenceError):
            connections.list_connections(limit=1)
        with pytest.raises(PersistenceError):
            connections.list_visible_connections(owner, False, limit=1)
        assert len(connections.list_connections(limit=1, offset=1)) == 1
        configured = SqlConnectionRepository(engine, maximum_allowed_users=1001)
        assert len(configured.list_connections(limit=1)[0].allowed_user_ids) == 1001
        with Session(engine) as database, database.begin():
            user_id = str(UUID(int=31000))
            database.add(
                UserRow(
                    id=user_id,
                    username="overflow-1000",
                    normalized_username="overflow-1000",
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role="user",
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
            database.flush()
            database.add(
                ComposerConnectionGrantRow(
                    connection_id=str(UUID(int=20001)), user_id=user_id
                )
            )
        with pytest.raises(PersistenceError):
            configured.list_connections(limit=1)
        with pytest.raises(ConnectionConfigurationError, match="grant limit"):
            configured.save_connection(
                replace(
                    healthy,
                    allowed_user_ids=frozenset(
                        UUID(int=30000 + number) for number in range(1002)
                    ),
                ),
                expected_version=healthy.version,
                actor_id=admin,
            )
        disabled = SqlConnectionRepository(engine)
        with pytest.raises(ConnectionConfigurationError, match="not configured"):
            disabled.list_connections(limit=1)
        with pytest.raises(ConnectionConfigurationError, match="not configured"):
            disabled.get_credentials(healthy.id, None)
        with pytest.raises(ConnectionConfigurationError, match="not configured"):
            disabled.save_connection(
                healthy, expected_version=healthy.version, actor_id=admin
            )
        assert disabled.get_personal_permission(owner) == (False, 0)
        with pytest.raises(ValueError, match="positive"):
            SqlConnectionRepository(engine, maximum_allowed_users=0)
    finally:
        engine.dispose()
