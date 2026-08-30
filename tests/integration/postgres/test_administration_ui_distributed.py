"""T17 administration HTTP coverage over live PostgreSQL and RustFS."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError

from markweave.app import AppComponents, build_components, create_app
from markweave.config import Settings
from markweave.malware import TrustingUploadScanner
from markweave.persistence.schema import (
    TemplatePreferenceRow,
    TemplateRow,
    TemplateVersionRow,
    UserRow,
)
from markweave.persistence.sql import SqlUserRepository
from markweave.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from markweave.storage import ObjectKey, ObjectScope
from markweave.templates.models import TemplatePublicationState
from markweave.templates.service import TemplateRecoveryPolicy, TemplateService
from markweave.templates.validation import ValidatedTemplate
from tests.settings import template_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def _settings(username: str, password: str, *, bucket: str | None = None) -> Settings:
    return Settings(
        **template_settings(),
        initial_admin_username=username,
        initial_admin_password=password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="distributed",
        distributed_database_url=os.environ["MARKWEAVE_TEST_POSTGRES_URL"],
        s3_bucket=bucket or os.environ["MARKWEAVE_TEST_S3_BUCKET"],
        s3_endpoint_url=os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"],
        s3_region=os.environ["MARKWEAVE_TEST_S3_REGION"],
        s3_access_key_id=os.environ["MARKWEAVE_TEST_S3_ACCESS_KEY_ID"],
        s3_secret_access_key=os.environ["MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY"],
        conversion_upload_max_bytes=128,
        conversion_request_max_bytes=2_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=60,
    )


def _validated(data: bytes, _declaration: object) -> ValidatedTemplate:
    return ValidatedTemplate(
        hashlib.sha256(data).hexdigest(),
        ("word/document.xml",),
        ("Calibri",),
        ("Calibri",),
        (("Calibri", "Carlito"),),
    )


def _app(settings: Settings) -> tuple[FastAPI, AppComponents]:
    built = build_components(settings)
    try:
        user_repository = cast(SqlUserRepository, built.authentication.users)
        engine = user_repository._engine
        templates = TemplateService(
            catalog=SqlTemplateCatalogRepository(engine),
            selections=SqlTemplateSelectionRepository(engine),
            objects=built.object_store,
            validate_content=_validated,
            recovery_policy=TemplateRecoveryPolicy(60),
        )
        components = replace(
            built, templates=templates, scanner=TrustingUploadScanner()
        )
        return create_app(settings, components=components), components
    except Exception:
        built.close()
        raise


def _login(client: TestClient, username: str, password: str) -> tuple[str, UUID]:
    response = client.post(
        "/api/v1/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"], UUID(response.json()["user"]["id"])


def _create_template(client: TestClient, csrf: str, name: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/templates",
        headers={"X-CSRF-Token": csrf},
        data={
            "name": name,
            "description": f"{name} distributed description",
            "expected_fonts": "Calibri",
        },
        files={
            "content": (
                "safe.docx",
                b"distributed-template",
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 201
    return response.json()


def _cleanup_data(components: AppComponents, user_ids: set[UUID]) -> None:
    user_repository = cast(SqlUserRepository, components.authentication.users)
    engine = user_repository._engine
    with engine.begin() as connection:
        versions = connection.execute(
            select(
                TemplateVersionRow.object_owner_id,
                TemplateVersionRow.id,
                TemplateVersionRow.publication_state,
            ).where(
                TemplateVersionRow.object_owner_id.in_(str(item) for item in user_ids)
            )
        ).all()
    for owner_id, version_id, publication_state in versions:
        if publication_state == TemplatePublicationState.PUBLISHED.value:
            components.object_store.delete(
                ObjectKey(
                    ObjectScope.TEMPLATE_VERSION, UUID(owner_id), UUID(version_id)
                )
            )
    serialized_user_ids = tuple(str(item) for item in user_ids)
    with engine.begin() as connection:
        template_ids = tuple(
            connection.scalars(
                select(TemplateRow.id).where(
                    TemplateRow.owner_id.in_(serialized_user_ids)
                )
            )
        )
        if template_ids:
            connection.execute(
                delete(TemplatePreferenceRow).where(
                    TemplatePreferenceRow.template_id.in_(template_ids)
                )
            )
            connection.execute(
                delete(TemplateRow).where(TemplateRow.id.in_(template_ids))
            )
        connection.execute(delete(UserRow).where(UserRow.id.in_(serialized_user_ids)))


def _cleanup(components: AppComponents, user_ids: set[UUID]) -> None:
    try:
        _cleanup_data(components, user_ids)
    finally:
        components.close()


def test_distributed_administration_pages_owner_search_and_authorization() -> None:
    unique = uuid4().hex
    admin_name = f"admin-{unique}"
    password = "distributed-" + "password"
    app, components = _app(_settings(admin_name, password))
    user_ids: set[UUID] = set()
    try:
        with TestClient(app, base_url="https://testserver") as admin:
            admin_csrf, admin_id = _login(admin, admin_name, password)
            user_ids.add(admin_id)
            created_users: dict[str, UUID] = {}
            for username in (f"alice-{unique}", f"bob-{unique}"):
                response = admin.post(
                    "/api/v1/admin/users",
                    headers={"X-CSRF-Token": admin_csrf},
                    json={"username": username, "password": password},
                )
                assert response.status_code == 201
                created_users[username] = UUID(response.json()["id"])
                user_ids.add(created_users[username])
            assert "Local accounts" in admin.get("/templates").text
            admin_template = _create_template(admin, admin_csrf, f"Admin {unique}")

        alice_name, bob_name = tuple(created_users)
        with TestClient(app, base_url="https://testserver") as alice:
            alice_csrf, alice_id = _login(alice, alice_name, password)
            alice_template = _create_template(alice, alice_csrf, f"Alice {unique}")
            listing = alice.get("/api/v1/templates", params={"limit": 100}).json()
            by_id = {item["id"]: item for item in listing["items"]}
            assert by_id[str(admin_template["id"])]["owner_username"] == admin_name
            assert by_id[str(alice_template["id"])]["owner_username"] == alice_name
            mine = alice.get(
                "/api/v1/templates", params={"owner_id": str(alice_id), "limit": 100}
            ).json()
            assert [item["id"] for item in mine["items"]] == [alice_template["id"]]
            by_name = alice.get(
                "/api/v1/templates", params={"name": unique.upper(), "limit": 100}
            ).json()
            assert by_name["total"] == 2
            assert "Local accounts" not in alice.get("/templates").text

        with TestClient(app, base_url="https://testserver") as bob:
            bob_csrf, _bob_id = _login(bob, bob_name, password)
            visible = bob.get(
                "/api/v1/templates", params={"name": unique, "limit": 100}
            ).json()
            assert {item["owner_username"] for item in visible["items"]} == {
                admin_name,
                alice_name,
            }
            forbidden = bob.patch(
                f"/api/v1/templates/{alice_template['id']}",
                headers={
                    "X-CSRF-Token": bob_csrf,
                    "If-Match": (
                        f'"template-{alice_template["id"]}-'
                        f'{alice_template["revision"]}"'
                    ),
                },
                json={"name": "Forbidden", "description": "Forbidden"},
            )
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "FORBIDDEN"
    finally:
        _cleanup(components, user_ids)


def test_distributed_administration_sanitizes_real_missing_bucket_failure() -> None:
    unique = uuid4().hex
    username = f"failure-{unique}"
    password = "distributed-" + "password"
    app, components = _app(
        _settings(username, password, bucket=f"missing-t17-{unique}")
    )
    user_ids: set[UUID] = set()
    try:
        with TestClient(app, base_url="https://testserver") as client:
            csrf, user_id = _login(client, username, password)
            user_ids.add(user_id)
            failed = client.post(
                "/api/v1/templates",
                headers={"X-CSRF-Token": csrf},
                data={
                    "name": "Unavailable",
                    "description": "Must not leak",
                    "expected_fonts": "Calibri",
                },
                files={
                    "content": (
                        "private-name.docx",
                        b"must-not-leak",
                        "application/octet-stream",
                    )
                },
            )
            assert failed.status_code == 503
            assert failed.json() == {
                "error": {
                    "code": "TEMPLATE_STORAGE_UNAVAILABLE",
                    "message": "Template storage is unavailable.",
                }
            }
            assert "private-name" not in failed.text
            assert "must-not-leak" not in failed.text
            assert client.get("/api/v1/templates").json()["total"] == 0
            assert client.get("/health/ready").status_code == 503
    finally:
        _cleanup(components, user_ids)


def test_distributed_administration_sanitizes_persistence_failure() -> None:
    unique = uuid4().hex
    username = f"failure-{unique}"
    password = "distributed-" + "password"
    app, components = _app(_settings(username, password))
    user_ids: set[UUID] = set()
    user_repository = cast(SqlUserRepository, components.authentication.users)

    def fail_statement(*_arguments: object) -> None:
        raise OperationalError("SELECT", {}, RuntimeError("private database detail"))

    try:
        with TestClient(app, base_url="https://testserver") as client:
            _csrf, user_id = _login(client, username, password)
            user_ids.add(user_id)
            event.listen(
                user_repository._engine,
                "before_cursor_execute",
                fail_statement,
                once=True,
            )
            failed = client.get("/api/v1/templates")
            assert failed.status_code == 503
            assert failed.json() == {
                "error": {
                    "code": "PERSISTENCE_UNAVAILABLE",
                    "message": "Persistent storage is unavailable.",
                }
            }
            assert "private" not in failed.text
            assert "select" not in failed.text.casefold()
    finally:
        event.remove(
            user_repository._engine,
            "before_cursor_execute",
            fail_statement,
        )
        _cleanup(components, user_ids)
