"""Distributed-profile author grants and typed DOCX object publication."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.author_knowledge import (
    AuthorKnowledgeLimits,
    AuthorKnowledgeNotFoundError,
)
from markweave.persistence.composer.audit import SqlComposerAuditRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.typed_templates import (
    SqlTypedTemplateRepository,
    TypedTemplateNotFoundError,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import SqlAuditReader
from markweave.persistence.schema import (
    AuthorKnowledgeAuditRow,
    RetentionCleanupRunRow,
    TypedTemplateAuditRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import ObjectKey, ObjectScope
from tests.integration.postgres.test_postgres_composer_foundations import _store

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def test_postgresql_s3_author_grants_and_typed_versions_survive_restart() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    owner, reader = uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id in (owner, reader):
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
    objects = _store()
    limits = AuthorKnowledgeLimits(
        max_fields=10,
        max_name_length=100,
        max_field_value_length=100,
        max_field_name_length=100,
        max_citation_length=100,
    )
    authors = SqlAuthorKnowledgeRepository(engine, limits)
    typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5)
    )
    version_id = None
    try:
        author = authors.create(
            owner,
            "Ada",
            json.dumps(
                {"affiliation": {"value": "University", "provenance": "supplied"}}
            ),
        )
        authors.grant(owner, author.id, reader, if_match=author.etag)
        assert (
            SqlAuthorKnowledgeRepository(engine, limits).get(reader, author.id).name
            == "Ada"
        )
        template = typed.create(
            owner,
            "Letter",
            '{"fields":[],"repeats":[]}',
            b"distributed-docx",
            schema_version=1,
        )
        version_id = template.active_version_id
        shared = typed.grant(owner, template.id, reader, if_match=template.etag)
        assert (
            SqlTypedTemplateRepository(
                engine, objects, publication_lease=timedelta(minutes=5)
            ).download(reader, template.id, version_id)
            == b"distributed-docx"
        )
        authors.revoke(
            owner, author.id, reader, if_match=authors.get(owner, author.id).etag
        )
        typed.revoke(owner, template.id, reader, if_match=shared.etag)
        with pytest.raises(AuthorKnowledgeNotFoundError):
            authors.get(reader, author.id)
        with pytest.raises(TypedTemplateNotFoundError):
            typed.get_version(reader, template.id, version_id)
    finally:
        if version_id is not None:
            objects.delete(ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, version_id))
        objects.close()


def test_postgresql_t91_audit_is_visible_guarded_and_bounded() -> None:
    engine = create_database_engine(os.environ["MARKWEAVE_TEST_POSTGRES_URL"])
    upgrade_database(engine)
    owner, reader = uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id in (owner, reader):
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
    now = datetime.now(UTC)
    old = now - timedelta(days=366)
    objects = _store()
    authors = SqlAuthorKnowledgeRepository(
        engine, AuthorKnowledgeLimits(10, 100, 100, 100, 100), clock=lambda: old
    )
    typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5), clock=lambda: old
    )
    version_id = None
    try:
        author = authors.create(
            owner,
            "PRIVATE-AUTHOR",
            json.dumps({"role": {"value": "PRIVATE-ROLE", "provenance": "supplied"}}),
        )
        authors.grant(owner, author.id, reader, if_match=author.etag)
        template = typed.create(
            owner,
            "PRIVATE-TEMPLATE",
            '{"fields":[],"repeats":[]}',
            b"PRIVATE-DOCX",
            schema_version=1,
        )
        version_id = template.active_version_id
        events = SqlComposerAuditRepository(engine).list_content_audit(owner)
        assert {event.operation for event in events} == {
            "author_create",
            "author_grant",
            "typed_template_create",
        }
        observed = SqlAuditReader(engine).list_recent(offset=0, limit=10_000)
        assert {event.id for event in events} <= {record.id for record in observed}
        assert "PRIVATE-ROLE" not in repr((events, observed))
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                update(AuthorKnowledgeAuditRow)
                .where(AuthorKnowledgeAuditRow.author_id == str(author.id))
                .values(operation="forged")
            )
        with (
            pytest.raises(SQLAlchemyError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                text("DELETE FROM typed_template_audit WHERE template_id = :id"),
                {"id": str(template.id)},
            )
        audit = SqlComposerAuditRepository(engine)
        cutoff = now - timedelta(days=365)
        assert [
            audit.cleanup_t91_audit(cutoff_at=cutoff, limit=1) for _ in range(4)
        ] == [
            1,
            1,
            1,
            0,
        ]
        with Session(engine) as database:
            assert (
                database.scalar(
                    select(AuthorKnowledgeAuditRow).where(
                        AuthorKnowledgeAuditRow.author_id == str(author.id)
                    )
                )
                is None
            )
            assert (
                database.scalar(
                    select(TypedTemplateAuditRow).where(
                        TypedTemplateAuditRow.template_id == str(template.id)
                    )
                )
                is None
            )
            receipts = database.scalars(
                select(RetentionCleanupRunRow).where(
                    RetentionCleanupRunRow.kind == "composer_t91_audit",
                    RetentionCleanupRunRow.completed_at >= now,
                )
            ).all()
        assert sorted(row.removed_count for row in receipts) == [0, 1, 1, 1]
        assert len(audit.list_content_audit(owner)) == 3
    finally:
        if version_id is not None:
            objects.delete(ObjectKey(ObjectScope.TYPED_TEMPLATE, owner, version_id))
        objects.close()
        engine.dispose()
