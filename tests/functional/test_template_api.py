"""Functional ASGI coverage for the versioned template HTTP contract."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from md_converter.app import create_app
from md_converter.config import Settings
from tests.unit.test_template_validation import _docx

pytestmark = pytest.mark.functional


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            Settings(
                initial_admin_username="Admin",
                initial_admin_password="admin-" + "password",
                argon2_memory_cost=8,
                argon2_time_cost=1,
                storage_profile="standalone",
                standalone_data_directory=tmp_path,
                conversion_upload_max_bytes=1_000_000,
                conversion_request_max_bytes=1_100_000,
                conversion_retry_after_seconds=1,
                job_result_retention_seconds=3_600,
            )
        ),
        base_url="https://testserver",
    )


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_template_http_lifecycle_downloads_etags_and_authorization(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as admin:
        admin_csrf = _login(admin, "admin", "admin-password")
        created_user = admin.post(
            "/api/v1/admin/users",
            headers={"X-CSRF-Token": admin_csrf},
            json={"username": "Alice", "password": "alice-password"},
        )
        assert created_user.status_code == 201

        created = admin.post(
            "/api/v1/templates",
            headers={"X-CSRF-Token": admin_csrf},
            data={"name": "Finance", "description": "Quarterly"},
            files={
                "content": ("hostile-name.docx", _docx(), "application/octet-stream")
            },
        )
        assert created.status_code == 201
        template_id = created.json()["id"]
        first_version = created.json()["current_version_id"]
        etag = created.headers["etag"]

        listing = admin.get("/api/v1/templates?name=FINANCE")
        assert listing.json()["total"] == 1
        downloaded = admin.get(f"/api/v1/templates/{template_id}/content")
        assert downloaded.content == _docx()
        assert "hostile-name" not in downloaded.headers["content-disposition"]
        assert downloaded.headers["x-content-type-options"] == "nosniff"

        missing = admin.patch(
            f"/api/v1/templates/{template_id}",
            headers={"X-CSRF-Token": admin_csrf},
            json={"name": "Renamed", "description": "Updated"},
        )
        assert missing.status_code == 428
        renamed = admin.patch(
            f"/api/v1/templates/{template_id}",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": etag},
            json={"name": "Renamed", "description": "Updated"},
        )
        assert renamed.status_code == 200
        stale = admin.patch(
            f"/api/v1/templates/{template_id}",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": etag},
            json={"name": "Lost", "description": "Race"},
        )
        assert stale.status_code == 412

        replaced = admin.put(
            f"/api/v1/templates/{template_id}/content",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": renamed.headers["etag"]},
            files={
                "content": ("replacement.docx", _docx(), "application/octet-stream")
            },
        )
        assert replaced.status_code == 201
        versions = admin.get(f"/api/v1/templates/{template_id}/versions").json()
        assert [item["number"] for item in versions] == [2, 1]
        assert (
            admin.get(
                f"/api/v1/templates/{template_id}/versions/{first_version}/content"
            ).content
            == _docx()
        )

        restored = admin.post(
            f"/api/v1/templates/{template_id}/versions/{first_version}/restore",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": replaced.headers["etag"]},
        )
        assert restored.status_code == 201
        assert restored.json()["restored_from_version_id"] == first_version

        with _client(tmp_path) as alice:
            alice_csrf = _login(alice, "alice", "alice-password")
            assert (
                alice.get(f"/api/v1/templates/{template_id}/content").status_code == 200
            )
            forbidden = alice.post(
                f"/api/v1/templates/{template_id}/archive",
                headers={
                    "X-CSRF-Token": alice_csrf,
                    "If-Match": restored.headers["etag"],
                },
            )
            assert forbidden.status_code == 403

        archived = admin.post(
            f"/api/v1/templates/{template_id}/archive",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": restored.headers["etag"]},
        )
        assert archived.status_code == 200
        deleted = admin.delete(
            f"/api/v1/templates/{template_id}",
            headers={"X-CSRF-Token": admin_csrf, "If-Match": archived.headers["etag"]},
        )
        assert deleted.status_code == 204
        assert admin.get(f"/api/v1/templates/{template_id}").status_code == 404


def test_invalid_template_is_sanitized_and_never_published(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        csrf = _login(client, "admin", "admin-password")
        response = client.post(
            "/api/v1/templates",
            headers={"X-CSRF-Token": csrf},
            data={"name": "Unsafe", "description": "Rejected"},
            files={
                "content": ("secret.docx", b"not-a-docx", "application/octet-stream")
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "TEMPLATE_INVALID_PACKAGE"
        assert "secret.docx" not in response.text
        blank = client.post(
            "/api/v1/templates",
            headers={"X-CSRF-Token": csrf},
            data={"name": "   ", "description": "Rejected"},
            files={"content": ("safe.docx", _docx(), "application/octet-stream")},
        )
        assert blank.status_code == 422
        assert blank.json()["error"]["code"] == "TEMPLATE_REQUEST_INVALID"
        assert client.get("/api/v1/templates").json()["total"] == 0
