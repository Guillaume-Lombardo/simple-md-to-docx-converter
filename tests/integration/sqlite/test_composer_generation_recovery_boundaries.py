"""Durable Composer generation links reject stale approval and crossed identities."""

from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerNotFoundError,
    RevisionSnapshot,
)
from tests.integration.sqlite.test_composer_foundations import _repo

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_APPROVED = b"# Human-approved Markdown\n"


def _reserve(
    store, owner: UUID, draft_id: UUID, revision_id: UUID, etag: str, **changes
):
    arguments = {
        "if_match": etag,
        "idempotency_key": "generation-1",
        "request_digest": "a" * 64,
        "approved_markdown_sha256": sha256(_APPROVED).hexdigest(),
        "input_sha256": sha256(_APPROVED).hexdigest(),
        "output": "docx",
        "template_id": None,
        "template_version_id": None,
        "presentation_options": None,
        "component_versions": '[["md-converter","0.7"]]',
    }
    arguments.update(changes)
    return store.reserve_generation(owner, draft_id, revision_id, **arguments)


def test_generation_rejects_unapproved_or_stale_source_and_unknown_owner(
    tmp_path: Path,
) -> None:
    engine, _objects, store, owner, other, _admin = _repo(tmp_path)
    try:
        draft = store.create_draft_with_source(
            owner,
            _APPROVED,
            "scanner-approved",
            title="Draft",
            content=_APPROVED.decode(),
            media_type="text/markdown",
        )
        captured = store.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="capture-source",
            snapshot=RevisionSnapshot(
                draft.source, None, "{}", "{}", None, "source", "capture_source"
            ),
            artifacts=(
                ArtifactContent("download", "text/markdown", _APPROVED),
                ArtifactContent("preview", "text/markdown", _APPROVED),
            ),
        )
        current = store.get_draft(owner, draft.id)
        with pytest.raises(ComposerConflictError, match="approved Markdown"):
            _reserve(store, owner, draft.id, captured.id, current.etag)
        with pytest.raises(ComposerConflictError, match="draft changed"):
            _reserve(store, owner, draft.id, captured.id, draft.etag)
        with pytest.raises(ValueError, match="idempotency"):
            _reserve(
                store,
                owner,
                draft.id,
                captured.id,
                current.etag,
                idempotency_key="",
            )
        with pytest.raises(ComposerNotFoundError):
            _reserve(store, other, draft.id, captured.id, current.etag)
    finally:
        engine.dispose()


def test_generation_link_replay_preserves_job_and_publication_receipts(
    tmp_path: Path,
) -> None:
    engine, _objects, store, owner, other, _admin = _repo(tmp_path)
    try:
        draft = store.create_draft_with_source(
            owner,
            _APPROVED,
            "scanner-approved",
            title="Draft",
            content=_APPROVED.decode(),
            media_type="text/markdown",
        )
        revision = store.publish_revision(
            owner,
            draft.id,
            actor_id=owner,
            if_match=draft.etag,
            idempotency_key="direct-approval",
            snapshot=RevisionSnapshot(
                draft.source,
                None,
                '{"content": "# Human-approved Markdown\\n"}',
                "{}",
                None,
                "human:direct",
                "publish_draft",
            ),
            artifacts=(
                ArtifactContent("download", "text/markdown", _APPROVED),
                ArtifactContent("preview", "text/markdown", _APPROVED),
            ),
        )
        etag = store.get_draft(owner, draft.id).etag
        generation = _reserve(store, owner, draft.id, revision.id, etag)
        assert _reserve(store, owner, draft.id, revision.id, etag) == generation
        with pytest.raises(ComposerConflictError, match="idempotency"):
            _reserve(
                store,
                owner,
                draft.id,
                revision.id,
                etag,
                request_digest="b" * 64,
            )
        first_job, second_job = UUID(int=1), UUID(int=2)
        attached = store.attach_generation_job(
            owner, draft.id, generation.id, first_job
        )
        assert attached.job_id == first_job
        assert (
            store.attach_generation_job(owner, draft.id, generation.id, first_job)
            == attached
        )
        with pytest.raises(ComposerConflictError, match="job changed"):
            store.attach_generation_job(owner, draft.id, generation.id, second_job)
        with pytest.raises(ComposerNotFoundError):
            store.attach_generation_job(owner, draft.id, UUID(int=9), first_job)
        result_id = UUID(int=3)
        published = store.attach_generation_revision(
            owner, draft.id, generation.id, result_id, "publication-1"
        )
        assert published.result_revision_id == result_id
        assert (
            store.attach_generation_revision(
                owner, draft.id, generation.id, result_id, "publication-1"
            )
            == published
        )
        with pytest.raises(ComposerConflictError, match="revision changed"):
            store.attach_generation_revision(
                owner, draft.id, generation.id, UUID(int=4), "publication-1"
            )
        with pytest.raises(ComposerConflictError, match="publication key changed"):
            store.attach_generation_revision(
                owner, draft.id, generation.id, result_id, "publication-2"
            )
        with pytest.raises(ComposerNotFoundError):
            store.attach_generation_revision(
                owner, draft.id, UUID(int=9), result_id, "publication-1"
            )
        with pytest.raises(ComposerNotFoundError):
            store.get_generation(other, draft.id, generation.id)
        with pytest.raises(ComposerNotFoundError):
            store.get_generation(owner, draft.id, UUID(int=9))
        assert store.get_generation(owner, draft.id, generation.id) == published
    finally:
        engine.dispose()
