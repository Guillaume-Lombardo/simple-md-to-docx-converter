"""HTTP coverage for private author and typed-template directories."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from markweave.auth.models import Role, User
from markweave.persistence.schema import UserRow
from tests.integration.composer.test_typed_docx_fill import (
    _SOURCE,
    _parts,
    _repack,
    _schema,
    _template,
)
from tests.integration.sqlite.test_composer_t91_http import _client

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_AUTHORS = "/api/v1/composer/authors"
_TEMPLATES = "/api/v1/composer/fill-templates"
_CSRF = {"X-CSRF-Token": "csrf"}
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _add_user(engine: Engine, identity: int, *, active: bool = True) -> User:
    user = User(
        UUID(int=identity), f"user-{identity}", f"user-{identity}", "hash", Role.USER
    )
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(user.id),
                username=user.username,
                normalized_username=user.normalized_username,
                password_hash="hash",  # noqa: S106 - isolated fixture
                role=user.role.value,
                active=active,
                auth_version=0,
                password_change_required=False,
            )
        )
    return user


def _upload(
    client: TestClient,
    *,
    schema: str | None = None,
    content: bytes | None = None,
    path: str = _TEMPLATES,
    etag: str | None = None,
):
    headers = dict(_CSRF)
    if etag is not None:
        headers["If-Match"] = etag
    return client.post(
        path,
        data={
            "name": "Decision",
            "schema": json.dumps(_schema()) if schema is None else schema,
        }
        if path == _TEMPLATES
        else {"schema": json.dumps(_schema()) if schema is None else schema},
        files={
            "file": (
                "decision.docx",
                _template() if content is None else content,
                _DOCX_MEDIA,
            )
        },
        headers=headers,
    )


def test_author_directory_pagination_etags_and_named_access(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, _composer, _scanner, _owner, _authors, *_ = _client(
        tmp_path, mocker
    )
    reader = _add_user(engine, 2)
    inactive = _add_user(engine, 3, active=False)
    auth = client.app.state.components.authentication
    try:
        with client:
            first = client.post(
                _AUTHORS,
                json={"name": "Ada", "fields": {}},
                headers=_CSRF,
            )
            second = client.post(
                _AUTHORS,
                json={"name": "Grace", "fields": {}},
                headers=_CSRF,
            )
            assert first.status_code == second.status_code == 201
            path = f"{_AUTHORS}/{first.json()['id']}"
            listed = client.get(_AUTHORS, params={"limit": 1, "offset": 1})
            assert listed.status_code == 200
            assert [item["name"] for item in listed.json()["authors"]] == ["Grace"]
            assert listed.json()["limit"] == listed.json()["offset"] == 1
            assert listed.headers["Cache-Control"] == "private, no-store"
            assert client.get(_AUTHORS, params={"limit": 0}).status_code == 422
            assert client.get(path).headers["ETag"] == first.headers["ETag"]

            assert (
                client.patch(
                    path,
                    json={"name": "Ada II", "fields": {}},
                    headers=_CSRF,
                ).status_code
                == 428
            )
            changed = client.patch(
                path,
                json={"name": "Ada II", "fields": {}},
                headers={**_CSRF, "If-Match": first.headers["ETag"]},
            )
            assert changed.status_code == 200
            assert changed.json()["version"] == 2
            assert (
                client.patch(
                    path,
                    json={"name": "stale", "fields": {}},
                    headers={**_CSRF, "If-Match": first.headers["ETag"]},
                ).status_code
                == 412
            )
            grant_path = f"{path}/grants/{reader.id}"
            assert client.put(grant_path, headers=_CSRF).status_code == 428
            assert (
                client.put(
                    f"{path}/grants/{inactive.id}",
                    headers={**_CSRF, "If-Match": changed.headers["ETag"]},
                ).status_code
                == 404
            )
            granted = client.put(
                grant_path, headers={**_CSRF, "If-Match": changed.headers["ETag"]}
            )
            assert granted.status_code == 200
            assert granted.json()["shared_with"] == [str(reader.id)]
            assert (
                client.put(
                    grant_path, headers={**_CSRF, "If-Match": changed.headers["ETag"]}
                ).status_code
                == 412
            )

            auth.authenticate.return_value = reader
            visible = client.get(path)
            assert visible.status_code == 200
            assert visible.json()["shared_with"] == []
            assert [
                entry["id"] for entry in client.get(_AUTHORS).json()["authors"]
            ] == [first.json()["id"]]
            assert (
                client.patch(
                    path,
                    json={"name": "Reader edit", "fields": {}},
                    headers={**_CSRF, "If-Match": granted.headers["ETag"]},
                ).status_code
                == 404
            )
            assert (
                client.delete(
                    grant_path,
                    headers={**_CSRF, "If-Match": granted.headers["ETag"]},
                ).status_code
                == 404
            )

            auth.authenticate.return_value = inactive
            assert client.get(path).status_code == 404
            assert client.get(_AUTHORS).json()["authors"] == []
            auth.authenticate.return_value = _owner
            revoked = client.delete(
                grant_path, headers={**_CSRF, "If-Match": granted.headers["ETag"]}
            )
            assert revoked.status_code == 200
            assert revoked.json()["shared_with"] == []
            assert (
                client.delete(
                    grant_path, headers={**_CSRF, "If-Match": granted.headers["ETag"]}
                ).status_code
                == 412
            )
            auth.authenticate.return_value = reader
            assert client.get(path).status_code == 404
    finally:
        engine.dispose()


def test_typed_template_versions_content_grants_and_owner_fence(  # noqa: PLR0915 - full version/grant lifecycle
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, client, _composer, scanner, owner, _authors, *_ = _client(tmp_path, mocker)
    reader = _add_user(engine, 2)
    inactive = _add_user(engine, 3, active=False)
    auth = client.app.state.components.authentication
    original = _template()
    replacement = _repack(
        original,
        {
            "word/document.xml": _parts(original)["word/document.xml"].replace(
                b"PLACEHOLDER", b"REPLACEMENT"
            )
        },
    )
    try:
        with client:
            created = _upload(client, content=original)
            assert created.status_code == 201, created.text
            assert scanner.scan.call_count == 1
            template_id = created.json()["id"]
            first_version = created.json()["active_version_id"]
            path = f"{_TEMPLATES}/{template_id}"
            versions = f"{path}/versions"
            assert client.get(path).headers["ETag"] == created.headers["ETag"]
            assert (
                client.get(_TEMPLATES, params={"limit": 1, "offset": 0}).json()[
                    "templates"
                ][0]["id"]
                == template_id
            )
            assert client.get(_TEMPLATES, params={"limit": 0}).status_code == 422
            assert (
                client.get(versions, params={"limit": 1, "offset": 1}).json()[
                    "versions"
                ]
                == []
            )
            first = client.get(f"{versions}/{first_version}")
            assert first.status_code == 200
            assert first.json()["number"] == 1
            assert first.json()["schema_version"] == 1
            assert first.json()["schema"] == _schema()
            original_download = client.get(f"{versions}/{first_version}/content")
            assert original_download.status_code == 200
            assert original_download.content == original
            assert original_download.headers["X-Content-Type-Options"] == "nosniff"
            assert original_download.headers["Cache-Control"] == "private, no-store"

            assert (
                _upload(client, path=versions, content=replacement).status_code == 428
            )
            changed = _upload(
                client,
                path=versions,
                etag=created.headers["ETag"],
                content=replacement,
            )
            assert changed.status_code == 201, changed.text
            second_version = changed.json()["active_version_id"]
            assert second_version != first_version
            assert (
                _upload(client, path=versions, etag=created.headers["ETag"]).status_code
                == 412
            )
            assert [
                entry["number"] for entry in client.get(versions).json()["versions"]
            ] == [1, 2]
            assert (
                client.get(versions, params={"limit": 1, "offset": 1}).json()[
                    "versions"
                ][0]["id"]
                == second_version
            )
            assert client.get(f"{versions}/{first_version}/content").content == original
            assert (
                client.get(f"{versions}/{second_version}/content").content
                == replacement
            )
            assert client.get(f"{versions}/{UUID(int=99)}").status_code == 404

            grant_path = f"{path}/grants/{reader.id}"
            assert client.put(grant_path, headers=_CSRF).status_code == 428
            assert (
                client.put(
                    f"{path}/grants/{inactive.id}",
                    headers={**_CSRF, "If-Match": changed.headers["ETag"]},
                ).status_code
                == 404
            )
            granted = client.put(
                grant_path, headers={**_CSRF, "If-Match": changed.headers["ETag"]}
            )
            assert granted.status_code == 200
            assert granted.json()["shared_with"] == [str(reader.id)]
            auth.authenticate.return_value = reader
            assert client.get(path).json()["shared_with"] == []
            assert [
                item["id"] for item in client.get(_TEMPLATES).json()["templates"]
            ] == [template_id]
            assert client.get(f"{versions}/{first_version}/content").content == original
            assert (
                _upload(client, path=versions, etag=granted.headers["ETag"]).status_code
                == 404
            )
            assert (
                client.put(
                    grant_path, headers={**_CSRF, "If-Match": granted.headers["ETag"]}
                ).status_code
                == 404
            )

            auth.authenticate.return_value = inactive
            assert client.get(path).status_code == 404
            assert client.get(_TEMPLATES).json()["templates"] == []
            auth.authenticate.return_value = owner
            revoked = client.delete(
                grant_path, headers={**_CSRF, "If-Match": granted.headers["ETag"]}
            )
            assert revoked.status_code == 200
            assert (
                client.delete(
                    grant_path, headers={**_CSRF, "If-Match": granted.headers["ETag"]}
                ).status_code
                == 412
            )
            auth.authenticate.return_value = reader
            assert client.get(path).status_code == 404
            assert client.get(f"{versions}/{second_version}/content").status_code == 404
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("schema", "content", "expected"),
    [
        ("{", None, 422),
        ("[]", None, 422),
        (json.dumps({"fields": [], "repeats": []}), None, 422),
        (None, b"not a ZIP", 422),
        (None, b"", 422),
        (None, _SOURCE.read_bytes(), 422),
    ],
)
def test_typed_upload_rejects_invalid_schema_or_package_before_publication(
    tmp_path: Path,
    mocker: MockerFixture,
    schema: str | None,
    content: bytes | None,
    expected: int,
) -> None:
    engine, client, _composer, scanner, *_ = _client(tmp_path, mocker)
    try:
        with client:
            response = _upload(client, schema=schema, content=content)
            assert response.status_code == expected, response.text
            assert client.get(_TEMPLATES).json()["templates"] == []
            if content != b"":
                scanner.scan.assert_called_once()
            else:
                scanner.scan.assert_not_called()
    finally:
        engine.dispose()
