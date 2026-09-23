"""Composer connection HTTP boundaries reject disclosure and stale mutation."""

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.connections import (
    ConnectionRecord,
    ConnectionScope,
    ConnectionService,
    IdentityMode,
)
from markweave.composer.egress import EgressCapacityError
from markweave.config import Settings
from markweave.persistence.composer import SqlConnectionRepository
from tests.settings import template_settings


def _client(mocker: MockerFixture) -> tuple[TestClient, Any, User]:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        composer_retry_after_seconds=7,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=2048,
        composer_http_request_max_bytes=4096,
        composer_maximum_credential_bytes=16384,
        composer_maximum_request_bytes=8192,
        composer_maximum_output_tokens=128,
    )
    administrator = User(UUID(int=1), "admin", "admin", "hash", Role.ADMIN)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = administrator
    authentication.authenticate.return_value = administrator
    repository = mocker.Mock(spec=SqlConnectionRepository)
    repository.can_manage_personal.return_value = False
    repository.get_outage.return_value = False
    service = mocker.Mock(spec=ConnectionService)
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_connection_repository=repository,
            composer_connections=service,
        ),
    )
    return TestClient(app, base_url="https://testserver"), service, administrator


@pytest.mark.unit
def test_connection_credentials_remain_write_only_in_response_and_validation_error(
    mocker: MockerFixture,
) -> None:
    client, service, administrator = _client(mocker)
    connection = ConnectionRecord(
        id=UUID(int=7),
        scope=ConnectionScope.INSTANCE,
        owner_id=None,
        identity_mode=IdentityMode.SHARED,
        endpoint="https://models.example.test/v1",
        selected_model="small",
        permitted_models=("small",),
        enabled=True,
        allowed_user_ids=frozenset({administrator.id}),
        version=1,
        generation=1,
        name="Internal model",
    )
    service.list_visible.return_value = (connection,)
    service.get_visible.return_value = connection
    service.set_credentials.return_value = connection
    presence = mocker.Mock()
    presence.has_api_key = True
    presence.has_client_certificate = False
    presence.has_ca_bundle = False
    service.credential_presence.return_value = presence
    secret = "private-" + "material"

    with client:
        response = client.put(
            f"/api/v1/composer/connections/{connection.id}/credentials",
            json={"api_key": secret},
            headers={"If-Match": '"1"', "X-CSRF-Token": "csrf"},
        )
        invalid = client.put(
            f"/api/v1/composer/connections/{connection.id}/credentials",
            json={"api_key": secret, "revoke": True},
            headers={"If-Match": '"1"', "X-CSRF-Token": "csrf"},
        )
        malformed = client.put(
            f"/api/v1/composer/connections/{connection.id}/credentials",
            json={"api_key": {"nested": secret}},
            headers={"If-Match": '"1"', "X-CSRF-Token": "csrf"},
        )

    assert response.status_code == 200
    assert response.headers["ETag"] == '"1"'
    assert response.json()["credential_present"] is True
    assert secret not in response.text
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert malformed.status_code == 422
    assert secret not in malformed.text
    service.set_credentials.assert_called_once()


@pytest.mark.unit
def test_composer_capabilities_are_safe_when_unconfigured(
    mocker: MockerFixture,
) -> None:
    client, service, _administrator = _client(mocker)
    service.availability.return_value.state.value = "unconfigured"

    with client:
        response = client.get("/api/v1/composer/capabilities")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["status"] == "unconfigured"
    assert response.json()["instance_connections_manageable"] is True
    assert response.json()["maximum_upload_bytes"] == 2048
    assert response.json()["maximum_credential_bytes"] == 16384
    assert response.json()["maximum_model_request_bytes"] == 8192
    assert response.json()["maximum_output_tokens"] == 128


@pytest.mark.unit
def test_connection_list_enforces_bounded_pagination(
    mocker: MockerFixture,
) -> None:
    client, service, _administrator = _client(mocker)
    service.list_visible.return_value = ()

    with client:
        page = client.get("/api/v1/composer/connections?limit=7&offset=9")
        invalid = client.get("/api/v1/composer/connections?limit=101")

    assert page.status_code == 200
    assert page.json() == {"connections": [], "limit": 7, "offset": 9}
    assert page.headers["Cache-Control"] == "private, no-store"
    service.list_visible.assert_called_once()
    assert service.list_visible.call_args.kwargs == {"limit": 7, "offset": 9}
    assert invalid.status_code == 422


@pytest.mark.unit
def test_personal_permission_list_uses_bounded_repository_page(
    mocker: MockerFixture,
) -> None:
    client, _service, administrator = _client(mocker)
    repository = client.app.state.components.composer_connection_repository
    repository.list_personal_permissions.return_value = (
        (administrator.id, administrator.username, False, 0),
    )

    with client:
        page = client.get("/api/v1/composer/personal-permissions?limit=2&offset=1")
        invalid = client.get("/api/v1/composer/personal-permissions?offset=-1")

    assert page.status_code == 200
    assert page.json() == {
        "permissions": [
            {
                "user_id": str(administrator.id),
                "username": administrator.username,
                "allowed": False,
                "etag": '"0"',
            }
        ],
        "limit": 2,
        "offset": 1,
    }
    repository.list_personal_permissions.assert_called_once_with(limit=2, offset=1)
    assert invalid.status_code == 422


@pytest.mark.unit
def test_revoked_personal_capability_keeps_metadata_but_marks_it_unauthorized(
    mocker: MockerFixture,
) -> None:
    client, service, administrator = _client(mocker)
    record = ConnectionRecord(
        id=UUID(int=8),
        scope=ConnectionScope.PERSONAL,
        owner_id=administrator.id,
        identity_mode=IdentityMode.INDIVIDUAL,
        endpoint="https://models.example.test/v1",
        selected_model="small",
        permitted_models=("small",),
        enabled=True,
        allowed_user_ids=frozenset(),
        version=1,
        generation=1,
        name="Former personal model",
    )
    service.list_visible.return_value = (record,)
    presence = mocker.Mock()
    presence.has_api_key = True
    presence.has_client_certificate = False
    presence.has_ca_bundle = False
    service.credential_presence.return_value = presence

    with client:
        response = client.get("/api/v1/composer/connections")

    assert response.status_code == 200
    assert response.json()["connections"][0]["authorized"] is False
    assert response.json()["connections"][0]["status"] == "unauthorized"


@pytest.mark.unit
def test_model_discovery_capacity_is_retryable_without_provider_outage(
    mocker: MockerFixture,
) -> None:
    client, service, _administrator = _client(mocker)
    service.discover_models.side_effect = EgressCapacityError(
        "Model connection is busy"
    )

    with client:
        response = client.get(f"/api/v1/composer/connections/{UUID(int=7)}/models")
        documented = client.app.openapi()["paths"][
            "/api/v1/composer/connections/{connection_id}/models"
        ]["get"]["responses"]["503"]["headers"]

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "7"
    assert response.json()["error"]["code"] == "COMPOSER_CAPACITY_EXHAUSTED"
    assert "Retry-After" in documented
