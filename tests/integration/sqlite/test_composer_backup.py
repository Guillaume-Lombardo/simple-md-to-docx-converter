"""Standalone Composer backup and isolated restore retain exact revisions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from markweave.composer.revisions import (
    ArtifactContent,
    ComposerNotFoundError,
    RevisionSnapshot,
)
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.sql import create_database_engine
from markweave.recovery_adapters import RecoveryDeadline, StandaloneRecoveryAdapter
from markweave.recovery_manifest import RecoveryError
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope
from tests.integration.sqlite.test_composer_foundations import _repo

pytestmark = pytest.mark.integration


def test_standalone_restore_verifies_composer_source_and_exact_revision(
    tmp_path: Path,
) -> None:
    data = (tmp_path / "source").resolve()
    data.mkdir()
    engine, _objects, repository, owner, _other, _admin = _repo(data)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"scanned",
            "scan-ok",
            title="Draft",
            content="hello",
            media_type="text/markdown",
        )
        revision = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="backup",
            snapshot=RevisionSnapshot(
                draft.source, None, "{}", "{}", None, "human", "generate"
            ),
            artifacts=(
                ArtifactContent("download", "text/plain", b"download"),
                ArtifactContent("preview", "text/html", b"preview"),
            ),
        )
    finally:
        engine.dispose()

    staging = (tmp_path / "backup").resolve()
    staging.mkdir()
    adapter = StandaloneRecoveryAdapter()
    database_backup, object_backup = adapter.backup(
        data, staging, RecoveryDeadline.after(10)
    )
    members = (*database_backup.members, *object_backup.members)
    target = (tmp_path / "restored").resolve()
    adapter.restore(staging, target, members, RecoveryDeadline.after(10))
    restored_engine = create_database_engine(
        f"sqlite+pysqlite:///{target / 'metadata.sqlite3'}"
    )
    try:
        restored = SqlComposerRepository(restored_engine, FilesystemObjectStore(target))
        assert restored.read_source(owner, draft.source.object_id) == b"scanned"
        assert restored.get_draft(owner, draft.id).current_revision_id == revision.id
        assert (
            restored.read_artifact(owner, draft.id, revision.id, "preview")
            == b"preview"
        )
    finally:
        restored_engine.dispose()

    missing = tuple(
        member
        for member in members
        if not member.path.endswith(str(revision.artifacts[0].id))
    )
    with pytest.raises(RecoveryError, match="references"):
        adapter.restore(
            staging,
            (tmp_path / "incomplete").resolve(),
            missing,
            RecoveryDeadline.after(10),
        )


def test_retention_hides_draft_before_removing_artifacts_and_orphan_source(
    tmp_path: Path,
) -> None:
    engine, objects, repository, owner, _other, _admin = _repo(tmp_path)
    try:
        draft = repository.create_draft_with_source(
            owner,
            b"scanned",
            "scan-ok",
            title="Draft",
            content="",
            media_type="text/markdown",
        )
        revision = repository.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="retention",
            snapshot=RevisionSnapshot(
                draft.source, None, "{}", "{}", None, "human", "generate"
            ),
            artifacts=(
                ArtifactContent("download", "text/plain", b"download"),
                ArtifactContent("preview", "text/html", b"preview"),
            ),
        )
        assert (
            repository.cleanup_expired_drafts(
                cutoff_at=datetime.now(UTC) - timedelta(hours=1), limit=10
            )
            == 0
        )
        assert (
            repository.cleanup_expired_drafts(
                cutoff_at=datetime.now(UTC) + timedelta(hours=1), limit=10
            )
            == 1
        )
        with pytest.raises(ComposerNotFoundError):
            repository.get_draft(owner, draft.id)
        for artifact in revision.artifacts:
            assert not objects.exists(
                ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner, artifact.id)
            )
        assert repository.cleanup_orphan_sources(limit=10) == 1
        assert not objects.exists(
            ObjectKey(ObjectScope.COMPOSER_SOURCE, owner, draft.source.object_id)
        )
    finally:
        engine.dispose()
