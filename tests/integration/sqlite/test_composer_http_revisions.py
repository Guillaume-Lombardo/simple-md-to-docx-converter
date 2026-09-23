"""Exercise exact Composer revision HTTP bytes against SQLite and filesystem storage."""

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.settings import template_settings

pytestmark = pytest.mark.integration


def test_exact_revision_capture_download_restore_and_owner_fence(  # noqa: PLR0915 - one workflow
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    store = SqlComposerRepository(engine, FilesystemObjectStore(tmp_path))
    owner = User(UUID(int=1), "owner", "owner", "hash", Role.USER)
    other = User(UUID(int=2), "other", "other", "hash", Role.USER)
    with Session(engine) as database, database.begin():
        for candidate in (owner, other):
            database.add(
                UserRow(
                    id=str(candidate.id),
                    username=candidate.username,
                    normalized_username=candidate.normalized_username,
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role=candidate.role.value,
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
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
        composer_upload_max_bytes=2048,
        composer_http_request_max_bytes=4096,
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_store=store,
        ),
    )
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"# Source\n",
            "clean-source",
            title="Source",
            content="# Human draft\n",
            media_type="text/markdown",
        )
        with TestClient(app, base_url="https://testserver") as client:
            path = f"/api/v1/composer/drafts/{draft.id}/revisions"
            headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": draft.etag,
                "Idempotency-Key": "source-1",
            }
            created = client.post(f"{path}/from-source", headers=headers)
            assert created.status_code == 201
            body = created.json()
            assert body["approved_values"] == '{"content": "# Source\\n"}'
            assert body["source_sha256"] == draft.source.sha256
            revision_id = body["id"]
            assert client.post(f"{path}/from-source", headers=headers).json() == body
            assert (
                client.get(f"{path}/{revision_id}/artifacts/preview").content
                == b"# Source\n"
            )
            download = client.get(f"{path}/{revision_id}/artifacts/download")
            assert download.content == b"# Source\n"
            assert download.headers["X-Composer-Revision"] == revision_id
            assert download.headers["Cache-Control"] == "private, no-store"
            stale = client.post(
                f"{path}/{revision_id}/restore",
                headers={**headers, "Idempotency-Key": "restore-1"},
            )
            assert stale.status_code == 412
            restored = client.post(
                f"{path}/{revision_id}/restore",
                headers={
                    **headers,
                    "If-Match": created.headers["ETag"],
                    "Idempotency-Key": "restore-1",
                },
            )
            assert restored.status_code == 201
            assert restored.json()["restored_from_revision_id"] == revision_id
            assert restored.json()["number"] == 2
            listed = client.get(path).json()
            assert listed["revisions"][0]["id"] == revision_id
            assert "approved_values" not in listed["revisions"][0]
            assert (
                "content"
                not in client.get("/api/v1/composer/drafts").json()["drafts"][0]
            )
            message_path = f"/api/v1/composer/drafts/{draft.id}/messages"
            message_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": restored.headers["ETag"],
                "Idempotency-Key": "message-1",
            }
            message = client.post(
                message_path,
                json={"content": "Question"},
                headers=message_headers,
            )
            assert message.status_code == 201
            assert (
                client.post(
                    message_path,
                    json={"content": "Question"},
                    headers=message_headers,
                ).json()
                == message.json()
            )
            assert (
                client.post(
                    message_path,
                    json={"content": "Changed"},
                    headers=message_headers,
                ).status_code
                == 412
            )
            authentication.authenticate.return_value = other
            assert client.get(f"{path}/{revision_id}").status_code == 404
            assert (
                client.get(f"{path}/{revision_id}/artifacts/download").status_code
                == 404
            )
    finally:
        engine.dispose()
