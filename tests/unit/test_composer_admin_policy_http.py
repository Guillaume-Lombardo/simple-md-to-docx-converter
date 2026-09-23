"""Only authenticated administrators can approve Composer destinations."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.admin_policy import AdminPolicy, ComposerAdminPolicy
from markweave.config import Settings
from tests.settings import template_settings


@pytest.mark.unit
def test_policy_http_requires_admin_and_fences_stale_mutations(
    mocker: MockerFixture,
) -> None:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - test-only credential
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_http_request_max_bytes=4096,
    )
    administrator = User(UUID(int=1), "admin", "admin", "hash", Role.ADMIN)
    ordinary = User(UUID(int=2), "user", "user", "hash", Role.USER)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.authenticate.return_value = ordinary
    policy = mocker.Mock(spec=ComposerAdminPolicy)
    policy.read.return_value = AdminPolicy("delegated", False, (), (), 0)
    policy.resolve.return_value = ("models.example.test:443", ("192.0.2.10/32",))
    policy.write.return_value = AdminPolicy(
        "delegated", True, ("models.example.test:443",), ("192.0.2.10/32",), 1
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_admin_policy=policy,
        ),
    )
    with TestClient(app, base_url="https://testserver") as client:
        denied_read = client.get("/api/v1/admin/composer-policy")
        denied_write = client.put(
            "/api/v1/admin/composer-policy",
            headers={"If-Match": '"0"', "X-CSRF-Token": "csrf"},
            json={"enabled": True, "allowed_destinations": [], "allowed_networks": []},
        )
        denied_resolve = client.post(
            "/api/v1/admin/composer-policy/resolve",
            headers={"X-CSRF-Token": "csrf"},
            json={"endpoint": "https://models.example.test/v1"},
        )
        assert (
            denied_read.status_code,
            denied_write.status_code,
            denied_resolve.status_code,
        ) == (
            403,
            403,
            403,
        )
        policy.write.assert_not_called()
        policy.resolve.assert_not_called()

        authentication.authenticate.return_value = administrator
        initial = client.get("/api/v1/admin/composer-policy")
        assert initial.status_code == 200
        assert initial.json()["etag"] == '"0"'
        assert initial.headers["Cache-Control"] == "private, no-store"
        missing_precondition = client.put(
            "/api/v1/admin/composer-policy",
            headers={"X-CSRF-Token": "csrf"},
            json={"enabled": True, "allowed_destinations": [], "allowed_networks": []},
        )
        assert missing_precondition.status_code == 428
        preview = client.post(
            "/api/v1/admin/composer-policy/resolve",
            headers={"X-CSRF-Token": "csrf"},
            json={"endpoint": "https://models.example.test/v1"},
        )
        assert preview.json() == {
            "destination": "models.example.test:443",
            "addresses": ["192.0.2.10/32"],
        }
        approved = client.put(
            "/api/v1/admin/composer-policy",
            headers={"If-Match": '"0"', "X-CSRF-Token": "csrf"},
            json={
                "enabled": True,
                "allowed_destinations": ["models.example.test:443"],
                "allowed_networks": ["192.0.2.10/32"],
            },
        )
        assert approved.status_code == 200
        assert approved.headers["ETag"] == '"1"'
        policy.write.assert_called_once_with(
            enabled=True,
            destinations=("models.example.test:443",),
            networks=("192.0.2.10/32",),
            expected_version=0,
            actor_id=administrator.id,
        )
