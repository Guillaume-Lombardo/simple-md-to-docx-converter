"""Exercise reviewed typed filling across real HTTP, SQL, and object boundaries."""

import json
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.author_knowledge import AuthorKnowledgeLimits
from markweave.config import Settings
from markweave.http.composer_step_runner import ComposerStepRunner
from markweave.malware import MalwareScannerUnavailableError
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.fill_plans import SqlFillPlanRepository
from markweave.persistence.composer.typed_templates import SqlTypedTemplateRepository
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import ComposerRevisionRow, UserRow
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.integration.composer.test_typed_docx_fill import _schema, _template
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _client(tmp_path: Path, mocker: MockerFixture):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner = User(UUID(int=1), "owner", "owner", "hash", Role.USER)
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner.id),
                username=owner.username,
                normalized_username=owner.normalized_username,
                password_hash="hash",  # noqa: S106 - isolated fixture
                role=owner.role.value,
                active=True,
                auth_version=0,
                password_change_required=False,
            )
        )
    objects = FilesystemObjectStore(tmp_path)
    composer = SqlComposerRepository(engine, objects)
    authors = SqlAuthorKnowledgeRepository(
        engine,
        AuthorKnowledgeLimits(
            max_fields=20,
            max_name_length=200,
            max_field_value_length=200,
            max_field_name_length=100,
            max_citation_length=200,
        ),
    )
    typed = SqlTypedTemplateRepository(
        engine, objects, publication_lease=timedelta(minutes=5)
    )
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = owner
    authentication.authenticate.return_value = owner
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - isolated fixture
        storage_profile="standalone",
        standalone_data_directory=str(tmp_path),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=20_000,
        composer_http_request_max_bytes=40_000,
        composer_maximum_request_bytes=20_000,
        composer_maximum_output_tokens=32,
    )
    scanner = mocker.Mock()
    connections = mocker.Mock()
    runner = mocker.Mock(spec=ComposerStepRunner)
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=objects,
            jobs=mocker.Mock(),
            scanner=scanner,
            composer_store=composer,
            composer_authors=authors,
            composer_fill_templates=typed,
            composer_fill_plans=SqlFillPlanRepository(engine),
            composer_connections=connections,
            composer_steps=runner,
        ),
    )
    return (
        engine,
        TestClient(app, base_url="https://testserver"),
        composer,
        scanner,
        owner,
        authors,
        connections,
        runner,
    )


