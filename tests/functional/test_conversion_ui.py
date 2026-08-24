"""Authenticated conversion UI over real SQLite and filesystem boundaries."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from md_converter.app import create_app
from md_converter.config import Settings
from md_converter.persistence.sql import create_database_engine, standalone_database_url
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)
from tests.settings import template_settings


def _publish_template(data_directory: Path, owner_id: UUID) -> TemplateIdentity:
    engine = create_database_engine(standalone_database_url(data_directory))
    catalog = SqlTemplateCatalogRepository(engine)
    template_id, version_id, token = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    template = TemplateIdentity(
        template_id,
        owner_id,
        "Accessible default",
        "Shared conversion template",
        TemplateStatus.ACTIVE,
        current_version_id=version_id,
    )
    version = TemplateVersion(
        version_id,
        template_id,
        1,
        owner_id,
        "0" * 64,
        1,
        now,
        owner_id,
        publication_state=TemplatePublicationState.PENDING,
        publication_token=token,
        publication_lease_expires_at=now,
    )
    catalog.reserve_create(template, version)
    catalog.finalize_version(
        template_id,
        expected_revision=1,
        version_id=version_id,
        publication_token=token,
        audit=TemplateAuditRecord(
            uuid4(), owner_id, owner_id, template_id, "create", version_id, False, now
        ),
    )
    SqlTemplateSelectionRepository(engine).set_system_fallback(template_id)
    engine.dispose()
    return template


@pytest.mark.functional
def test_browser_conversion_shell_uses_real_session_template_and_job_boundaries(
    tmp_path: Path,
) -> None:
    password = "admin-" + "password"
    app = create_app(
        Settings(
            **template_settings(),
            initial_admin_username="admin",
            initial_admin_password=password,
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="standalone",
            standalone_data_directory=tmp_path,
            conversion_upload_max_bytes=128,
            conversion_request_max_bytes=2_000,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=3_600,
        )
    )
    with TestClient(app, base_url="https://testserver") as client:
        assert (
            client.get("/convert", follow_redirects=False).headers["location"]
            == "/login"
        )
        login = client.post(
            "/login",
            headers={"Origin": "https://testserver"},
            data={"username": "admin", "password": password},
            follow_redirects=False,
        )
        assert login.status_code == 303
        session = client.get("/api/v1/session").json()
        template = _publish_template(tmp_path, UUID(session["id"]))

        page = client.get("/convert")
        assert page.status_code == 200
        assert "System fallback template" in page.text
        assert "Accessible default" in page.text
        assert str(template.current_version_id) in page.text
        assert "Templates" in page.text
        assert client.get("/static/conversion.js").status_code == 200
        assert client.get("/static/conversion.css").status_code == 200

        search = client.get(
            "/api/v1/templates", params={"status": "active", "name": "default"}
        )
        assert search.status_code == 200
        assert search.json()["items"][0]["id"] == str(template.id)

        csrf = client.cookies.get("__Host-md_converter_csrf")
        assert csrf is not None
        created = client.post(
            "/api/v1/conversions",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": "browser-request"},
            files={"source": ("source.md", b"# Browser conversion", "text/markdown")},
            data={
                "template_id": str(template.id),
                "template_version_id": str(template.current_version_id),
                "output": "both",
            },
        )
        assert created.status_code == 202
        job_id = created.json()["id"]
        assert client.get(f"/api/v1/conversions/{job_id}").json()["state"] == "queued"

        recent = client.get("/convert")
        assert job_id in recent.text
        cancelled = client.delete(
            f"/api/v1/conversions/{job_id}", headers={"X-CSRF-Token": csrf}
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == "cancelled"
