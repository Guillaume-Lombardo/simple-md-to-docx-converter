"""Durable fill-plan review, authorization, and publication on real SQL storage."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import AuthorKnowledgeLimits, AuthorRecord
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    ComposerNotFoundError,
    RevisionSnapshot,
)
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.fill_plans import FillPlan, SqlFillPlanRepository
from markweave.persistence.composer.repository import SqlComposerRepository
from markweave.persistence.composer.typed_templates import (
    SqlTypedTemplateRepository,
    TypedTemplate,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerContentAuditRow,
    ComposerDraftRow,
    ComposerRevisionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectStore

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(slots=True)
class Prepared:
    engine: Engine
    objects: ObjectStore
    composer: SqlComposerRepository
    authors: SqlAuthorKnowledgeRepository
    templates: SqlTypedTemplateRepository
    plans: SqlFillPlanRepository
    owner: UUID
    sharer: UUID
    stranger: UUID
    draft_id: UUID
    source_revision_id: UUID
    author: AuthorRecord
    template: TypedTemplate


def prepare(engine: Engine, objects: ObjectStore) -> Prepared:
    """Create an approved source and explicitly shared author/template records."""

    upgrade_database(engine)
    owner, sharer, stranger = uuid4(), uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id in (owner, sharer, stranger):
            database.add(
                UserRow(
                    id=str(user_id),
                    username=str(user_id),
                    normalized_username=str(user_id),
                    password_hash="fixture",  # noqa: S106 - isolated fixture
                    role="user",
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
    authors = SqlAuthorKnowledgeRepository(
        engine,
        AuthorKnowledgeLimits(
            max_fields=10,
            max_name_length=100,
            max_field_value_length=100,
            max_field_name_length=100,
            max_citation_length=100,
        ),
    )
    author = authors.create(
        sharer,
        "Ada",
        json.dumps(
            {"affiliation": {"value": "PRIVATE-AFFILIATION", "provenance": "supplied"}}
        ),
    )
    author = authors.grant(sharer, author.id, owner, if_match=author.etag)
    templates = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5)
    )
    template = templates.create(
        sharer,
        "Letter",
        '{"fields":[],"repeats":[]}',
        b"private-template",
        schema_version=1,
    )
    template = templates.grant(sharer, template.id, owner, if_match=template.etag)
    composer = SqlComposerRepository(engine, objects)
    draft = composer.create_draft_with_source(
        owner,
        b"# source\n",
        "scan-receipt",
        title="Draft",
        content="# source\n",
        media_type="text/markdown",
    )
    source_revision = composer.publish_revision(
        owner,
        draft.id,
        actor_id=owner,
        if_match=draft.etag,
        idempotency_key="source-revision",
        snapshot=RevisionSnapshot(
            draft.source, None, "{}", "{}", None, "human:approved", "generate"
        ),
        artifacts=(
            ArtifactContent("download", "text/plain", b"approved source"),
            ArtifactContent("preview", "text/html", b"<p>approved source</p>"),
        ),
    )
    return Prepared(
        engine,
        objects,
        composer,
        authors,
        templates,
        SqlFillPlanRepository(engine),
        owner,
        sharer,
        stranger,
        draft.id,
        source_revision.id,
        author,
        template,
    )


def _sqlite(tmp_path: Path) -> Prepared:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    return prepare(engine, FilesystemObjectStore(tmp_path))


def create_plan(
    context: Prepared, *, key: str = "fill-create", if_match: str | None = None
) -> FillPlan:
    draft = context.composer.get_draft(context.owner, context.draft_id)
    return context.plans.create(
        context.owner,
        context.draft_id,
        source_revision_id=context.source_revision_id,
        template_version_id=context.template.active_version_id,
        author_refs=((context.author.id, context.author.version),),
        values={"finding": "PRIVATE-FINDING"},
        provenance={"finding": "human_edited"},
        questions=({"path": "decision", "question": "Confirm decision"},),
        if_match=if_match or draft.etag,
        idempotency_key=key,
    )


def test_create_decide_cas_idempotency_and_content_free_audit(tmp_path: Path) -> None:
    context = _sqlite(tmp_path)
    draft_before = context.composer.get_draft(context.owner, context.draft_id)
    plan = create_plan(context)
    assert plan.state == "pending" and plan.version == 1
    assert plan.draft_version == draft_before.version + 1
    assert context.plans.list_visible(context.owner, context.draft_id) == (plan,)
    assert create_plan(context, if_match=draft_before.etag) == plan
    with pytest.raises(ComposerConflictError, match="key was reused"):
        context.plans.create(
            context.owner,
            context.draft_id,
            source_revision_id=context.source_revision_id,
            template_version_id=context.template.active_version_id,
            author_refs=((context.author.id, context.author.version),),
            values={"finding": "changed"},
            provenance={},
            questions=(),
            if_match=draft_before.etag,
            idempotency_key="fill-create",
        )
    with pytest.raises(ComposerConflictError, match="draft changed"):
        create_plan(context, key="different-key", if_match=draft_before.etag)
    with pytest.raises(ComposerNotFoundError):
        context.plans.get(context.stranger, context.draft_id, plan.id)
    edited = context.plans.decide(
        context.owner,
        context.draft_id,
        plan.id,
        if_match=plan.etag,
        idempotency_key="decision-edit",
        values={"finding": "PRIVATE-HUMAN-EDIT"},
        provenance={"finding": "human_edited"},
        questions=({"path": "decision", "question": "Confirm decision"},),
        approve=False,
    )
    assert edited.version == 2 and edited.state == "pending"
    assert (
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="decision-edit",
            values=edited.values,
            provenance=edited.provenance,
            questions=edited.questions,
            approve=False,
        )
        == edited
    )
    with pytest.raises(ComposerConflictError, match="key was reused"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="decision-edit",
            values={"finding": "different"},
            provenance=edited.provenance,
            questions=edited.questions,
            approve=False,
        )
    with pytest.raises(ComposerConflictError, match="Fill plan changed"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="stale",
            values=edited.values,
            provenance=edited.provenance,
            questions=(),
            approve=True,
        )
    with pytest.raises(ComposerConflictError, match="unanswered"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=edited.etag,
            idempotency_key="premature",
            values=edited.values,
            provenance=edited.provenance,
            questions=edited.questions,
            approve=True,
        )
    approved = context.plans.decide(
        context.owner,
        context.draft_id,
        plan.id,
        if_match=edited.etag,
        idempotency_key="decision-approve",
        values=edited.values,
        provenance=edited.provenance,
        questions=(),
        approve=True,
    )
    assert approved.state == "approved" and approved.version == 3
    assert (
        context.composer.get_draft(context.owner, context.draft_id).version
        == plan.draft_version
    )
    with Session(context.engine) as database:
        events = database.scalars(
            select(ComposerContentAuditRow)
            .where(ComposerContentAuditRow.target_kind == "composer_fill_plan")
            .order_by(ComposerContentAuditRow.created_at)
        ).all()
        assert len(events) == 3
        assert {event.operation for event in events} == {
            "fill_plan_create",
            "fill_plan_edit",
            "fill_plan_approve",
        }
        assert all("PRIVATE-" not in str(event.__dict__) for event in events)


@pytest.mark.parametrize("revoked", ["author", "template"])
def test_revoked_reference_blocks_existing_plan(tmp_path: Path, revoked: str) -> None:
    context = _sqlite(tmp_path)
    plan = create_plan(context)
    if revoked == "author":
        context.authors.revoke(
            context.sharer,
            context.author.id,
            context.owner,
            if_match=context.author.etag,
        )
    else:
        context.templates.revoke(
            context.sharer,
            context.template.id,
            context.owner,
            if_match=context.template.etag,
        )
    with pytest.raises(ComposerConflictError, match="access changed"):
        context.plans.get(context.owner, context.draft_id, plan.id)
    assert context.plans.list_visible(context.owner, context.draft_id) == ()
    with pytest.raises(ComposerConflictError, match="access changed"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="after-revoke",
            values=plan.values,
            provenance=plan.provenance,
            questions=(),
            approve=True,
        )


def test_plan_pages_count_only_authorized_rows(tmp_path: Path) -> None:
    context = _sqlite(tmp_path)
    older = []
    for number in range(2):
        draft = context.composer.get_draft(context.owner, context.draft_id)
        older.append(
            context.plans.create(
                context.owner,
                context.draft_id,
                source_revision_id=context.source_revision_id,
                template_version_id=context.template.active_version_id,
                author_refs=(),
                values={"finding": "independent"},
                provenance={"finding": "human_edited"},
                questions=(),
                if_match=draft.etag,
                idempotency_key=f"independent-plan-{number}",
            )
        )
    for number in range(101):
        newer = create_plan(context, key=f"shared-plan-{number}")
    context.authors.revoke(
        context.sharer,
        context.author.id,
        context.owner,
        if_match=context.author.etag,
    )
    assert context.plans.list_visible(context.owner, context.draft_id, limit=1) == (
        older[1],
    )
    assert context.plans.list_visible(
        context.owner, context.draft_id, limit=1, offset=1
    ) == (older[0],)
    assert (
        context.plans.list_visible(context.owner, context.draft_id, limit=1, offset=2)
        == ()
    )
    with pytest.raises(ComposerConflictError, match="access changed"):
        context.plans.get(context.owner, context.draft_id, newer.id)


def test_inactive_user_and_changed_draft_block_plan_decision(tmp_path: Path) -> None:
    context = _sqlite(tmp_path)
    plan = create_plan(context)
    with Session(context.engine) as database, database.begin():
        user = database.get(UserRow, str(context.owner))
        assert user is not None
        user.active = False
    with pytest.raises(ComposerConflictError, match="access changed"):
        context.plans.get(context.owner, context.draft_id, plan.id)
    with pytest.raises(ComposerConflictError, match="access changed"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="inactive-user",
            values=plan.values,
            provenance=plan.provenance,
            questions=(),
            approve=True,
        )
    with Session(context.engine) as database, database.begin():
        user = database.get(UserRow, str(context.owner))
        assert user is not None
        user.active = True
        draft = database.get(ComposerDraftRow, str(context.draft_id))
        assert draft is not None
        draft.version += 1
    with pytest.raises(ComposerConflictError, match="draft changed"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="stale-draft",
            values=plan.values,
            provenance=plan.provenance,
            questions=(),
            approve=True,
        )


def test_concurrent_fill_decisions_keep_one_version(tmp_path: Path) -> None:
    context = _sqlite(tmp_path)
    plan = create_plan(context)

    def decide(key: str) -> str:
        try:
            result = context.plans.decide(
                context.owner,
                context.draft_id,
                plan.id,
                if_match=plan.etag,
                idempotency_key=key,
                values={"finding": key},
                provenance={"finding": "human_edited"},
                questions=(),
                approve=False,
            )
            return f"version-{result.version}"
        except ComposerConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(decide, ("decision-a", "decision-b")))
    assert sorted(results) == ["conflict", "version-2"]
    assert context.plans.get(context.owner, context.draft_id, plan.id).version == 2


def publish_filled_revision(context: Prepared, plan: FillPlan) -> UUID:
    """Recreate the exact frozen revision published before a process crash."""

    source = context.composer.get_revision(
        context.owner, context.draft_id, context.source_revision_id
    )
    content = b"filled-docx"
    frozen = {
        "source_revision_id": str(plan.source_revision_id),
        "source_sha256": source.snapshot.source.sha256,
        "template_id": str(plan.template_id),
        "template_version_id": str(plan.template_version_id),
        "template_docx_sha256": plan.template_docx_sha256,
        "template_schema_sha256": plan.template_schema_sha256,
        "approved_values": plan.values,
        "provenance": plan.provenance,
        "author_refs": [
            {"id": str(id_), "version": version} for id_, version in plan.author_refs
        ],
        "result_sha256": hashlib.sha256(content).hexdigest(),
        "fill_plan_id": str(plan.id),
        "fill_plan_version": plan.version,
    }
    revision = context.composer.publish_revision(
        context.owner,
        context.draft_id,
        actor_id=context.owner,
        if_match=f'"{plan.draft_version}"',
        idempotency_key="filled-revision",
        snapshot=RevisionSnapshot(
            source.snapshot.source,
            None,
            "{}",
            "{}",
            None,
            "human:typed_fill",
            "fill_template",
            typed_fill_snapshot=json.dumps(
                frozen, sort_keys=True, separators=(",", ":")
            ),
        ),
        artifacts=(
            ArtifactContent("download", _DOCX_MEDIA, content),
            ArtifactContent("preview", _DOCX_MEDIA, content),
        ),
    )
    return revision.id


@pytest.mark.parametrize("revoke_during_publication", [False, True])
def test_regeneration_rechecks_frozen_author_grant(
    tmp_path: Path, mocker: MockerFixture, revoke_during_publication: bool
) -> None:
    assert_regeneration_rechecks_frozen_author_grant(
        _sqlite(tmp_path), mocker, revoke_during_publication
    )


def assert_regeneration_rechecks_frozen_author_grant(
    context: Prepared, mocker: MockerFixture, revoke_during_publication: bool
) -> None:
    pending = create_plan(context)
    plan = context.plans.decide(
        context.owner,
        context.draft_id,
        pending.id,
        if_match=pending.etag,
        idempotency_key="approve",
        values=pending.values,
        provenance=pending.provenance,
        questions=(),
        approve=True,
    )
    parent_id = publish_filled_revision(context, plan)
    parent = context.composer.get_revision(context.owner, context.draft_id, parent_id)
    assert parent.snapshot.typed_fill_snapshot is not None
    frozen = json.loads(parent.snapshot.typed_fill_snapshot)
    frozen["parent_revision_id"] = str(parent_id)
    frozen["actor_id"] = str(context.owner)

    def revoke() -> None:
        context.authors.revoke(
            context.sharer,
            context.author.id,
            context.owner,
            if_match=context.author.etag,
        )

    if revoke_during_publication:
        original_put = context.objects.put
        revoked = False

        def revoke_on_put(key: ObjectKey, content: bytes) -> None:
            nonlocal revoked
            if not revoked:
                revoke()
                revoked = True
            return original_put(key, content)

        mocker.patch.object(context.objects, "put", side_effect=revoke_on_put)
    else:
        revoke()
    draft = context.composer.get_draft(context.owner, context.draft_id)
    with pytest.raises(ComposerConflictError, match="Author access changed"):
        context.composer.publish_revision(
            context.owner,
            context.draft_id,
            actor_id=context.owner,
            if_match=draft.etag,
            idempotency_key="regenerate-after-revoke",
            snapshot=RevisionSnapshot(
                parent.snapshot.source,
                None,
                "{}",
                parent.snapshot.render_options,
                None,
                "human:frozen_regeneration",
                "regenerate_fill",
                typed_fill_snapshot=json.dumps(frozen),
            ),
            artifacts=(
                ArtifactContent("download", _DOCX_MEDIA, b"filled-docx"),
                ArtifactContent("preview", _DOCX_MEDIA, b"filled-docx"),
            ),
        )
    assert context.composer.get_revision(context.owner, context.draft_id, parent_id)
    assert (
        context.composer.get_draft(context.owner, context.draft_id).current_revision_id
        == parent_id
    )
    with Session(context.engine) as database:
        assert database.scalars(
            select(ComposerRevisionRow).where(
                ComposerRevisionRow.draft_id == str(context.draft_id),
                ComposerRevisionRow.publication_state == "published",
            )
        ).all() == [
            database.get(ComposerRevisionRow, str(context.source_revision_id)),
            database.get(ComposerRevisionRow, str(parent_id)),
        ]


def test_regeneration_keeps_frozen_values_after_author_update(tmp_path: Path) -> None:
    assert_regeneration_keeps_frozen_values_after_author_update(_sqlite(tmp_path))


def assert_regeneration_keeps_frozen_values_after_author_update(
    context: Prepared,
) -> None:
    pending = create_plan(context)
    plan = context.plans.decide(
        context.owner,
        context.draft_id,
        pending.id,
        if_match=pending.etag,
        idempotency_key="approve",
        values=pending.values,
        provenance=pending.provenance,
        questions=(),
        approve=True,
    )
    parent_id = publish_filled_revision(context, plan)
    parent = context.composer.get_revision(context.owner, context.draft_id, parent_id)
    assert parent.snapshot.typed_fill_snapshot is not None
    frozen = json.loads(parent.snapshot.typed_fill_snapshot)
    changed = context.authors.update(
        context.sharer,
        context.author.id,
        if_match=context.author.etag,
        name="Ada, updated",
        fields_json=json.dumps(
            {"affiliation": {"value": "New affiliation", "provenance": "supplied"}}
        ),
    )
    assert changed.version != frozen["author_refs"][0]["version"]
    assert context.authors.get(context.owner, context.author.id).id == changed.id
    frozen["parent_revision_id"] = str(parent_id)
    frozen["actor_id"] = str(context.owner)
    draft = context.composer.get_draft(context.owner, context.draft_id)
    regenerated = context.composer.publish_revision(
        context.owner,
        context.draft_id,
        actor_id=context.owner,
        if_match=draft.etag,
        idempotency_key="regenerate-after-author-update",
        snapshot=RevisionSnapshot(
            parent.snapshot.source,
            None,
            "{}",
            parent.snapshot.render_options,
            None,
            "human:frozen_regeneration",
            "regenerate_fill",
            typed_fill_snapshot=json.dumps(frozen),
        ),
        artifacts=(
            ArtifactContent("download", _DOCX_MEDIA, b"filled-docx"),
            ArtifactContent("preview", _DOCX_MEDIA, b"filled-docx"),
        ),
    )
    assert regenerated.snapshot.typed_fill_snapshot is not None
    regenerated_values = json.loads(regenerated.snapshot.typed_fill_snapshot)
    assert regenerated_values["approved_values"] == frozen["approved_values"]
    assert regenerated_values["author_refs"] == frozen["author_refs"]
    assert (
        context.composer.read_artifact(
            context.owner, context.draft_id, regenerated.id, "download"
        )
        == b"filled-docx"
    )


def test_mark_published_rejects_unrelated_revision_and_recovers_exact_link(
    tmp_path: Path,
) -> None:
    context = _sqlite(tmp_path)
    pending = create_plan(context)
    plan = context.plans.decide(
        context.owner,
        context.draft_id,
        pending.id,
        if_match=pending.etag,
        idempotency_key="approve",
        values=pending.values,
        provenance=pending.provenance,
        questions=(),
        approve=True,
    )
    with pytest.raises(ComposerConflictError):
        context.plans.mark_published(
            context.owner, context.draft_id, plan.id, context.source_revision_id
        )
    filled_revision_id = publish_filled_revision(context, plan)
    assert (
        context.plans.get(context.owner, context.draft_id, plan.id).state == "approved"
    )
    restarted = SqlFillPlanRepository(context.engine)
    linked = restarted.mark_published(
        context.owner, context.draft_id, plan.id, filled_revision_id
    )
    assert (
        linked.state == "published" and linked.result_revision_id == filled_revision_id
    )
    assert (
        restarted.mark_published(
            context.owner, context.draft_id, plan.id, filled_revision_id
        )
        == linked
    )
    with Session(context.engine) as database:
        events = database.scalars(
            select(ComposerContentAuditRow).where(
                ComposerContentAuditRow.target_kind == "composer_fill_plan",
                ComposerContentAuditRow.operation == "fill_plan_publish",
            )
        ).all()
        assert len(events) == 1
        assert "PRIVATE-" not in str(events[0].__dict__)
    with pytest.raises(ComposerConflictError):
        restarted.mark_published(
            context.owner, context.draft_id, plan.id, context.source_revision_id
        )


def test_fill_plan_rejects_invalid_keys_and_unknown_identity(tmp_path: Path) -> None:
    context = _sqlite(tmp_path)
    draft = context.composer.get_draft(context.owner, context.draft_id)
    with pytest.raises(ValueError, match="idempotency key"):
        create_plan(context, key="invalid key", if_match=draft.etag)
    plan = create_plan(context)
    with pytest.raises(ComposerNotFoundError):
        context.plans.get(context.owner, context.draft_id, uuid4())
    with pytest.raises(ValueError, match="idempotency key"):
        context.plans.decide(
            context.owner,
            context.draft_id,
            plan.id,
            if_match=plan.etag,
            idempotency_key="invalid key",
            values=plan.values,
            provenance=plan.provenance,
            questions=plan.questions,
            approve=False,
        )
    with pytest.raises(ComposerNotFoundError):
        context.plans.mark_published(
            context.owner, context.draft_id, uuid4(), context.source_revision_id
        )
    assert context.plans.get(context.owner, context.draft_id, plan.id) == plan


def test_fill_publication_rechecks_frozen_snapshot_and_artifact_receipts(
    tmp_path: Path,
) -> None:
    context = _sqlite(tmp_path)
    pending = create_plan(context)
    approved = context.plans.decide(
        context.owner,
        context.draft_id,
        pending.id,
        if_match=pending.etag,
        idempotency_key="approve",
        values=pending.values,
        provenance=pending.provenance,
        questions=(),
        approve=True,
    )
    revision_id = publish_filled_revision(context, approved)
    with Session(context.engine) as database, database.begin():
        revision = database.get(ComposerRevisionRow, str(revision_id))
        assert revision is not None and revision.typed_fill_snapshot is not None
        original = revision.typed_fill_snapshot
        revision.typed_fill_snapshot = "invalid-json"
    with pytest.raises(ComposerConflictError, match="invalid"):
        context.plans.mark_published(
            context.owner, context.draft_id, approved.id, revision_id
        )
    with Session(context.engine) as database, database.begin():
        revision = database.get(ComposerRevisionRow, str(revision_id))
        assert revision is not None
        frozen = json.loads(original)
        frozen["fill_plan_id"] = str(uuid4())
        revision.typed_fill_snapshot = json.dumps(frozen)
    with pytest.raises(ComposerConflictError, match="does not match plan"):
        context.plans.mark_published(
            context.owner, context.draft_id, approved.id, revision_id
        )
    with Session(context.engine) as database, database.begin():
        revision = database.get(ComposerRevisionRow, str(revision_id))
        assert revision is not None
        frozen = json.loads(original)
        frozen["result_sha256"] = "0" * 64
        revision.typed_fill_snapshot = json.dumps(frozen)
    with pytest.raises(ComposerConflictError, match="artifacts"):
        context.plans.mark_published(
            context.owner, context.draft_id, approved.id, revision_id
        )
    with Session(context.engine) as database, database.begin():
        revision = database.get(ComposerRevisionRow, str(revision_id))
        assert revision is not None
        revision.typed_fill_snapshot = original
    linked = context.plans.mark_published(
        context.owner, context.draft_id, approved.id, revision_id
    )
    assert linked.state == "published"
