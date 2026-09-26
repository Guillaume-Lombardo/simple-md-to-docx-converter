"""Revision publication and artifact failures leave the draft pointer unchanged."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from markweave.composer.revisions import (
    ArtifactContent,
    ComposerArtifactError,
    ComposerConflictError,
    ComposerNotFoundError,
    RevisionSnapshot,
)
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.schema import ComposerRevisionRow
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectStoreError,
)
from tests.integration.sqlite.test_composer_model_steps import _draft, _setup

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _prepared(tmp_path: Path):
    engine, drafts, _steps, owner, _other, _connection_id = _setup(tmp_path)
    draft = _draft(drafts, owner)
    objects = FilesystemObjectStore(tmp_path)
    publisher = SqlComposerRepository(engine, objects)
    snapshot = RevisionSnapshot(
        draft.source, None, "{}", "{}", None, "human", "generate"
    )
    artifacts = (
        ArtifactContent("download", "text/plain", b"exact-download"),
        ArtifactContent("preview", "text/html", b"<p>exact-preview</p>"),
    )
    return engine, objects, publisher, owner, draft, snapshot, artifacts


def _publish(  # noqa: PLR0913 - explicit publication failure inputs
    publisher, owner, draft, snapshot, artifacts, *, key="revision"
):
    return publisher.publish_revision(
        owner,
        draft.id,
        actor_id=owner,
        if_match=draft.etag,
        idempotency_key=key,
        snapshot=snapshot,
        artifacts=artifacts,
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ("key", ValueError),
        ("missing-artifact", ValueError),
        ("duplicate-artifact", ValueError),
        ("wrong-owner", ComposerNotFoundError),
        ("changed-source", ComposerConflictError),
    ],
)
def test_revision_rejects_invalid_publication_inputs_before_reservation(
    tmp_path: Path, change: str, error: type[Exception]
) -> None:
    engine, _objects, publisher, owner, draft, snapshot, artifacts = _prepared(tmp_path)
    try:
        key = "revision"
        if change == "key":
            key = ""
        elif change == "missing-artifact":
            artifacts = artifacts[:1]
        elif change == "duplicate-artifact":
            artifacts = (*artifacts, artifacts[0])
        elif change == "wrong-owner":
            snapshot = replace(snapshot, source=replace(draft.source, owner_id=uuid4()))
        else:
            snapshot = replace(snapshot, source=replace(draft.source, sha256="0" * 64))
        with pytest.raises(error):
            _publish(publisher, owner, draft, snapshot, artifacts, key=key)
        with Session(engine) as database:
            assert database.scalar(select(ComposerRevisionRow)) is None
    finally:
        engine.dispose()


def test_revision_artifact_read_checks_kind_presence_and_exact_bytes(
    tmp_path: Path,
) -> None:
    engine, objects, publisher, owner, draft, snapshot, artifacts = _prepared(tmp_path)
    try:
        revision = _publish(publisher, owner, draft, snapshot, artifacts)
        with pytest.raises(ComposerNotFoundError):
            publisher.read_artifact(owner, draft.id, revision.id, "missing")
        download = next(item for item in revision.artifacts if item.kind == "download")
        key = ObjectKey(ObjectScope.COMPOSER_ARTIFACT, owner, download.id)
        objects.delete(key)
        with pytest.raises(ComposerArtifactError, match="unavailable"):
            publisher.read_artifact(owner, draft.id, revision.id, "download")
        objects.put(key, b"wrong-content")
        with pytest.raises(ComposerArtifactError, match="integrity"):
            publisher.read_artifact(owner, draft.id, revision.id, "download")
    finally:
        engine.dispose()


def test_failed_artifact_write_leaves_hidden_reservation_and_blocks_key_reuse(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, publisher, owner, draft, snapshot, artifacts = _prepared(tmp_path)
    try:
        mocker.patch.object(objects, "put", side_effect=ObjectStoreError("offline"))
        with pytest.raises(ComposerArtifactError, match="publication failed"):
            _publish(publisher, owner, draft, snapshot, artifacts)
        with pytest.raises(ComposerConflictError, match="in progress"):
            _publish(publisher, owner, draft, snapshot, artifacts)
        assert publisher.get_draft(owner, draft.id).current_revision_id is None
        with Session(engine) as database:
            pending = database.scalar(select(ComposerRevisionRow))
            assert pending is not None and pending.publication_state == "pending"
    finally:
        engine.dispose()


@pytest.mark.parametrize("race", ["readback", "draft-version"])
def test_revision_rechecks_storage_and_draft_after_artifact_write(
    tmp_path: Path, mocker: MockerFixture, race: str
) -> None:
    engine, objects, publisher, owner, draft, snapshot, artifacts = _prepared(tmp_path)
    try:
        if race == "readback":
            mocker.patch.object(objects, "get", return_value=b"wrong-readback")
            expected = ComposerArtifactError
        else:
            actual_put = objects.put
            changed = []

            def put_after_edit(key: ObjectKey, content: bytes) -> None:
                actual_put(key, content)
                if not changed:
                    current = publisher.get_draft(owner, draft.id)
                    publisher.save_draft(
                        owner,
                        draft.id,
                        if_match=current.etag,
                        title=current.title,
                        content="human edit during publication",
                    )
                    changed.append(True)

            mocker.patch.object(objects, "put", side_effect=put_after_edit)
            expected = ComposerConflictError
        with pytest.raises(expected):
            _publish(publisher, owner, draft, snapshot, artifacts)
        assert publisher.get_draft(owner, draft.id).current_revision_id is None
        with Session(engine) as database:
            pending = database.scalar(select(ComposerRevisionRow))
            assert pending is not None and pending.publication_state == "pending"
    finally:
        engine.dispose()
