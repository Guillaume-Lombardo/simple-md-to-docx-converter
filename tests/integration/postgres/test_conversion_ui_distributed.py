"""Conversion UI composition over live PostgreSQL and S3-compatible storage."""

import hashlib
import os
import sys
from collections.abc import Callable
from functools import partial
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update

from markweave.app import create_app
from markweave.config import Settings
from markweave.malware import TrustingUploadScanner
from markweave.persistence.schema import (
    ConversionJobRow,
    SystemTemplateSelectionRow,
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.persistence.templates import SqlTemplateSelectionRepository
from markweave.storage import ObjectKey, ObjectScope, ObjectStore
from tests.settings import template_settings
from tests.template_records import publish_template_pair


def _best_effort(actions: tuple[Callable[[], None], ...]) -> None:
    failures: list[str] = []
    for action in actions:
        try:
            action()
        except Exception as error:
            failures.append(type(error).__name__)
    if failures:
        print(
            f"Distributed test cleanup failures: {', '.join(failures)}",
            file=sys.stderr,
        )


def _delete_objects(store: ObjectStore, keys: tuple[ObjectKey, ...]) -> None:
    _best_effort(tuple(partial(store.delete, key) for key in keys))


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
        preferred_template_id, preferred_version_id = uuid4(), uuid4()
        assert preferred_template_id != template_id
        assert preferred_version_id != version_id
        fallback_content = b"distributed-fallback-template"
        preferred_content = b"distributed-preferred-template"
        engine = create_database_engine(database_url)
        selections = SqlTemplateSelectionRepository(engine)
        previous_fallback = selections.system_fallback_id()
        template_ids = (str(template_id), str(preferred_template_id))
        version_keys = (
            ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, version_id),
            ObjectKey(ObjectScope.TEMPLATE_VERSION, owner_id, preferred_version_id),
        )

        def cleanup() -> None:
            owned_objects = list(version_keys)
            owner = str(owner_id)

            def cleanup_database() -> None:
                with engine.begin() as connection:
                    jobs = connection.execute(
                        select(
                            ConversionJobRow.source_object_id,
                            ConversionJobRow.result_object_id,
                            ConversionJobRow.result_manifest_object_id,
                        ).where(ConversionJobRow.owner_id == owner)
                    ).all()
                    for source_id, result_id, manifest_id in jobs:
                        owned_objects.append(
                            ObjectKey(ObjectScope.UPLOAD, owner_id, UUID(source_id))
                        )
                        if result_id is not None:
                            owned_objects.append(
                                ObjectKey(ObjectScope.RESULT, owner_id, UUID(result_id))
                            )
                        if manifest_id is not None:
                            owned_objects.append(
                                ObjectKey(
                                    ObjectScope.RESULT_MANIFEST,
                                    owner_id,
                                    UUID(manifest_id),
                                )
                            )
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
                    connection.execute(
                        delete(ConversionJobRow).where(
                            ConversionJobRow.owner_id == owner
                        )
                    )
                    connection.execute(
                        delete(TemplatePreferenceRow).where(
                            TemplatePreferenceRow.user_id == str(owner_id)
                        )
                    )
                    connection.execute(
                        delete(TemplateVersionRow).where(
                            TemplateVersionRow.template_id.in_(template_ids)
                        )
                    )
                    connection.execute(
                        delete(TemplateRow).where(TemplateRow.id.in_(template_ids))
                    )
                    connection.execute(
                        delete(UserRow).where(UserRow.id == str(owner_id))
                    )

            def cleanup_objects() -> None:
                _delete_objects(app.state.components.object_store, tuple(owned_objects))

            _best_effort((cleanup_database, cleanup_objects, engine.dispose))

        request.addfinalizer(cleanup)
        publish_template_pair(
            engine,
            owner_id,
            template_id,
            version_id,
            sha256=hashlib.sha256(fallback_content).hexdigest(),
            size=len(fallback_content),
        )
        publish_template_pair(
            engine,
            owner_id,
            preferred_template_id,
            preferred_version_id,
            sha256=hashlib.sha256(preferred_content).hexdigest(),
            size=len(preferred_content),
        )
        with engine.begin() as connection:
            connection.execute(
                update(TemplateRow)
                .where(TemplateRow.id == str(preferred_template_id))
                .values(
                    name="Distinct preferred template",
                    normalized_name="distinct preferred template",
                    description="Preferred PostgreSQL selection",
                    normalized_description="preferred postgresql selection",
                )
            )
        app.state.components.object_store.put(version_keys[0], fallback_content)
        app.state.components.object_store.put(version_keys[1], preferred_content)
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
        assert (
            app.state.components.object_store.get(version_keys[0]) == fallback_content
        )

        selections.set_preferred(owner_id, preferred_template_id)
        preferred = client.get("/api/v1/conversion-options").json()
        assert preferred["selection_source"] == "preferred"
        assert preferred["resolved_template"]["id"] == str(preferred_template_id)
        assert preferred["resolved_template"]["current_version_id"] == str(
            preferred_version_id
        )
        assert preferred["template_version_id"] == str(preferred_version_id)
        assert client.get("/api/v1/template-context").json() == {
            "preferred_template_id": str(preferred_template_id),
            "system_fallback_template_id": str(template_id),
            "template_max_archive_bytes": 1_000_000,
        }
        preferred_page = client.get("/convert")
        assert preferred_page.status_code == 200
        assert "Preferred template" in preferred_page.text
        assert "Distinct preferred template" in preferred_page.text
        assert "Preferred PostgreSQL selection" in preferred_page.text
        assert str(preferred_version_id) in preferred_page.text
        assert str(version_id) not in preferred_page.text
        assert (
            app.state.components.object_store.get(version_keys[1]) == preferred_content
        )
        csrf = login.json()["csrf_token"]
        created = client.post(
            "/api/v1/conversions",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"ui-{unique}"},
            files={"source": ("source.md", b"# Distributed UI", "text/markdown")},
            data={
                "template_id": str(preferred_template_id),
                "template_version_id": str(preferred_version_id),
                "output": "pdf",
            },
        )
        assert created.status_code == 202
        created_payload = created.json()
        assert created_payload["template_id"] == str(preferred_template_id)
        assert created_payload["template_version_id"] == str(preferred_version_id)
        assert created_payload["template_mode"] == "versioned"
        assert app.state.components.object_store.is_ready()
        status = client.get(created.headers["Location"])
        assert status.status_code == 200
        status_payload = status.json()
        assert status_payload["state"] == "queued"
        assert status_payload["template_id"] == str(preferred_template_id)
        assert status_payload["template_version_id"] == str(preferred_version_id)
        assert status_payload["template_mode"] == "versioned"
