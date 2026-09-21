"""Functional conversion API coverage over real SQLite and filesystem storage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from markweave.app import create_app
from markweave.config import Settings
from markweave.jobs.models import JobState
from markweave.malware import TrustingUploadScanner
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.sql import managed_database_engine, standalone_database_url
from markweave.persistence.templates import SqlTemplateCatalogRepository
from markweave.storage import ObjectKey, ObjectScope
from markweave.templates.models import (
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
    filename: str = "source.md",
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
        files={"source": (filename, content, "text/markdown")},
        data={
            "template_id": str(template_id),
            "template_version_id": str(template_version_id),
            "output": "docx",
        },
    )


def submit_default(
    client: TestClient, csrf_token: str, *, filename: str, output: str
) -> Any:
    return client.post(
        "/api/v1/conversions",
        headers={"X-CSRF-Token": csrf_token},
        files={"source": (filename, b"# History", "text/markdown")},
        data={"output": output},
    )


@pytest.mark.functional
def test_conversion_history_filters_before_pagination_and_remains_owner_scoped(
    tmp_path: Path,
) -> None:
    admin_password = "admin-" + "password"
    settings = Settings(
        **template_settings(job_active_limit_per_user=10),
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
    with (
        managed_database_engine(standalone_database_url(tmp_path)) as auxiliary_engine,
        TestClient(app, base_url="https://testserver") as client,
    ):
        admin = login(client, "admin", admin_password)
        admin_csrf = str(admin["csrf_token"])
        for username in ("alice", "bob"):
            created = client.post(
                "/api/v1/admin/users",
                headers={"X-CSRF-Token": admin_csrf},
                json={"username": username, "password": f"{username}-password"},
            )
            assert created.status_code == 201

        alice = login(client, "alice", "alice-password")
        alice_id = UUID(alice["user"]["id"])
        alice_csrf = str(alice["csrf_token"])
        document_old = submit_default(
            client, alice_csrf, filename="old.md", output="docx"
        )
        presentation_expired = submit_default(
            client, alice_csrf, filename="expired.md", output="pptx"
        )
        document_new = submit_default(
            client, alice_csrf, filename="new.md", output="pdf"
        )
        presentation_new = submit_default(
            client, alice_csrf, filename="slides.md", output="pptx-bundle"
        )
        assert all(
            response.status_code == 202
            for response in (
                document_old,
                presentation_expired,
                document_new,
                presentation_new,
            )
        )
        repository = SqlJobRepository(auxiliary_engine)
        now = datetime.now(UTC)
        expired_id = UUID(presentation_expired.json()["id"])
        assert repository.request_cancel(expired_id, alice_id, now, now) is not None
        expired = repository.expire_terminal(
            "history-test", now + timedelta(seconds=1), now + timedelta(seconds=31), 10
        )
        assert expired_id in {candidate.job_id for candidate in expired}

        page = client.get(
            "/api/v1/conversions",
            params={
                "output_family": "document",
                "expired": "false",
                "offset": 1,
                "limit": 1,
            },
        )
        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert [item["id"] for item in page.json()["items"]] == [
            document_old.json()["id"]
        ]
        presentation_page = client.get(
            "/api/v1/conversions?output_family=presentation&expired=false&limit=10"
        )
        assert presentation_page.status_code == 200
        assert presentation_page.json()["total"] == 1
        assert [item["id"] for item in presentation_page.json()["items"]] == [
            presentation_new.json()["id"]
        ]
        expired_page = client.get(
            "/api/v1/conversions?output_family=presentation&expired=true"
        )
        assert expired_page.status_code == 200
        assert expired_page.json()["total"] == 1
        assert expired_page.json()["items"][0]["id"] == str(expired_id)
        assert client.get("/api/v1/conversions").json()["total"] == 4
        assert (
            client.get("/api/v1/conversions?output_family=spreadsheet").status_code
            == 422
        )
        assert client.get("/api/v1/conversions?expired=sometimes").status_code == 422

        bob = login(client, "bob", "bob-password")
        bob_job = submit_default(
            client, str(bob["csrf_token"]), filename="bob.md", output="pptx"
        )
        assert bob_job.status_code == 202
        bob_page = client.get(
            "/api/v1/conversions?output_family=presentation&expired=false"
        ).json()
        assert bob_page["total"] == 1
        assert bob_page["items"][0]["id"] == bob_job.json()["id"]

        login(client, "admin", admin_password)
        admin_page = client.get("/api/v1/conversions").json()
        assert admin_page["total"] == 0


@pytest.mark.functional
def test_conversion_api_idempotency_authorization_cancellation_and_result(  # noqa: PLR0915
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
    with (
        managed_database_engine(standalone_database_url(tmp_path)) as auxiliary_engine,
        TestClient(app, base_url="https://testserver") as client,
    ):
        admin = login(client, "admin", admin_password)
        csrf = str(admin["csrf_token"])
        template_id, template_version_id = uuid4(), uuid4()
        owner_id = UUID(admin["user"]["id"])
        now = datetime.now(UTC)
        catalog = SqlTemplateCatalogRepository(auxiliary_engine)
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
        correlation_id = first.headers["X-Correlation-ID"]
        assert UUID(correlation_id).version == 4
        assert correlation_id != "edge-request-42"
        assert first.json()["correlation_id"] == correlation_id
        assert first.json()["component_versions"]
        assert first.json()["expires_at"] is None
        first_id = UUID(first.json()["id"])
        persisted = SqlJobRepository(auxiliary_engine).get(first_id)
        assert persisted is not None
        assert persisted.correlation_id == correlation_id
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

        attacker_values = (
            "private-secret-token",
            "customer-upload.docx",
            "Bearer-private-credential",
            "00000000-0000-4000-8000-000000000001",
            "../../private.md",
        )
        generated_ids = []
        for attacker_value in attacker_values:
            response = client.get(
                "/health/live", headers={"X-Correlation-ID": attacker_value}
            )
            generated = response.headers["X-Correlation-ID"]
            assert UUID(generated).version == 4
            assert generated != attacker_value
            generated_ids.append(generated)
        assert len(set(generated_ids)) == len(attacker_values)
        captured_logs = capsys.readouterr().out
        assert all(value not in captured_logs for value in attacker_values)

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
            filename="fichier1.md",
            template_id=template_id,
            template_version_id=template_version_id,
        )
        assert successful.status_code == 202
        successful_id = UUID(successful.json()["id"])
        owner_id = UUID(successful.json()["owner_id"])
        repository = SqlJobRepository(auxiliary_engine)
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
        assert result.headers["Content-Disposition"] == (
            'attachment; filename="fichier1.docx"'
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

        with ThreadPoolExecutor(max_workers=2) as executor:
            concurrent = list(
                executor.map(
                    lambda index: submit(
                        client,
                        csrf,
                        content=f"# Concurrent {index}".encode(),
                        template_id=template_id,
                        template_version_id=template_version_id,
                    ),
                    range(2),
                )
            )
        assert all(response.status_code == 202 for response in concurrent)
        assert all(
            response.json()["correlation_id"] == response.headers["X-Correlation-ID"]
            for response in concurrent
        )
        assert len({response.json()["correlation_id"] for response in concurrent}) == 2
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