def test_typed_fill_questions_approval_publication_and_frozen_regeneration(  # noqa: PLR0915 - complete review and crash-recovery workflow
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, scanner, owner, authors, _connections, _runner = _client(
        tmp_path, mocker
    )
    draft = composer.create_draft_with_source(
        owner.id,
        b"# Input\n",
        "scanner-approved",
        title="Input",
        content="# Input\n",
        media_type="text/markdown",
    )
    prefix = f"/api/v1/composer/drafts/{draft.id}"
    with TestClient(client.app, base_url="https://testserver") as http:
        created_author = http.post(
            "/api/v1/composer/authors",
            json={"name": "Ada Lovelace", "fields": {}},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert created_author.status_code == 201, created_author.text
        author_id = created_author.json()["id"]
        typed = http.post(
            "/api/v1/composer/fill-templates",
            data={"name": "Decision", "schema": json.dumps(_schema())},
            files={
                "file": (
                    "decision.docx",
                    _template(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert typed.status_code == 201, typed.text
        scanner.scan.assert_called_once()
        template = typed.json()
        source = http.post(
            f"{prefix}/revisions/from-draft",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": draft.etag,
                "Idempotency-Key": "source",
            },
        )
        assert source.status_code == 201, source.text
        current = composer.get_draft(owner.id, draft.id)
        plan = http.post(
            f"{prefix}/fill-plans",
            json={
                "source_revision_id": source.json()["id"],
                "template_id": template["id"],
                "template_version_id": template["active_version_id"],
                "author_ids": [author_id],
                "values": {
                    "decision.date": "2026-09-24",
                    "decision.approved": True,
                    "findings": [{"title": "First", "score": 73}],
                },
            },
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": current.etag,
                "Idempotency-Key": "plan",
            },
        )
        assert plan.status_code == 201, plan.text
        pending = plan.json()
        assert pending["values"]["author.name"] == "Ada Lovelace"
        assert pending["questions"] == [
            {
                "path": "finding.count",
                "text": "What value should be used for finding.count?",
                "reason": "missing",
            }
        ]
        plan_path = f"{prefix}/fill-plans/{pending['id']}"
        unresolved = http.post(
            f"{plan_path}/approve",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": pending["etag"],
                "Idempotency-Key": "premature",
            },
        )
        assert unresolved.status_code == 412
        values = {**pending["values"], "finding.count": 1}
        provenance = {
            **pending["provenance"],
            "finding.count": {"kind": "human_edited"},
        }
        updated = http.patch(
            plan_path,
            json={"values": values, "provenance": provenance},
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": pending["etag"],
                "Idempotency-Key": "answer",
            },
        )
        assert updated.status_code == 200, updated.text
        approved = http.post(
            f"{plan_path}/approve",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": updated.json()["etag"],
                "Idempotency-Key": "approve",
            },
        )
        assert approved.status_code == 200, approved.text
        plan_store = client.app.state.components.composer_fill_plans
        interrupted_link = mocker.patch.object(
            plan_store, "mark_published", side_effect=PersistenceError()
        )
        interrupted = http.post(
            f"{plan_path}/publish",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": approved.json()["etag"],
                "Idempotency-Key": "publish",
            },
        )
        assert interrupted.status_code == 503
        committed = composer.get_draft(owner.id, draft.id).current_revision_id
        assert committed is not None
        mocker.stop(interrupted_link)
        authors.update(
            owner.id,
            UUID(author_id),
            if_match=created_author.headers["ETag"],
            name="Ada Lovelace",
            fields_json="{}",
        )
        published = http.post(
            f"{plan_path}/publish",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": approved.json()["etag"],
                "Idempotency-Key": "publish",
            },
        )
        assert published.status_code == 201, published.text
        result = published.json()
        assert result["id"] == str(committed)
        assert result["operation"] == "fill_template"
        original = http.get(f"{prefix}/revisions/{result['id']}/artifacts/download")
        assert original.status_code == 200
        with ZipFile(BytesIO(original.content)) as package:
            assert b"Ada Lovelace" in package.read("word/document.xml")
        with Session(engine) as database:
            row = database.get(ComposerRevisionRow, result["id"])
            assert row is not None and row.typed_fill_snapshot is not None
            original_snapshot = row.typed_fill_snapshot
        for number, corrupt in enumerate(("engine", "values", "digest"), start=1):
            snapshot = json.loads(original_snapshot)
            if corrupt == "engine":
                snapshot["component_versions"]["typed_docx_fill_engine"] += 1
            elif corrupt == "values":
                del snapshot["approved_values"]["decision.date"]
            else:
                snapshot["result_sha256"] = "0" * 64
            with Session(engine) as database, database.begin():
                row = database.get(ComposerRevisionRow, result["id"])
                assert row is not None
                row.typed_fill_snapshot = json.dumps(snapshot)
            denied = http.post(
                f"{prefix}/revisions/{result['id']}/regenerations",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": published.headers["ETag"],
                    "Idempotency-Key": f"corrupt-{number}",
                },
            )
            assert denied.status_code == 412
            with Session(engine) as database, database.begin():
                row = database.get(ComposerRevisionRow, result["id"])
                assert row is not None
                row.typed_fill_snapshot = original_snapshot
        regeneration = http.post(
            f"{prefix}/revisions/{result['id']}/regenerations",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": published.headers["ETag"],
                "Idempotency-Key": "regenerate",
            },
        )
        assert regeneration.status_code == 201, regeneration.text
        regenerated = http.get(
            f"{prefix}/revisions/{regeneration.json()['id']}/artifacts/download"
        )
        assert regenerated.content == original.content
        assert http.get(plan_path).status_code == 412
    engine.dispose()


