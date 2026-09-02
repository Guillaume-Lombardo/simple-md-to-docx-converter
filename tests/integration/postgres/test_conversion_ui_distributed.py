"""Conversion UI composition over live PostgreSQL and S3-compatible storage."""

import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from markweave.app import create_app
from markweave.config import Settings
from markweave.malware import TrustingUploadScanner
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.schema import (
    ConversionJobRow,
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.persistence.templates import SqlTemplateSelectionRepository
from markweave.storage import ObjectKey, ObjectScope
from tests.settings import template_settings
from tests.template_records import publish_template_pair


@pytest.mark.integration
@pytest.mark.requires_postgres
@pytest.mark.requires_s3
def test_distributed_conversion_ui_submits_to_postgresql_and_rustfs(  # noqa: PLR0915
    request: pytest.FixtureRequest,
) -> None:
    unique = uuid4().hex
    username = f"ui-{unique}"
    password = "distributed-" + "password"
    database_url = os.environ["MARKWEAVE_TEST_POSTGRES_URL"]
    app = create_app(
        Settings(
            **template_settings(),
            initial_admin_username=username,
            initial_admin_password=password,
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="distributed",
            distributed_database_url=database_url,
            s3_bucket=os.environ["MARKWEAVE_TEST_S3_BUCKET"],
            s3_endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
            s3_region=os.environ["MARKWEAVE_TEST_S3_REGION"],
            s3_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
            s3_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
            conversion_upload_max_bytes=128,
            conversion_request_max_bytes=2_000,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=60,
        ),
        scanner=TrustingUploadScanner(),
    )
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post(
            "/api/v1/login", json={"username": username, "password": password}
        )
        assert login.status_code == 200
        owner_id = UUID(login.json()["user"]["id"])
        template_id, version_id = uuid4(), uuid4()
        engine = create_database_engine(database_url)
        publish_template_pair(engine, owner_id, template_id, version_id)
        selections = SqlTemplateSelectionRepository(engine)
        previous_fallback = selections.system_fallback_id()
        job_id: UUID | None = None

        def cleanup() -> None:
            if job_id is not None:
                job = SqlJobRepository(engine).get(job_id)
                if job is not None:
                    app.state.components.object_store.delete(
                        ObjectKey(ObjectScope.UPLOAD, owner_id, job.source_object_id)
                    )
            with engine.begin() as connection:
                current = connection.scalar(
                    select(SystemTemplateSelectionRow.fallback_template_id).where(
                        SystemTemplateSelectionRow.id == 1
                    )
                )
                if current == str(template_id):
                    if previous_fallback is None:
                        connection.execute(delete(SystemTemplateSelectionRow))
                    else:
                        connection.execute(
                            update(SystemTemplateSelectionRow)
                            .where(SystemTemplateSelectionRow.id == 1)
                            .values(fallback_template_id=str(previous_fallback))
                        )
                if job_id is not None:
                    connection.execute(
                        delete(ConversionJobRow).where(
                            ConversionJobRow.id == str(job_id)
                        )
                    )
                connection.execute(
                    delete(TemplatePreferenceRow).where(
                        TemplatePreferenceRow.user_id == str(owner_id)
                    )
                )
                connection.execute(
                    delete(TemplateRow).where(TemplateRow.id == str(template_id))
                )
                connection.execute(delete(UserRow).where(UserRow.id == str(owner_id)))
            engine.dispose()

        request.addfinalizer(cleanup)
        defaults = client.get("/api/v1/conversion-options").json()
        assert defaults == {
            "conversion_upload_max_bytes": 128,
            "resolved_template": None,
            "template_version_id": None,
            "selection_source": "pandoc_default",
        }
        selections.set_system_fallback(template_id)
        options = client.get("/api/v1/conversion-options").json()
        assert options["selection_source"] == "system_fallback"
        assert options["resolved_template"]["id"] == str(template_id)
        assert options["template_version_id"] == str(version_id)
        assert client.get("/api/v1/template-context").json() == {
            "preferred_template_id": None,
            "system_fallback_template_id": str(template_id),
            "template_max_archive_bytes": 1_000_000,
        }
        fallback_page = client.get("/convert")
        assert fallback_page.status_code == 200
        assert "System fallback template" in fallback_page.text
        assert str(version_id) in fallback_page.text

        selections.set_preferred(owner_id, template_id)
        assert (
            client.get("/api/v1/conversion-options").json()["selection_source"]
            == "preferred"
        )
        preferred_page = client.get("/convert")
        assert preferred_page.status_code == 200
        assert "Preferred template" in preferred_page.text
        assert str(version_id) in preferred_page.text
        csrf = login.json()["csrf_token"]
        created = client.post(
            "/api/v1/conversions",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"ui-{unique}"},
            files={"source": ("source.md", b"# Distributed UI", "text/markdown")},
            data={
                "template_id": str(template_id),
                "template_version_id": str(version_id),
                "output": "pdf",
            },
        )
        assert created.status_code == 202
        job_id = UUID(created.json()["id"])
        assert app.state.components.object_store.is_ready()
        status = client.get(created.headers["Location"])
        assert status.status_code == 200
        assert status.json()["state"] == "queued"
