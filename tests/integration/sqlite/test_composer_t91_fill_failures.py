"""Typed fill questions, human precedence, and frozen-input failure fences."""

import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from defusedxml.ElementTree import fromstring
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from markweave.persistence.schema import TypedTemplateVersionRow
from tests.integration.composer.test_typed_docx_fill import (
    _W,
    _parts,
    _repack,
    _schema,
    _sdt,
    _template,
)
from tests.integration.sqlite.test_composer_t91_http import _client

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _source(client, composer, owner):
    draft = composer.create_draft_with_source(
        owner.id,
        b"# Source\n",
        "scanner-approved",
        title="Source",
        content="# Source\n",
        media_type="text/markdown",
    )
    prefix = f"/api/v1/composer/drafts/{draft.id}"
    captured = client.post(
        f"{prefix}/revisions/from-draft",
        headers={
            "X-CSRF-Token": "csrf",
            "If-Match": draft.etag,
            "Idempotency-Key": "capture",
        },
    )
    assert captured.status_code == 201, captured.text
    return draft, prefix, captured.json(), composer.get_draft(owner.id, draft.id)


def _typed(client, schema=None, content=None):
    response = client.post(
        "/api/v1/composer/fill-templates",
        data={"name": "Qualified", "schema": json.dumps(schema or _schema())},
        files={
            "file": (
                "qualified.docx",
                content or _template(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers={"X-CSRF-Token": "csrf"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _authors_repeat() -> tuple[dict[str, object], bytes]:
    schema = json.loads(json.dumps(_schema()))
    schema["repeats"] = [
        {
            "name": "authors",
            "min_items": 0,
            "max_items": 3,
            "fields": [
                {"name": "name", "type": "text", "required": True, "constraints": {}},
                {"name": "role", "type": "text", "required": True, "constraints": {}},
            ],
        }
    ]
    content = _template()
    root = fromstring(_parts(content)["word/document.xml"])
    renamed = {
        "repeat:findings": "repeat:authors",
        "findings.title": "authors.name",
        "findings.score": "authors.role",
    }
    for tag in root.iter(_W + "tag"):
        old = tag.get(_W + "val")
        if old in renamed:
            tag.set(_W + "val", renamed[old])
    return schema, _repack(content, {"word/document.xml": ET.tostring(root)})


def _author_scalars() -> tuple[dict[str, object], bytes]:
    schema = json.loads(json.dumps(_schema()))
    for name in ("author.role", "author.affiliation"):
        schema["fields"].append(
            {"name": name, "type": "text", "required": True, "constraints": {}}
        )
    content = _template()
    root = fromstring(_parts(content)["word/document.xml"])
    body = root.find(_W + "body")
    assert body is not None
    position = next(
        (index for index, child in enumerate(body) if child.tag == _W + "sectPr"),
        len(body),
    )
    for name in ("author.role", "author.affiliation"):
        body.insert(position, _sdt(name, "PLACEHOLDER", block=True))
        position += 1
    return schema, _repack(content, {"word/document.xml": ET.tostring(root)})


def test_single_author_fact_provenance_and_unresolved_question(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    try:
        with client:
            author = client.post(
                "/api/v1/composer/authors",
                json={
                    "name": "Ada",
                    "fields": {
                        "role": {
                            "value": "Reviewer",
                            "provenance": "model_suggested",
                            "source_reference": "review-note-1",
                        }
                    },
                },
                headers={"X-CSRF-Token": "csrf"},
            )
            assert author.status_code == 201, author.text
            schema, docx = _author_scalars()
            template = _typed(client, schema, docx)
            _draft, prefix, source, current = _source(client, composer, owner)
            plan = client.post(
                f"{prefix}/fill-plans",
                json={
                    "source_revision_id": source["id"],
                    "template_id": template["id"],
                    "template_version_id": template["active_version_id"],
                    "author_ids": [author.json()["id"]],
                    "values": {
                        "decision.date": "2026-09-24",
                        "decision.approved": True,
                        "finding.count": 0,
                    },
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "single-author-facts",
                },
            )
            assert plan.status_code == 201, plan.text
            proposed = plan.json()
            assert proposed["values"]["author.name"] == "Ada"
            assert proposed["values"]["author.role"] == "Reviewer"
            assert proposed["values"].get("author.affiliation") is None
            assert proposed["provenance"]["author.role"] == {
                "kind": "model_suggested",
                "source_reference": "review-note-1",
            }
            assert {item["path"] for item in proposed["questions"]} == {
                "author.role",
                "author.affiliation",
            }
    finally:
        engine.dispose()


def test_multiple_authors_create_durable_ambiguities_and_bounded_repeat(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    try:
        with client:
            first = client.post(
                "/api/v1/composer/authors",
                json={
                    "name": "Ada",
                    "fields": {
                        "role": {"value": "Reviewer", "provenance": "model_suggested"}
                    },
                },
                headers={"X-CSRF-Token": "csrf"},
            )
            second = client.post(
                "/api/v1/composer/authors",
                json={"name": "Grace", "fields": {}},
                headers={"X-CSRF-Token": "csrf"},
            )
            assert first.status_code == second.status_code == 201
            schema, docx = _authors_repeat()
            template = _typed(client, schema, docx)
            draft, prefix, source, current = _source(client, composer, owner)
            plan = client.post(
                f"{prefix}/fill-plans",
                json={
                    "source_revision_id": source["id"],
                    "template_id": template["id"],
                    "template_version_id": template["active_version_id"],
                    "author_ids": [first.json()["id"], second.json()["id"]],
                    "values": {
                        "decision.date": "2026-09-24",
                        "decision.approved": True,
                        "finding.count": 2,
                    },
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "authors-plan",
                },
            )
            assert plan.status_code == 201, plan.text
            proposed = plan.json()
            assert proposed["values"]["authors"] == [
                {"name": "Ada", "role": "Reviewer"},
                {"name": "Grace"},
            ]
            assert {item["path"] for item in proposed["questions"]} == {
                "author.name",
                "authors[0].role",
                "authors[1].role",
            }
            assert proposed["provenance"]["authors[0].role"]["kind"] == (
                "model_suggested"
            )
            assert (
                client.get(f"{prefix}/fill-plans").json()["plans"][0]["id"]
                == proposed["id"]
            )
            assert (
                client.get(f"{prefix}/fill-plans/{proposed['id']}").json()["questions"]
                == proposed["questions"]
            )
            duplicate = client.post(
                f"{prefix}/fill-plans",
                json={
                    "source_revision_id": source["id"],
                    "template_id": template["id"],
                    "template_version_id": template["active_version_id"],
                    "author_ids": [first.json()["id"], first.json()["id"]],
                    "values": {},
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": composer.get_draft(owner.id, draft.id).etag,
                    "Idempotency-Key": "duplicate-authors",
                },
            )
            assert duplicate.status_code == 422
    finally:
        engine.dispose()


def test_human_value_cannot_be_dropped_or_demoted_to_model_suggestion(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    try:
        with client:
            template = _typed(client)
            _draft, prefix, source, current = _source(client, composer, owner)
            values = {
                "author.name": "Ada",
                "decision.date": "2026-09-24",
                "decision.approved": True,
                "finding.count": 1,
                "findings": [{"title": "Reviewed", "score": 73}],
            }
            created = client.post(
                f"{prefix}/fill-plans",
                json={
                    "source_revision_id": source["id"],
                    "template_id": template["id"],
                    "template_version_id": template["active_version_id"],
                    "values": values,
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "plan",
                },
            )
            assert created.status_code == 201, created.text
            plan = created.json()
            path = f"{prefix}/fill-plans/{plan['id']}"
            provenance = {
                **plan["provenance"],
                "author.name": {"kind": "human_edited"},
            }
            reviewed = client.patch(
                path,
                json={"values": values, "provenance": provenance},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": plan["etag"],
                    "Idempotency-Key": "human-edit",
                },
            )
            assert reviewed.status_code == 200, reviewed.text
            current_plan = reviewed.json()
            for altered_values, altered_provenance in (
                (values, {**provenance, "author.name": {"kind": "model_suggested"}}),
                (
                    {**values, "author.name": "Grace"},
                    {**provenance, "author.name": {"kind": "human_approved"}},
                ),
                (
                    {
                        key: value
                        for key, value in values.items()
                        if key != "author.name"
                    },
                    {
                        key: value
                        for key, value in provenance.items()
                        if key != "author.name"
                    },
                ),
            ):
                denied = client.patch(
                    path,
                    json={
                        "values": altered_values,
                        "provenance": altered_provenance,
                    },
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": current_plan["etag"],
                        "Idempotency-Key": "demote",
                    },
                )
                assert denied.status_code == 412, denied.text
            orphan = client.patch(
                path,
                json={
                    "values": values,
                    "provenance": {
                        **provenance,
                        "findings[1].title": {"kind": "human_approved"},
                    },
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current_plan["etag"],
                    "Idempotency-Key": "orphan-provenance",
                },
            )
            assert orphan.status_code == 422
    finally:
        engine.dispose()


def test_schema_digest_and_repeat_shape_fail_before_fill_plan_persistence(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    try:
        with client:
            template = _typed(client)
            _draft, prefix, source, current = _source(client, composer, owner)
            payload = {
                "source_revision_id": source["id"],
                "template_id": template["id"],
                "template_version_id": template["active_version_id"],
                "values": {
                    "author.name": "Ada",
                    "decision.date": "2026-09-24",
                    "decision.approved": True,
                    "finding.count": 0,
                },
            }
            with Session(engine) as database, database.begin():
                version = database.get(
                    TypedTemplateVersionRow, template["active_version_id"]
                )
                assert version is not None
                original_schema_sha256 = version.schema_sha256
                version.schema_sha256 = "0" * 64
            digest_mismatch = client.post(
                f"{prefix}/fill-plans",
                json=payload,
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "changed-schema-digest",
                },
            )
            assert digest_mismatch.status_code == 412
            with Session(engine) as database, database.begin():
                version = database.get(
                    TypedTemplateVersionRow, template["active_version_id"]
                )
                assert version is not None
                version.schema_sha256 = original_schema_sha256
            malformed_repeat = client.post(
                f"{prefix}/fill-plans",
                json={
                    **payload,
                    "values": {**payload["values"], "findings": [1]},
                },
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "malformed-repeat-row",
                },
            )
            assert malformed_repeat.status_code == 422
            assert client.get(f"{prefix}/fill-plans").json()["plans"] == []
    finally:
        engine.dispose()


def test_plan_preconditions_and_incomplete_provenance_fail_without_revision(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, composer, _scanner, owner, _authors, _connections, _runner = (
        _client(tmp_path, mocker)
    )
    try:
        with client:
            template = _typed(client)
            _draft, prefix, source, current = _source(client, composer, owner)
            payload = {
                "source_revision_id": source["id"],
                "template_id": template["id"],
                "template_version_id": template["active_version_id"],
                "values": {
                    "author.name": "Ada",
                    "decision.date": "2026-09-24",
                    "decision.approved": True,
                    "finding.count": 1,
                    "findings": [{"title": "Reviewed", "score": 73}],
                },
            }
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json=payload,
                    headers={"X-CSRF-Token": "csrf", "If-Match": current.etag},
                ).status_code
                == 428
            )
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "csrf",
                        "Idempotency-Key": "missing-etag",
                    },
                ).status_code
                == 428
            )
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": current.etag,
                        "Idempotency-Key": "bad key",
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json={**payload, "author_ids": [str(UUID(int=999))]},
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": current.etag,
                        "Idempotency-Key": "unavailable-author",
                    },
                ).status_code
                == 412
            )
            with Session(engine) as database, database.begin():
                version = database.get(
                    TypedTemplateVersionRow, template["active_version_id"]
                )
                assert version is not None
                version.schema_version = 2
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": current.etag,
                        "Idempotency-Key": "unsupported-version",
                    },
                ).status_code
                == 422
            )
            with Session(engine) as database, database.begin():
                version = database.get(
                    TypedTemplateVersionRow, template["active_version_id"]
                )
                assert version is not None
                version.schema_version = 1
                version.sha256 = "0" * 64
            assert (
                client.post(
                    f"{prefix}/fill-plans",
                    json=payload,
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": current.etag,
                        "Idempotency-Key": "changed-template-digest",
                    },
                ).status_code
                == 503
            )
            with Session(engine) as database, database.begin():
                version = database.get(
                    TypedTemplateVersionRow, template["active_version_id"]
                )
                assert version is not None
                version.sha256 = sha256(_template()).hexdigest()
            created = client.post(
                f"{prefix}/fill-plans",
                json=payload,
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": current.etag,
                    "Idempotency-Key": "valid",
                },
            )
            assert created.status_code == 201, created.text
            plan = created.json()
            assert (
                client.post(
                    f"{prefix}/fill-plans/{plan['id']}/publish",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": plan["etag"],
                        "Idempotency-Key": "premature",
                    },
                ).status_code
                == 412
            )
            assert (
                client.patch(
                    f"{prefix}/fill-plans/{plan['id']}",
                    json={"values": payload["values"], "provenance": {}},
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": plan["etag"],
                        "Idempotency-Key": "incomplete",
                    },
                ).status_code
                == 422
            )
            assert (
                client.post(
                    f"{prefix}/revisions/{source['id']}/regenerations",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": composer.get_draft(owner.id, current.id).etag,
                        "Idempotency-Key": "no-frozen-input",
                    },
                ).status_code
                == 422
            )
            assert (
                str(composer.get_draft(owner.id, current.id).current_revision_id)
                == (source["id"])
            )
            approved = client.post(
                f"{prefix}/fill-plans/{plan['id']}/approve",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": plan["etag"],
                    "Idempotency-Key": "approve-complete",
                },
            )
            assert approved.status_code == 200, approved.text
            publication_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": approved.json()["etag"],
                "Idempotency-Key": "publish-complete",
            }
            published = client.post(
                f"{prefix}/fill-plans/{plan['id']}/publish",
                headers=publication_headers,
            )
            assert published.status_code == 201, published.text
            replayed = client.post(
                f"{prefix}/fill-plans/{plan['id']}/publish",
                headers=publication_headers,
            )
            assert replayed.status_code == 201, replayed.text
            assert replayed.json()["id"] == published.json()["id"]
            assert (
                client.post(
                    f"{prefix}/fill-plans/{plan['id']}/publish",
                    headers={**publication_headers, "If-Match": '"0"'},
                ).status_code
                == 412
            )
            assert (
                client.post(
                    f"{prefix}/fill-plans/{plan['id']}/publish",
                    headers={**publication_headers, "Idempotency-Key": "other-key"},
                ).status_code
                == 412
            )
    finally:
        engine.dispose()