def test_author_prompt_preview_is_exact_and_stale_version_never_starts_model(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, authors, connections, runner = _client(
        tmp_path, mocker
    )
    draft = composer.create_draft_with_source(
        owner.id,
        b"# Input\n",
        "scanner-approved",
        title="Input",
        content="# Input\n",
        media_type="text/markdown",
    )
    connection_id = UUID(int=17)
    connection = mocker.Mock()
    connection.enabled = True
    connection.endpoint = "https://llm.example.test/v1"
    connection.selected_model = "approved"
    connection.permitted_models = ("approved",)
    connections.get_visible.return_value = connection
    with client:
        author = client.post(
            "/api/v1/composer/authors",
            json={
                "name": "Ada",
                "fields": {
                    "affiliation": {
                        "value": "University",
                        "provenance": "cited",
                        "source_reference": "profile-1",
                    }
                },
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert author.status_code == 201, author.text
        author_id = UUID(author.json()["id"])
        preview = client.post(
            f"/api/v1/composer/drafts/{draft.id}/model-steps/preview",
            json={
                "connection_id": str(connection_id),
                "approved_endpoint": connection.endpoint,
                "approved_model": connection.selected_model,
                "content": "Write a summary.",
                "author_ids": [str(author_id)],
                "max_output_tokens": 8,
            },
            headers={"X-CSRF-Token": "csrf", "If-Match": draft.etag},
        )
        assert preview.status_code == 200, preview.text
        reviewed = preview.json()
        assert "Ada" in reviewed["transmitted_content"]
        assert "University" in reviewed["transmitted_content"]
        assert "profile-1" in reviewed["transmitted_content"]
        assert "Write a summary." in reviewed["transmitted_content"]
        assert len(reviewed["preview_digest"]) == 64
        authors.update(
            owner.id,
            author_id,
            if_match=author.headers["ETag"],
            name="Ada",
            fields_json=json.dumps(
                {
                    "affiliation": {
                        "value": "New University",
                        "provenance": "human_edited",
                    }
                }
            ),
        )
        stale = client.post(
            f"/api/v1/composer/drafts/{draft.id}/model-steps",
            json={
                "connection_id": str(connection_id),
                "approved_endpoint": connection.endpoint,
                "approved_model": connection.selected_model,
                "content": "Write a summary.",
                "max_output_tokens": 8,
                "author_refs": reviewed["author_refs"],
                "author_preview_digest": reviewed["preview_digest"],
            },
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": draft.etag,
                "Idempotency-Key": "stale-preview",
            },
        )
        assert stale.status_code == 412
        runner.start.assert_not_called()
    engine.dispose()


def test_typed_upload_scans_before_schema_or_ooxml_parse_and_fails_closed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, _composer, scanner, _owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    scanner.scan.side_effect = MalwareScannerUnavailableError("scanner unavailable")
    with client:
        blocked = client.post(
            "/api/v1/composer/fill-templates",
            data={"name": "Bad", "schema": "not-json"},
            files={"file": ("bad.docx", b"not-a-zip", "application/octet-stream")},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert blocked.status_code == 503
        scanner.scan.assert_called_once_with(b"not-a-zip")
        assert client.get("/api/v1/composer/fill-templates").json()["templates"] == []
    engine.dispose()


def test_authored_schema_default_is_attributed_and_frozen(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    draft = composer.create_draft_with_source(
        owner.id,
        b"# Input\n",
        "scanner-approved",
        title="Input",
        content="# Input\n",
        media_type="text/markdown",
    )
    prefix = f"/api/v1/composer/drafts/{draft.id}"
    schema = json.loads(json.dumps(_schema()))
    schema["fields"][3]["required"] = False
    schema["fields"][3]["default"] = 1
    with client:
        typed = client.post(
            "/api/v1/composer/fill-templates",
            data={"name": "Default", "schema": json.dumps(schema)},
            files={
                "file": (
                    "default.docx",
                    _template(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert typed.status_code == 201, typed.text
        source = client.post(
            f"{prefix}/revisions/from-draft",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": draft.etag,
                "Idempotency-Key": "source",
            },
        )
        assert source.status_code == 201, source.text
        template = typed.json()
        plan = client.post(
            f"{prefix}/fill-plans",
            json={
                "source_revision_id": source.json()["id"],
                "template_id": template["id"],
                "template_version_id": template["active_version_id"],
                "values": {
                    "author.name": "Ada",
                    "decision.date": "2026-09-24",
                    "decision.approved": True,
                    "findings": [],
                },
            },
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": composer.get_draft(owner.id, draft.id).etag,
                "Idempotency-Key": "plan",
            },
        )
        assert plan.status_code == 201, plan.text
        pending = plan.json()
        assert pending["values"]["finding.count"] == 1
        assert pending["provenance"]["finding.count"]["kind"] == "template_default"
        assert pending["questions"] == []
        approved = client.post(
            f"{prefix}/fill-plans/{pending['id']}/approve",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": pending["etag"],
                "Idempotency-Key": "approve",
            },
        )
        assert approved.status_code == 200, approved.text
        published = client.post(
            f"{prefix}/fill-plans/{pending['id']}/publish",
            headers={
                "X-CSRF-Token": "csrf",
                "If-Match": approved.json()["etag"],
                "Idempotency-Key": "publish",
            },
        )
        assert published.status_code == 201, published.text
        frozen = json.loads(published.json()["typed_fill_snapshot"])
        assert frozen["approved_values"]["finding.count"] == 1
        assert frozen["provenance"]["finding.count"]["kind"] == "human_approved"
    engine.dispose()
