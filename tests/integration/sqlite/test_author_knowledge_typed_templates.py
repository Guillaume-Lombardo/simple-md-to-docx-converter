"""Real SQLite/filesystem permission and immutable-object publication fences."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import (
    AuthorKnowledgeConflictError,
    AuthorKnowledgeLimits,
    AuthorKnowledgeNotFoundError,
)
from markweave.persistence.composer.audit import SqlComposerAuditRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.typed_templates import (
    SqlTypedTemplateRepository,
    TypedTemplateArtifactError,
    TypedTemplateConflictError,
    TypedTemplateNotFoundError,
)
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import SqlAuditReader
from markweave.persistence.schema import (
    AuthorKnowledgeAuditRow,
    ComposerContentAuditRow,
    ComposerFillPlanRow,
    RetentionCleanupRunRow,
    TypedTemplateAuditRow,
    TypedTemplateRow,
    TypedTemplateVersionRow,
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


def _setup(tmp_path: Path):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner, reader, stranger, admin = (uuid4() for _ in range(4))
    with Session(engine) as database, database.begin():
        for user_id, role in (
            (owner, "user"),
            (reader, "user"),
            (stranger, "user"),
            (admin, "admin"),
        ):
            database.add(
                UserRow(
                    id=str(user_id),
                    username=str(user_id),
                    normalized_username=str(user_id),
                    password_hash="fixture",  # noqa: S106 - isolated fixture
                    role=role,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
    objects = FilesystemObjectStore(tmp_path)
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
    typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5)
    )
    return engine, objects, authors, typed, owner, reader, stranger, admin


def test_author_grant_revoke_and_transactional_revision_fence(tmp_path: Path) -> None:
    engine, _, authors, _, owner, reader, stranger, admin = _setup(tmp_path)
    fields = json.dumps(
        {"affiliation": {"value": "University", "provenance": "supplied"}}
    )
    author = authors.create(owner, "Ada", fields)
    assert author.version == 1
    assert authors.list_visible(reader) == ()
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.get(reader, author.id)
    shared = authors.grant(owner, author.id, reader, if_match=author.etag)
    assert shared.version == 2
    assert authors.get(reader, author.id).fields["affiliation"].value == "University"
    assert [item.id for item in authors.list_visible(reader, query="Ad")] == [author.id]
    assert authors.list_visible(reader, query="Bob") == ()
    with Session(engine) as database:
        assert (
            authors.require_access(
                database, reader, author.id, expected_version=2
            ).version
            == 2
        )
    with pytest.raises(AuthorKnowledgeConflictError):
        authors.update(
            owner, author.id, if_match=author.etag, name="Ada", fields_json=fields
        )
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.grant(stranger, author.id, admin, if_match=shared.etag)
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.revoke(admin, author.id, reader, if_match=shared.etag, is_admin=True)
    revoked = authors.revoke(owner, author.id, reader, if_match=shared.etag)
    assert revoked.version == 3
    with Session(engine) as database:
        with pytest.raises(AuthorKnowledgeNotFoundError):
            authors.require_access(database, reader, author.id, expected_version=2)
        audits = database.scalars(
            select(AuthorKnowledgeAuditRow).order_by(AuthorKnowledgeAuditRow.version)
        ).all()
        assert [item.operation for item in audits] == ["create", "grant", "revoke"]
        assert audits[-1].administrator_intervention is False
        assert all("University" not in str(item.__dict__) for item in audits)


def test_author_provenance_validation_rejects_uncertain_fact(tmp_path: Path) -> None:
    _, _, authors, _, owner, *_ = _setup(tmp_path)
    with pytest.raises(ValueError, match="Unresolved"):
        authors.create(
            owner,
            "Ada",
            json.dumps({"affiliation": {"value": "Maybe", "provenance": "unresolved"}}),
        )
    with pytest.raises(ValueError, match="source reference"):
        authors.create(
            owner,
            "Ada",
            json.dumps({"affiliation": {"value": "University", "provenance": "cited"}}),
        )


def test_model_suggestion_cannot_replace_human_author_value(tmp_path: Path) -> None:
    _, _, authors, _, owner, *_ = _setup(tmp_path)
    approved = json.dumps(
        {"affiliation": {"value": "University", "provenance": "human_edited"}}
    )
    author = authors.create(owner, "Ada", approved)
    suggestion = json.dumps(
        {"affiliation": {"value": "Unknown", "provenance": "model_suggested"}}
    )
    with pytest.raises(AuthorKnowledgeConflictError, match="review"):
        authors.update(
            owner, author.id, if_match=author.etag, name="Ada", fields_json=suggestion
        )
    assert authors.get(owner, author.id).fields["affiliation"].value == "University"


def test_deactivated_user_loses_author_and_template_access(tmp_path: Path) -> None:
    engine, _, authors, typed, owner, reader, *_ = _setup(tmp_path)
    author = authors.create(owner, "Ada", "{}")
    template = typed.create(
        owner, "Letter", '{"fields":[],"repeats":[]}', b"docx", schema_version=1
    )
    authors.grant(owner, author.id, reader, if_match=author.etag)
    typed.grant(owner, template.id, reader, if_match=template.etag)
    with Session(engine) as database, database.begin():
        user = database.get(UserRow, str(reader))
        assert user is not None
        user.active = False
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.get(reader, author.id)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.get_version(reader, template.id, template.active_version_id)


def test_typed_version_sharing_exact_download_and_digest_fence(tmp_path: Path) -> None:
    engine, objects, _, typed, owner, reader, stranger, admin = _setup(tmp_path)
    schema = '{"fields":[],"repeats":[]}'
    first = typed.create(owner, "Letter", schema, b"docx-one", schema_version=1)
    assert first.version == 1
    version = typed.get_version(owner, first.id, first.active_version_id)
    assert version.docx_sha256 == hashlib.sha256(b"docx-one").hexdigest()
    assert typed.download(owner, first.id, version.id) == b"docx-one"
    with pytest.raises(TypedTemplateNotFoundError):
        typed.get(reader, first.id)
    shared = typed.grant(owner, first.id, reader, if_match=first.etag)
    assert [item.id for item in typed.list_visible(reader, query="Let")] == [first.id]
    assert typed.list_visible(reader, query="Other") == ()
    with pytest.raises(TypedTemplateNotFoundError):
        typed.revoke(admin, first.id, reader, if_match=shared.etag, is_admin=True)
    assert typed.download(reader, first.id, version.id) == b"docx-one"
    with Session(engine) as database:
        assert typed.require_version_access(
            database,
            reader,
            version.id,
            expected_docx_sha256=version.docx_sha256,
            expected_schema_sha256=version.schema_sha256,
        ).id == str(version.id)
    replaced = typed.replace(
        owner,
        first.id,
        if_match=shared.etag,
        schema_json=schema,
        docx_bytes=b"docx-two",
        schema_version=1,
    )
    assert replaced.version == 3
    assert typed.download(reader, first.id, version.id) == b"docx-one"
    assert typed.download(reader, first.id, replaced.active_version_id) == b"docx-two"
    assert [item.id for item in typed.list_versions(reader, first.id)] == [
        version.id,
        replaced.active_version_id,
    ]
    with pytest.raises(TypedTemplateConflictError):
        typed.replace(
            owner,
            first.id,
            if_match=shared.etag,
            schema_json=schema,
            docx_bytes=b"stale",
            schema_version=1,
        )
    typed.revoke(owner, first.id, reader, if_match=replaced.etag)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.download(reader, first.id, version.id)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.list_versions(reader, first.id)
    assert typed.list_visible(stranger) == ()
    objects.put(ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, version.id), b"tampered")
    with pytest.raises(TypedTemplateArtifactError):
        typed.download(owner, first.id, version.id)
    with Session(engine) as database:
        audits = database.scalars(select(TypedTemplateAuditRow)).all()
        assert [item.operation for item in audits] == [
            "create",
            "grant",
            "replace",
            "revoke",
        ]
        assert all("docx-one" not in str(item.__dict__) for item in audits)


def test_failed_typed_publication_remains_hidden_and_recovers(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, *_ = _setup(tmp_path)
    mocker.patch.object(objects, "put", side_effect=ObjectStoreError("offline"))
    with pytest.raises(TypedTemplateArtifactError):
        typed.create(
            owner,
            "Letter",
            '{"fields":[],"repeats":[]}',
            b"docx",
            schema_version=1,
        )
    assert typed.list_visible(owner) == ()
    with Session(engine) as database:
        pending = database.scalar(select(TypedTemplateVersionRow))
        assert pending is not None and pending.publication_state == "pending"
        pending_id = pending.id
    # The lease has not expired; recovery leaves the reservation untouched.
    assert typed.recover_pending(limit=1) == 0
    with Session(engine) as database, database.begin():
        row = database.get(TypedTemplateVersionRow, pending_id)
        assert row is not None
        row.lease_expires_at = row.created_at
    mocker.patch.object(objects, "delete", side_effect=ObjectStoreError("offline"))
    with pytest.raises(ObjectStoreError):
        typed.recover_pending(limit=1)
    with Session(engine) as database:
        assert database.get(TypedTemplateVersionRow, pending_id) is not None
    mocker.stopall()
    assert typed.recover_pending(limit=1) == 1
    with Session(engine) as database:
        assert database.get(TypedTemplateVersionRow, pending_id) is None


def test_expired_complete_typed_publication_is_recovered(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, *_ = _setup(tmp_path)
    content = b"complete-docx"
    mocker.patch.object(objects, "put", side_effect=ObjectStoreError("interrupted"))
    with pytest.raises(TypedTemplateArtifactError):
        typed.create(
            owner,
            "Letter",
            '{"fields":[],"repeats":[]}',
            content,
            schema_version=1,
        )
    with Session(engine) as database, database.begin():
        pending = database.scalar(select(TypedTemplateVersionRow))
        assert pending is not None
        template_id, version_id = pending.template_id, pending.id
        pending.lease_expires_at = pending.created_at
    mocker.stopall()
    objects.put(ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, UUID(version_id)), content)
    assert typed.recover_pending(limit=1) == 1
    assert typed.download(owner, UUID(template_id), UUID(version_id)) == content


def test_fill_plan_schema_matches_migration(tmp_path: Path) -> None:
    engine, *_ = _setup(tmp_path)
    actual = {
        column["name"] for column in inspect(engine).get_columns("composer_fill_plans")
    }
    expected = set(ComposerFillPlanRow.__table__.columns.keys())
    assert actual == expected
    checks = {
        check["name"]
        for check in inspect(engine).get_check_constraints("composer_fill_plans")
    }
    assert "ck_composer_fill_plan_draft_version" in checks


def test_author_inputs_owner_and_grant_state_are_bounded(tmp_path: Path) -> None:
    engine, _, authors, _, owner, reader, stranger, _ = _setup(tmp_path)
    for malformed in ("not-json", "[]", '{"role":1}', '{"role":{"value":"x"}}'):
        with pytest.raises(ValueError):
            authors.create(owner, "Ada", malformed)
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.create(uuid4(), "Ada", "{}")
    entry = authors.create(owner, "Ada", "{}")
    with pytest.raises(ValueError, match="Owner access"):
        authors.grant(owner, entry.id, owner, if_match=entry.etag)
    with pytest.raises(AuthorKnowledgeNotFoundError, match="Target"):
        authors.grant(owner, entry.id, uuid4(), if_match=entry.etag)
    with pytest.raises(AuthorKnowledgeConflictError, match="grant state"):
        authors.revoke(owner, entry.id, reader, if_match=entry.etag)
    shared = authors.grant(owner, entry.id, reader, if_match=entry.etag)
    with pytest.raises(AuthorKnowledgeConflictError, match="grant state"):
        authors.grant(owner, entry.id, reader, if_match=shared.etag)
    with Session(engine) as database, pytest.raises(AuthorKnowledgeConflictError):
        authors.require_access(
            database, reader, entry.id, expected_version=entry.version
        )
    revised = authors.update(
        owner,
        entry.id,
        if_match=shared.etag,
        name="  Ada Revised  ",
        fields_json='{"role":{"value":"Reviewer","provenance":"human_edited"}}',
    )
    assert revised.name == "Ada Revised"
    assert revised.fields["role"].value == "Reviewer"
    assert authors.list_visible(reader, limit=1, offset=1) == ()
    with pytest.raises(ValueError, match="query"):
        authors.list_visible(reader, query="x" * 101)
    with pytest.raises(AuthorKnowledgeNotFoundError):
        authors.update(
            stranger, entry.id, if_match=revised.etag, name="No", fields_json="{}"
        )


def test_typed_template_inputs_access_and_object_failures(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, reader, stranger, _ = _setup(tmp_path)
    canonical = '{"fields":[],"repeats":[]}'
    for name, schema, content, version in (
        ("", canonical, b"docx", 1),
        ("Letter", canonical, b"", 1),
        ("Letter", canonical, b"docx", 0),
        ("Letter", "not-json", b"docx", 1),
        ("Letter", '{"repeats":[],"fields":[]}', b"docx", 1),
    ):
        with pytest.raises(ValueError):
            typed.create(owner, name, schema, content, schema_version=version)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.create(uuid4(), "Absent", canonical, b"docx", schema_version=1)
    first = typed.create(owner, "Letter", canonical, b"docx", schema_version=1)
    other = typed.create(owner, "Other", canonical, b"other", schema_version=1)
    first_version = typed.get_version(owner, first.id, first.active_version_id)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.get_version(owner, other.id, first_version.id)
    with pytest.raises(TypedTemplateNotFoundError):
        typed.download(owner, other.id, first_version.id)
    with Session(engine) as database:
        with pytest.raises(TypedTemplateConflictError, match="content"):
            typed.require_version_access(
                database, owner, first_version.id, expected_docx_sha256="0" * 64
            )
        with pytest.raises(TypedTemplateConflictError, match="schema"):
            typed.require_version_access(
                database, owner, first_version.id, expected_schema_sha256="0" * 64
            )
    with pytest.raises(ValueError, match="Owner access"):
        typed.grant(owner, first.id, owner, if_match=first.etag)
    with pytest.raises(TypedTemplateNotFoundError, match="Target"):
        typed.grant(owner, first.id, uuid4(), if_match=first.etag)
    with pytest.raises(TypedTemplateConflictError, match="grant state"):
        typed.revoke(owner, first.id, reader, if_match=first.etag)
    shared = typed.grant(owner, first.id, reader, if_match=first.etag)
    with pytest.raises(TypedTemplateConflictError, match="grant state"):
        typed.grant(owner, first.id, reader, if_match=shared.etag)
    with pytest.raises(ValueError, match="query"):
        typed.list_visible(reader, query=cast("str", 1))
    with pytest.raises(TypedTemplateNotFoundError):
        typed.replace(
            stranger,
            first.id,
            if_match=shared.etag,
            schema_json=canonical,
            docx_bytes=b"replacement",
            schema_version=1,
        )
    key = ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, first_version.id)
    objects.delete(key)
    with pytest.raises(TypedTemplateArtifactError, match="unavailable"):
        typed.download(owner, first.id, first_version.id)
    mocker.patch.object(objects, "get_bounded", side_effect=ObjectStoreError("offline"))
    with pytest.raises(TypedTemplateArtifactError, match="unavailable"):
        typed.download(owner, first.id, first_version.id)


def test_expired_replacement_recovers_without_exposing_pending_version(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, *_ = _setup(tmp_path)
    canonical = '{"fields":[],"repeats":[]}'
    first = typed.create(owner, "Letter", canonical, b"original", schema_version=1)
    mocker.patch.object(objects, "put", side_effect=ObjectStoreError("interrupted"))
    with pytest.raises(TypedTemplateArtifactError):
        typed.replace(
            owner,
            first.id,
            if_match=first.etag,
            schema_json=canonical,
            docx_bytes=b"replacement",
            schema_version=1,
        )
    assert typed.list_versions(owner, first.id)[0].id == first.active_version_id
    assert len(typed.list_versions(owner, first.id)) == 1
    with Session(engine) as database, database.begin():
        pending = database.scalar(
            select(TypedTemplateVersionRow).where(
                TypedTemplateVersionRow.publication_state == "pending"
            )
        )
        assert pending is not None
        pending_id = UUID(pending.id)
        pending.lease_expires_at = pending.created_at
    mocker.stopall()
    key = ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, pending_id)
    objects.put(key, b"replacement")
    mocker.patch.object(objects, "get", side_effect=ObjectStoreError("temporary"))
    assert typed.recover_pending(limit=1) == 0
    mocker.stopall()
    assert typed.recover_pending(limit=1) == 1
    replaced = typed.get(owner, first.id)
    assert replaced.active_version_id == pending_id
    assert typed.download(owner, first.id, pending_id) == b"replacement"
    assert len(typed.list_versions(owner, first.id)) == 2
    with Session(engine) as database:
        row = database.get(TypedTemplateRow, str(first.id))
        assert row is not None and row.publication_state == "published"


def test_typed_write_readback_mismatch_stays_hidden_until_recovered(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, *_ = _setup(tmp_path)
    mocker.patch.object(objects, "get", return_value=b"wrong-readback")
    with pytest.raises(TypedTemplateArtifactError, match="integrity"):
        typed.create(
            owner,
            "Letter",
            '{"fields":[],"repeats":[]}',
            b"persisted-content",
            schema_version=1,
        )
    assert typed.list_visible(owner) == ()
    with Session(engine) as database, database.begin():
        pending = database.scalar(select(TypedTemplateVersionRow))
        assert pending is not None
        template_id, version_id = UUID(pending.template_id), UUID(pending.id)
        pending.lease_expires_at = pending.created_at
    mocker.stopall()
    assert typed.recover_pending(limit=1) == 1
    assert typed.download(owner, template_id, version_id) == b"persisted-content"
    with pytest.raises(ValueError, match="Recovery limit"):
        typed.recover_pending(limit=0)


def test_typed_recovery_discards_stale_replacement_reservation(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, objects, _, typed, owner, *_ = _setup(tmp_path)
    first = typed.create(
        owner, "Letter", '{"fields":[],"repeats":[]}', b"original", schema_version=1
    )
    actual_put = objects.put

    def concurrent_revision_change(key: ObjectKey, content: bytes) -> None:
        actual_put(key, content)
        with Session(engine) as database, database.begin():
            row = database.get(TypedTemplateRow, str(first.id))
            assert row is not None
            row.revision += 1

    mocker.patch.object(objects, "put", side_effect=concurrent_revision_change)
    with pytest.raises(TypedTemplateConflictError, match="changed"):
        typed.replace(
            owner,
            first.id,
            if_match=first.etag,
            schema_json='{"fields":[],"repeats":[]}',
            docx_bytes=b"replacement",
            schema_version=1,
        )
    mocker.stopall()
    assert typed.get(owner, first.id).active_version_id == first.active_version_id
    with Session(engine) as database, database.begin():
        pending = database.scalar(
            select(TypedTemplateVersionRow).where(
                TypedTemplateVersionRow.publication_state == "pending"
            )
        )
        assert pending is not None
        pending_id = UUID(pending.id)
        pending.lease_expires_at = pending.created_at
    assert typed.recover_pending(limit=1) == 1
    assert typed.get(owner, first.id).active_version_id == first.active_version_id
    assert len(typed.list_versions(owner, first.id)) == 1
    with Session(engine) as database:
        assert database.get(TypedTemplateVersionRow, str(pending_id)) is None
    with pytest.raises(TypedTemplateNotFoundError):
        typed.download(owner, first.id, pending_id)


def test_author_and_typed_mutations_share_content_free_operator_audit(
    tmp_path: Path,
) -> None:
    engine, _, authors, typed, owner, reader, *_ = _setup(tmp_path)
    author = authors.create(
        owner,
        "PRIVATE-AUTHOR",
        '{"role":{"value":"PRIVATE-ROLE","provenance":"supplied"}}',
    )
    author = authors.grant(owner, author.id, reader, if_match=author.etag)
    author = authors.update(
        owner,
        author.id,
        if_match=author.etag,
        name="PRIVATE-UPDATED",
        fields_json='{"role":{"value":"PRIVATE-UPDATED-ROLE","provenance":"human_edited"}}',
    )
    authors.revoke(owner, author.id, reader, if_match=author.etag)
    template = typed.create(
        owner,
        "PRIVATE-TEMPLATE",
        '{"fields":[],"repeats":[]}',
        b"PRIVATE-DOCX",
        schema_version=1,
    )
    template = typed.grant(owner, template.id, reader, if_match=template.etag)
    template = typed.replace(
        owner,
        template.id,
        if_match=template.etag,
        schema_json='{"fields":[],"repeats":[]}',
        docx_bytes=b"PRIVATE-REPLACEMENT",
        schema_version=1,
    )
    typed.revoke(owner, template.id, reader, if_match=template.etag)
    events = SqlComposerAuditRepository(engine).list_content_audit(owner)
    assert {event.operation for event in events} == {
        "author_create",
        "author_grant",
        "author_update",
        "author_revoke",
        "typed_template_create",
        "typed_template_grant",
        "typed_template_replace",
        "typed_template_revoke",
    }
    assert {event.target_kind for event in events} == {
        "author_knowledge",
        "typed_template",
    }
    assert all(event.draft_id is None and event.draft_version > 0 for event in events)
    observed = SqlAuditReader(engine).list_recent(offset=0, limit=100)
    assert {event.id for event in events} <= {record.id for record in observed}
    with Session(engine) as database:
        author_evidence = database.scalars(
            select(AuthorKnowledgeAuditRow).where(
                AuthorKnowledgeAuditRow.target_user_id == str(reader)
            )
        ).all()
        typed_evidence = database.scalars(
            select(TypedTemplateAuditRow).where(
                TypedTemplateAuditRow.target_user_id == str(reader)
            )
        ).all()
    assert {row.operation for row in author_evidence} == {"grant", "revoke"}
    assert {row.operation for row in typed_evidence} == {"grant", "revoke"}
    serialized = repr((events, observed, author_evidence, typed_evidence))
    for secret in (
        "PRIVATE-AUTHOR",
        "PRIVATE-ROLE",
        "PRIVATE-TEMPLATE",
        "PRIVATE-DOCX",
    ):
        assert secret not in serialized


def test_common_audit_failure_rolls_back_author_and_template_grants(
    tmp_path: Path,
) -> None:
    engine, _, authors, typed, owner, reader, *_ = _setup(tmp_path)
    author = authors.create(owner, "Ada", "{}")
    template = typed.create(
        owner, "Letter", '{"fields":[],"repeats":[]}', b"docx", schema_version=1
    )
    with Session(engine) as database, database.begin():
        database.execute(
            text(
                "CREATE TRIGGER reject_t91_common_audit "
                "BEFORE INSERT ON composer_content_audit "
                "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )
    with pytest.raises(AuthorKnowledgeConflictError):
        authors.grant(owner, author.id, reader, if_match=author.etag)
    with pytest.raises(TypedTemplateConflictError):
        typed.grant(owner, template.id, reader, if_match=template.etag)
    assert authors.get(owner, author.id) == author
    assert typed.get(owner, template.id) == template
    with Session(engine) as database:
        assert (
            database.scalar(
                select(AuthorKnowledgeAuditRow).where(
                    AuthorKnowledgeAuditRow.operation == "grant"
                )
            )
            is None
        )
        assert (
            database.scalar(
                select(TypedTemplateAuditRow).where(
                    TypedTemplateAuditRow.operation == "grant"
                )
            )
            is None
        )


def test_failed_publication_audit_keeps_template_hidden_until_recovery(
    tmp_path: Path,
) -> None:
    engine, _, authors, typed, owner, *_ = _setup(tmp_path)
    with Session(engine) as database, database.begin():
        database.execute(
            text(
                "CREATE TRIGGER reject_t91_publication_audit "
                "BEFORE INSERT ON composer_content_audit "
                "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
            )
        )
    with pytest.raises(PersistenceError):
        authors.create(owner, "Rejected author", "{}")
    with pytest.raises(PersistenceError):
        typed.create(
            owner,
            "Recovered template",
            '{"fields":[],"repeats":[]}',
            b"verified-docx",
            schema_version=1,
        )
    assert authors.list_visible(owner) == ()
    assert typed.list_visible(owner) == ()
    with Session(engine) as database, database.begin():
        pending = database.scalar(
            select(TypedTemplateVersionRow).where(
                TypedTemplateVersionRow.publication_state == "pending"
            )
        )
        assert pending is not None
        template_id, version_id = UUID(pending.template_id), UUID(pending.id)
        pending.lease_expires_at = pending.created_at
        database.execute(text("DROP TRIGGER reject_t91_publication_audit"))
    assert typed.recover_pending(limit=1) == 1
    assert typed.download(owner, template_id, version_id) == b"verified-docx"
    assert {
        event.operation
        for event in SqlComposerAuditRepository(engine).list_content_audit(owner)
    } == {"typed_template_create"}


def test_specialized_audit_retention_is_bounded_guarded_and_receipted(
    tmp_path: Path,
) -> None:
    engine, objects, _, _, owner, *_ = _setup(tmp_path)
    now = datetime.now(UTC)
    old = now - timedelta(days=366)
    recent = now - timedelta(days=364)
    limits = AuthorKnowledgeLimits(10, 100, 100, 100, 100)
    old_authors = SqlAuthorKnowledgeRepository(engine, limits, clock=lambda: old)
    recent_authors = SqlAuthorKnowledgeRepository(engine, limits, clock=lambda: recent)
    old_typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5), clock=lambda: old
    )
    recent_typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5), clock=lambda: recent
    )
    old_author = old_authors.create(owner, "Old", "{}")
    recent_author = recent_authors.create(owner, "Recent", "{}")
    old_template = old_typed.create(
        owner, "Old", '{"fields":[],"repeats":[]}', b"old", schema_version=1
    )
    recent_template = recent_typed.create(
        owner, "Recent", '{"fields":[],"repeats":[]}', b"recent", schema_version=1
    )
    with (
        pytest.raises(SQLAlchemyError),
        Session(engine) as database,
        database.begin(),
    ):
        database.execute(update(AuthorKnowledgeAuditRow).values(operation="forged"))
    with (
        pytest.raises(SQLAlchemyError),
        Session(engine) as database,
        database.begin(),
    ):
        database.execute(text("DELETE FROM typed_template_audit"))
    audit = SqlComposerAuditRepository(engine)
    cutoff = now - timedelta(days=365)
    assert audit.cleanup_t91_audit(cutoff_at=cutoff, limit=1) == 1
    assert audit.cleanup_t91_audit(cutoff_at=cutoff, limit=1) == 1
    assert audit.cleanup_t91_audit(cutoff_at=cutoff, limit=1) == 0
    with pytest.raises(ValueError, match="limit"):
        audit.cleanup_t91_audit(cutoff_at=cutoff, limit=0)
    with Session(engine) as database:
        authors_left = database.scalars(select(AuthorKnowledgeAuditRow)).all()
        templates_left = database.scalars(select(TypedTemplateAuditRow)).all()
        common = database.scalars(select(ComposerContentAuditRow)).all()
        receipts = database.scalars(
            select(RetentionCleanupRunRow).where(
                RetentionCleanupRunRow.kind == "composer_t91_audit"
            )
        ).all()
    assert {row.author_id for row in authors_left} == {str(recent_author.id)}
    assert {row.template_id for row in templates_left} == {str(recent_template.id)}
    assert {row.target_id for row in common} >= {
        str(old_author.id),
        str(old_template.id),
    }
    assert sorted(row.removed_count for row in receipts) == [0, 1, 1]
