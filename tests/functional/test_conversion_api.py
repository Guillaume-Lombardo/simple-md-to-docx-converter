"""Functional conversion API coverage over real SQLite and filesystem storage."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from md_converter.app import create_app
from md_converter.config import Settings
from md_converter.jobs.models import JobState
from md_converter.malware import TrustingUploadScanner
from md_converter.persistence.jobs import SqlJobRepository
from md_converter.persistence.sql import create_database_engine, standalone_database_url
from md_converter.persistence.templates import SqlTemplateCatalogRepository
from md_converter.storage import ObjectKey, ObjectScope
from md_converter.templates.models import (
    TemplateAuditRecord,
    TemplateIdentity,
    TemplatePublicationState,
    TemplateStatus,
    TemplateVersion,
)
from tests.settings import template_settings


def login(client: TestClient, username: str, password: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()


def submit(  # noqa: PLR0913 - explicit HTTP form helper
    client: TestClient,
    csrf_token: str,
    *,
    content: bytes = b"# Durable conversion",
    idempotency_key: str | None = None,
    correlation_id: str | None = None,
    template_id: UUID,
    template_version_id: UUID,
) -> Any:
    headers = {"X-CSRF-Token": csrf_token}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    if correlation_id is not None:
        headers["X-Correlation-ID"] = correlation_id
    return client.post(
        "/api/v1/conversions",
        headers=headers,
        files={"source": ("source.md", content, "text/markdown")},
        data={
            "template_id": str(template_id),
            "template_version_id": str(template_version_id),
            "output": "docx",
        },
    )


@pytest.mark.functional
def test_conversion_api_idempotency_authorization_cancellation_and_result(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
    admin_password = "admin-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=admin_password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=64,
        conversion_request_max_bytes=1_024,
        conversion_retry_after_seconds=2,
        job_result_retention_seconds=3_600,
    )
    app = create_app(settings, scanner=TrustingUploadScanner())
    with TestClient(app, base_url="https://testserver") as client:
        admin = login(client, "admin", admin_password)
        csrf = str(admin["csrf_token"])
        template_id, template_version_id = uuid4(), uuid4()
        owner_id = UUID(admin["user"]["id"])
        now = datetime.now(UTC)
        catalog = SqlTemplateCatalogRepository(
            create_database_engine(standalone_database_url(tmp_path))
        )
        template = TemplateIdentity(
            template_id,
            owner_id,
            "Job template",
            "Visible",
            TemplateStatus.ACTIVE,
            current_version_id=template_version_id,
        )
        publication_token = uuid4()
        version = TemplateVersion(
            template_version_id,
            template_id,
            1,
            owner_id,
            "0" * 64,
            1,
            now,
            owner_id,
            declared_fonts=("Calibri",),
            resolved_fonts=(("Calibri", "Carlito"),),
            validation_trace=("static_ooxml",),
            publication_state=TemplatePublicationState.PENDING,
            publication_token=publication_token,
            publication_lease_expires_at=now,
        )
        catalog.reserve_create(template, version)
        catalog.finalize_version(
            template_id,
            expected_revision=1,
            version_id=template_version_id,
            publication_token=publication_token,
            audit=TemplateAuditRecord(
                uuid4(),
                owner_id,
                owner_id,
                template_id,
                "create",
                template_version_id,
                False,
                now,
            ),
        )
        arbitrary = submit(
            client,
            csrf,
            template_id=uuid4(),
            template_version_id=uuid4(),
        )
        assert arbitrary.status_code == 422
        assert arbitrary.json()["error"]["code"] == "CONVERSION_REQUEST_INVALID"
        first = submit(
            client,
            csrf,
            idempotency_key="stable-request",
            correlation_id="edge-request-42",
            template_id=template_id,
            template_version_id=template_version_id,
        )
        assert first.status_code == 202
        assert first.headers["Location"].endswith(first.json()["id"])
        assert first.headers["Retry-After"] == "2"
        assert first.headers["X-Correlation-ID"] == "edge-request-42"
        assert first.json()["correlation_id"] == "edge-request-42"
        assert first.json()["component_versions"]
        assert first.json()["expires_at"] is None
        first_id = UUID(first.json()["id"])
        persisted = SqlJobRepository(
            create_database_engine(standalone_database_url(tmp_path))
        ).get(first_id)
        assert persisted is not None
        assert persisted.correlation_id == "edge-request-42"
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "md_converter_queue_depth 1" in metrics.text
        audits = client.get("/api/v1/audit")
        assert audits.status_code == 200
        assert audits.json()[0] == {
            "id": str(audits.json()[0]["id"]),
            "actor_id": str(owner_id),
            "owner_id": str(owner_id),
            "operation": "create",
            "target_id": str(template_id),
            "target_type": "template",
            "target_version": str(template_version_id),
            "version_id": str(template_version_id),
            "administrator_intervention": False,
            "created_at": audits.json()[0]["created_at"],
        }

        replay = client.post(
            "/api/v1/conversions",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "stable-request",
            },
            files={"source": ("other-name.md", b"# Durable conversion")},
            data={
                "template_id": first.json()["template_id"],
                "template_version_id": first.json()["template_version_id"],
                "output": "docx",
            },
        )
        assert replay.status_code == 202
        assert replay.json()["id"] == str(first_id)

        conflict = client.post(
            "/api/v1/conversions",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "stable-request",
            },
            files={"source": ("source.md", b"different")},
            data={
                "template_id": first.json()["template_id"],
                "template_version_id": first.json()["template_version_id"],
                "output": "docx",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "CONVERSION_CONFLICT"

        assert client.get("/api/v1/conversions").json()["total"] == 1
        assert client.get(f"/api/v1/conversions/{first_id}").status_code == 200

        created_user = client.post(
            "/api/v1/admin/users",
            headers={"X-CSRF-Token": csrf},
            json={"username": "Alice", "password": "alice-password"},
        )
        assert created_user.status_code == 201
        login(client, "alice", "alice-password")
        assert client.get(f"/api/v1/conversions/{first_id}").status_code == 404
        assert client.get("/api/v1/audit").status_code == 403

        hostile = client.get(
            "/health/live", headers={"X-Correlation-ID": "../../private.md"}
        )
        assert hostile.headers["X-Correlation-ID"] != "../../private.md"

        admin = login(client, "admin", admin_password)
        csrf = str(admin["csrf_token"])
        cancelled = client.delete(
            f"/api/v1/conversions/{first_id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["state"] == JobState.CANCELLED.value

        successful = submit(
            client,
            csrf,
            template_id=template_id,
            template_version_id=template_version_id,
        )
        assert successful.status_code == 202
        successful_id = UUID(successful.json()["id"])
        owner_id = UUID(successful.json()["owner_id"])
        repository = SqlJobRepository(
            create_database_engine(standalone_database_url(tmp_path))
        )
        now = datetime.now(UTC)
        claimed = repository.claim(
            "functional-worker", now, now + timedelta(seconds=30)
        )
        assert claimed is not None and claimed.id == successful_id
        assert claimed.lease_token is not None
        result_id = uuid4()
        app.state.components.object_store.put(
            ObjectKey(ObjectScope.RESULT, owner_id, result_id), b"docx-result"
        )
        repository.succeed(
            successful_id,
            "functional-worker",
            claimed.lease_token,
            result_id,
            now,
            now + timedelta(hours=1),
        )
        result = client.get(f"/api/v1/conversions/{successful_id}/result")
        assert result.status_code == 200
        assert result.content == b"docx-result"
        assert (
            f"conversion-{successful_id}.docx" in result.headers["Content-Disposition"]
        )

        assert (
            submit(
                client,
                csrf,
                content=b"",
                template_id=template_id,
                template_version_id=template_version_id,
            ).status_code
            == 422
        )
        assert (
            submit(
                client,
                csrf,
                content=b"x" * 65,
                template_id=template_id,
                template_version_id=template_version_id,
            ).status_code
            == 422
        )
