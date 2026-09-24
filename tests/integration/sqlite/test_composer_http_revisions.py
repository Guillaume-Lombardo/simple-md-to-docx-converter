"""Exercise exact Composer revision HTTP bytes against SQLite and filesystem storage."""

from hashlib import sha256
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
from markweave.composer.revisions import (
    ArtifactContent,
    ComposerConflictError,
    RevisionSnapshot,
)
from markweave.config import Settings
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


@pytest.mark.parametrize("archive_source", [False, True])
def test_publish_current_saved_markdown_freezes_exact_bytes_and_zip_assets(  # noqa: PLR0915 - complete HTTP publication flow
    tmp_path: Path, mocker: MockerFixture, archive_source: bool
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    store = SqlComposerRepository(engine, FilesystemObjectStore(tmp_path))
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
    source = b"# Original source\n"
    if archive_source:
        archive = BytesIO()
        with ZipFile(archive, "w") as package:
            package.writestr("main.md", source)
            package.writestr("assets/chart.png", b"\x89PNG\r\n\x1a\n")
        source = archive.getvalue()
    try:
        draft = store.create_draft_with_source(
            owner.id,
            source,
            "scanner-approved",
            title="Draft",
            content="# Original source\n",
            media_type="application/zip" if archive_source else "text/markdown",
        )
        with TestClient(app, base_url="https://testserver") as client:
            path = f"/api/v1/composer/drafts/{draft.id}/revisions"
            if not archive_source:
                captured = client.post(
                    f"{path}/from-source",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": draft.etag,
                        "Idempotency-Key": "source-1",
                    },
                )
                assert captured.status_code == 201
                assert (
                    client.get(
                        f"{path}/{captured.json()['id']}/artifacts/download"
                    ).content
                    == source
                )
            current = store.get_draft(owner.id, draft.id)
            edited = store.save_draft(
                owner.id,
                draft.id,
                if_match=current.etag,
                title="Draft",
                content="# Approved edit\n\nExact body.\n",
            )
            headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": edited.etag,
                "Idempotency-Key": "direct-1",
            }
            published = client.post(f"{path}/from-draft", headers=headers)
            assert published.status_code == 201
            revision = published.json()
            assert revision["operation"] == "publish_draft"
            assert revision["approved_values"] == (
                '{"content": "# Approved edit\\n\\nExact body.\\n"}'
            )
            assert revision["source_sha256"] == draft.source.sha256
            for kind in ("download", "preview"):
                artifact = client.get(f"{path}/{revision['id']}/artifacts/{kind}")
                assert artifact.status_code == 200
                assert artifact.content == b"# Approved edit\n\nExact body.\n"
                assert artifact.headers["X-Composer-Revision"] == revision["id"]
            assert client.post(f"{path}/from-draft", headers=headers).json() == revision
            if archive_source:
                asset = client.get(f"{path}/{revision['id']}/artifacts/source")
                assert asset.status_code == 200
                assert asset.content == source
                assert asset.headers["Content-Disposition"].endswith('.zip"')
            else:
                assert (
                    client.get(f"{path}/{revision['id']}/artifacts/source").status_code
                    == 404
                )
            assert (
                client.post(
                    f"{path}/from-draft",
                    headers={**headers, "Idempotency-Key": "stale-new-key"},
                ).status_code
                == 412
            )
            forged_artifacts = (
                (ArtifactContent("source", "application/zip", source),)
                if archive_source
                else ()
            ) + (
                ArtifactContent("download", "text/markdown", b"# Forged\n"),
                ArtifactContent("preview", "text/markdown", b"# Forged\n"),
            )
            latest = store.get_draft(owner.id, draft.id)
            with pytest.raises(ComposerConflictError, match="Current Markdown draft"):
                store.publish_revision(
                    owner.id,
                    draft.id,
                    actor_id=owner.id,
                    if_match=latest.etag,
                    idempotency_key="forged-current-content",
                    snapshot=RevisionSnapshot(
                        source=draft.source,
                        template_reference=None,
                        approved_values=revision["approved_values"],
                        render_options="{}",
                        model_identity=None,
                        provenance="human:direct",
                        operation="publish_draft",
                    ),
                    artifacts=forged_artifacts,
                )
    finally:
        engine.dispose()


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
            current = store.get_draft(owner.id, draft.id)
            proposal = store.create_proposal(
                owner.id,
                draft.id,
                base_version=current.version,
                proposed_value="# Revised source\n",
                provenance="manual-suggestion",
            )
            proposal_path = (
                f"/api/v1/composer/drafts/{draft.id}/proposals/{proposal.id}"
            )
            approved = client.post(
                f"{proposal_path}/decision",
                json={"state": "edited", "decided_value": "# Human correction\n"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": store.get_draft(owner.id, draft.id).etag,
                },
            )
            assert approved.status_code == 200
            assert approved.json()["decided_value"] == "# Human correction\n"
            publication_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": store.get_draft(owner.id, draft.id).etag,
                "Idempotency-Key": "proposal-1",
            }
            with pytest.raises(ComposerConflictError):
                store.publish_revision(
                    owner.id,
                    draft.id,
                    actor_id=owner.id,
                    if_match=publication_headers["If-Match"],
                    idempotency_key="tampered-proposal",
                    snapshot=RevisionSnapshot(
                        draft.source,
                        None,
                        '{"content": "# Human correction\\n"}',
                        "{}",
                        None,
                        "human:edited:manual-suggestion",
                        f"publish_proposal:{proposal.id}",
                    ),
                    artifacts=(
                        ArtifactContent("download", "text/markdown", b"# Tampered\n"),
                        ArtifactContent("preview", "text/markdown", b"# Tampered\n"),
                    ),
                    approved_proposal_id=proposal.id,
                )
            published = client.post(
                f"{proposal_path}/publish", headers=publication_headers
            )
            assert published.status_code == 201
            assert published.json()["number"] == 3
            assert store.get_draft(owner.id, draft.id).content == "# Human correction\n"
            assert (
                client.post(
                    f"{proposal_path}/publish", headers=publication_headers
                ).json()
                == published.json()
            )
            new_revision_id = published.json()["id"]
            assert (
                client.get(f"{path}/{new_revision_id}/artifacts/download").content
                == b"# Human correction\n"
            )
            diff = client.get(
                f"{path}/{new_revision_id}/diff",
                params={"from_revision_id": revision_id},
            )
            assert diff.status_code == 200
            assert diff.json()["status"] == "available"
            assert diff.json()["changes"][0]["before_text"] == "# Source\n"
            assert diff.json()["changes"][0]["after_text"] == ("# Human correction\n")
            assert (
                client.get(
                    f"{path}/{new_revision_id}/diff",
                    params={"from_revision_id": new_revision_id},
                ).json()["status"]
                == "unchanged"
            )
            archive = BytesIO()
            with ZipFile(archive, "w") as package:
                package.writestr("document.md", "# Package source\n")
            archive_data = archive.getvalue()
            package_draft = store.create_draft_with_source(
                owner.id,
                archive_data,
                "clean-package",
                title="Package",
                content="# Package source\n",
                media_type="application/zip",
            )
            package_path = f"/api/v1/composer/drafts/{package_draft.id}/revisions"
            package_revision = client.post(
                f"{package_path}/from-source",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": package_draft.etag,
                    "Idempotency-Key": "package-source-1",
                },
            )
            assert package_revision.status_code == 201
            package_id = package_revision.json()["id"]
            assert (
                client.get(f"{package_path}/{package_id}/artifacts/download").content
                == archive_data
            )
            assert (
                client.get(f"{package_path}/{package_id}/artifacts/preview").content
                == b"# Package source\n"
            )
            reverse_archive = BytesIO()
            with ZipFile(reverse_archive, "w") as package:
                package.writestr("document.md", "# Reverse source\n")
                package.writestr("manifest.json", "{}")
            reverse_data = reverse_archive.getvalue()
            reverse_draft = store.create_draft_with_source(
                owner.id,
                reverse_data,
                "clean-reverse-package",
                title="Reverse package",
                content="# Reverse source\n",
                media_type="application/zip",
                source_kind="reversion_result",
                origin_job_id=UUID(int=40),
                origin_result_object_id=UUID(int=41),
                origin_result_sha256=sha256(reverse_data).hexdigest(),
            )
            reverse_path = f"/api/v1/composer/drafts/{reverse_draft.id}/revisions"
            reverse_revision = client.post(
                f"{reverse_path}/from-source",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": reverse_draft.etag,
                    "Idempotency-Key": "reverse-source-1",
                },
            )
            assert reverse_revision.status_code == 201
            reverse_id = reverse_revision.json()["id"]
            assert (
                client.get(f"{reverse_path}/{reverse_id}/artifacts/download").content
                == reverse_data
            )
            assert (
                client.get(f"{reverse_path}/{reverse_id}/artifacts/preview").content
                == b"# Reverse source\n"
            )
            before_rejection = store.get_draft(owner.id, draft.id)
            rejected_proposal = store.create_proposal(
                owner.id,
                draft.id,
                base_version=before_rejection.version,
                proposed_value="# Unsupported claim\n",
                provenance="manual-suggestion",
            )
            rejected_path = (
                f"/api/v1/composer/drafts/{draft.id}/proposals/{rejected_proposal.id}"
            )
            rejected = client.post(
                f"{rejected_path}/decision",
                json={"state": "rejected"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": store.get_draft(owner.id, draft.id).etag,
                },
            )
            assert rejected.status_code == 200
            assert (
                client.post(
                    f"{rejected_path}/publish",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": store.get_draft(owner.id, draft.id).etag,
                        "Idempotency-Key": "rejected-1",
                    },
                ).status_code
                == 422
            )
            assert store.get_draft(owner.id, draft.id).current_revision_id == UUID(
                new_revision_id
            )
            authentication.authenticate.return_value = other
            assert client.get(f"{path}/{revision_id}").status_code == 404
            assert (
                client.get(f"{path}/{revision_id}/artifacts/download").status_code
                == 404
            )
            assert (
                client.get(
                    f"{path}/{new_revision_id}/diff",
                    params={"from_revision_id": revision_id},
                ).status_code
                == 404
            )
    finally:
        engine.dispose()
