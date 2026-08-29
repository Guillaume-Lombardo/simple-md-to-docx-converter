"""Functional ASGI tests for the public authentication and administration API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from markweave.app import AppComponents, build_components, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.config import ConfigurationError, Settings
from tests.settings import template_settings


def make_client(data_directory: Path) -> TestClient:
    """Build an HTTPS ASGI client with intentionally cheap test-only Argon2 settings."""
    password = "admin-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="Admin",
        initial_admin_password=password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        session_idle_seconds=60,
        session_absolute_seconds=300,
        storage_profile="standalone",
        standalone_data_directory=data_directory,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    return TestClient(create_app(settings), base_url="https://testserver")


def login(
    client: TestClient, username: str = "admin", password: str | None = None
) -> dict[str, Any]:
    resolved_password = password or "admin-password"
    response = client.post(
        "/api/v1/login", json={"username": username, "password": resolved_password}
    )
    assert response.status_code == 200
    return response.json()


def csrf(payload: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(payload["csrf_token"])}


def session_cookie(client: TestClient) -> str:
    token = client.cookies.get("md_converter_session")
    assert token is not None
    return token


def use_session(client: TestClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set("md_converter_session", token)


def provisioning_settings(data_directory: Path, source: Path | None) -> Settings:
    return Settings(
        **template_settings(),
        initial_admin_username="Admin",
        initial_admin_password="admin-password",  # noqa: S106 - test credential
        user_provisioning_file=source,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        session_idle_seconds=60,
        session_absolute_seconds=300,
        storage_profile="standalone",
        standalone_data_directory=data_directory,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )


@pytest.mark.functional
def test_health_login_page_docs_and_openapi_contract(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        page = client.get("/login")
        assert page.status_code == 200
        assert 'lang="en"' in page.text
        assert "Sign in" in page.text
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert not any("signup" in path or "register" in path for path in paths)


@pytest.mark.functional
def test_readiness_failure_is_cheap_and_stable(tmp_path: Path) -> None:
    password = "admin-" + "password"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=password,
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    built = build_components(settings)
    components = AppComponents(
        authentication=built.authentication,
        readiness=MemoryReadinessProbe(ready=False),
        object_store=built.object_store,
        jobs=built.jobs,
    )
    with TestClient(
        create_app(settings, components=components), base_url="https://testserver"
    ) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NOT_READY"


@pytest.mark.functional
def test_json_login_sets_hardened_cookie_without_exposing_session_token(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/login",
            json={"username": "ADMIN", "password": "admin-password"},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=lax" in cookie
        assert "Max-Age=300" in cookie
        body = response.json()
        assert set(body) == {"user", "csrf_token"}
        assert "admin-password" not in response.text.casefold()
        assert "argon2" not in response.text.casefold()
        assert client.get("/api/v1/session").json()["username"] == "Admin"


@pytest.mark.functional
def test_startup_csv_upsert_and_required_password_renewal_workflow(
    tmp_path: Path,
) -> None:
    source = tmp_path / "users.csv"
    source.write_text(
        "username,password,role,active,password_change_required\n"
        "Alice,temporary-password,user,true,true\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    settings = provisioning_settings(data, source)

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        authenticated = login(client, "alice", "temporary-password")
        assert authenticated["user"]["password_change_required"] is True
        assert client.get("/api/v1/session").status_code == 200
        restricted = client.get("/convert", follow_redirects=False)
        assert restricted.status_code == 303
        assert restricted.headers["location"] == "/change-password"
        page = client.get("/change-password")
        assert page.status_code == 200
        assert "current password was accepted" in page.text
        assert client.get("/api/v1/admin/users").status_code == 403

        mismatch = client.post(
            "/api/v1/password",
            headers=csrf(authenticated),
            json={"password": "renewed-password", "confirmation": "different"},
        )
        assert mismatch.status_code == 422
        changed = client.post(
            "/api/v1/password",
            headers=csrf(authenticated),
            json={
                "password": "renewed-password",
                "confirmation": "renewed-password",
            },
        )
        assert changed.status_code == 204
        assert client.get("/api/v1/session").status_code == 401
        assert (
            client.post(
                "/api/v1/login",
                json={"username": "alice", "password": "temporary-password"},
            ).status_code
            == 401
        )
        renewed = login(client, "alice", "renewed-password")
        assert renewed["user"]["password_change_required"] is False

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        assert (
            client.post(
                "/api/v1/login",
                json={"username": "alice", "password": "renewed-password"},
            ).status_code
            == 401
        )
        reprovisioned = login(client, "alice", "temporary-password")
        assert reprovisioned["user"]["password_change_required"] is True


@pytest.mark.functional
def test_invalid_csv_fails_startup_without_partial_user_batch(tmp_path: Path) -> None:
    source = tmp_path / "users.csv"
    source.write_text(
        "username,password,role,active,password_change_required\n"
        "Alice,first-password,user,true,true\n"
        "\uff21LICE,second-password,user,true,false\n",
        encoding="utf-8",
    )
    data = tmp_path / "data"

    with pytest.raises(ConfigurationError, match="Invalid user provisioning file"):
        create_app(provisioning_settings(data, source))

    with TestClient(
        create_app(provisioning_settings(data, None)),
        base_url="https://testserver",
    ) as client:
        admin = login(client)
        users = client.get("/api/v1/admin/users", headers=csrf(admin)).json()
        assert [user["username"] for user in users] == ["Admin"]


@pytest.mark.functional
def test_invalid_unknown_and_inactive_logins_are_indistinguishable(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        wrong = client.post(
            "/api/v1/login", json={"username": "admin", "password": "wrong"}
        )
        unknown = client.post(
            "/api/v1/login", json={"username": "missing", "password": "wrong"}
        )
        assert wrong.status_code == unknown.status_code == 401
        assert (
            wrong.json()
            == unknown.json()
            == {
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": "The username or password is incorrect.",
                }
            }
        )

        admin = login(client)
        created = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "Alice", "password": "alice-password"},
        ).json()
        disabled = client.patch(
            f"/api/v1/admin/users/{created['id']}/active",
            headers=csrf(admin),
            json={"active": False},
        )
        assert disabled.status_code == 200
        inactive = client.post(
            "/api/v1/login",
            json={"username": "alice", "password": "alice-password"},
        )
        assert inactive.status_code == 401
        assert inactive.json() == wrong.json()


@pytest.mark.functional
def test_admin_lifecycle_csrf_authorization_and_revocation(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        admin = login(client)
        missing_csrf = client.post(
            "/api/v1/admin/users",
            json={"username": "Alice", "password": "alice-password"},
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json()["error"]["code"] == "CSRF_REQUIRED"

        alice = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "  Alice  ", "password": "alice-password"},
        )
        assert alice.status_code == 201
        alice_id = alice.json()["id"]
        duplicate = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "\uff21LICE", "password": "other-password"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "USERNAME_TAKEN"
        assert len(client.get("/api/v1/admin/users").json()) == 2

        alice_login = login(client, "alice", "alice-password")
        forbidden = client.post(
            "/api/v1/admin/users",
            headers=csrf(alice_login),
            json={"username": "Bob", "password": "bob-password"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"

        client.cookies.clear()
        admin = login(client)
        disabled = client.patch(
            f"/api/v1/admin/users/{alice_id}/active",
            headers=csrf(admin),
            json={"active": False},
        )
        assert disabled.status_code == 200
        client.cookies.clear()
        client.cookies.set("md_converter_session", "token-from-revoked-session")
        assert client.get("/api/v1/session").status_code == 401

        client.cookies.clear()
        admin = login(client)
        assert (
            client.patch(
                f"/api/v1/admin/users/{alice_id}/active",
                headers=csrf(admin),
                json={"active": True},
            ).status_code
            == 200
        )
        old_alice = login(client, "alice", "alice-password")
        client.cookies.clear()
        admin = login(client)
        reset = client.post(
            f"/api/v1/admin/users/{alice_id}/password",
            headers=csrf(admin),
            json={"password": "new-alice-password"},
        )
        assert reset.status_code == 204
        client.cookies.clear()
        assert (
            client.post(
                "/api/v1/login",
                json={"username": "alice", "password": "alice-password"},
            ).status_code
            == 401
        )
        assert login(client, "alice", "new-alice-password")["user"]["id"] == alice_id
        assert old_alice["csrf_token"] != admin["csrf_token"]

        client.cookies.clear()
        login(client)
        audits = client.get("/api/v1/audit").json()
        account_audits = [
            record for record in audits if record["target_type"] == "user"
        ]
        assert [record["operation"] for record in account_audits] == [
            "user_password_reset",
            "user_reactivate",
            "user_deactivate",
            "user_create",
            "bootstrap_admin_create",
        ]
        assert account_audits[0]["target_id"] == alice_id
        assert account_audits[0]["target_version"] == "3"
        assert all(record["version_id"] is None for record in account_audits)
        assert all(
            record["administrator_intervention"] for record in account_audits[:-1]
        )
        assert not account_audits[-1]["administrator_intervention"]
        assert "new-alice-password" not in str(audits)
        assert "alice-password" not in str(audits)


@pytest.mark.functional
def test_csrf_replay_session_rotation_and_logout(tmp_path: Path) -> None:
    with (
        make_client(tmp_path / "first") as first,
        make_client(tmp_path / "unrelated") as unrelated,
    ):
        login(first)
        unrelated_login = login(unrelated)
        replay = first.post(
            "/api/v1/admin/users",
            headers=csrf(unrelated_login),
            json={"username": "Alice", "password": "alice-password"},
        )
        assert replay.status_code == 403

        old_cookie = first.cookies.get("md_converter_session")
        rotated = login(first)
        assert first.cookies.get("md_converter_session") != old_cookie
        logout = first.post("/api/v1/logout", headers=csrf(rotated))
        assert logout.status_code == 204
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert first.get("/api/v1/session").status_code == 401


@pytest.mark.functional
def test_browser_login_has_stable_failure_and_redirect_success(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        failed = client.post(
            "/login",
            data={"username": "admin", "password": "wrong"},
        )
        assert failed.status_code == 401
        assert "The username or password is incorrect." in failed.text
        success = client.post(
            "/login",
            data={"username": "admin", "password": "admin-password"},
            follow_redirects=False,
        )
        assert success.status_code == 303
        assert success.headers["location"] == "/convert"


@pytest.mark.functional
def test_login_csrf_rejects_attacker_origin_and_allows_documented_api_policy(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        payload = {"username": "admin", "password": "admin-password"}
        hostile = client.post(
            "/api/v1/login",
            headers={"Origin": "https://attacker.example"},
            json=payload,
        )
        assert hostile.status_code == 403
        assert hostile.json()["error"]["code"] == "LOGIN_ORIGIN_INVALID"
        assert "md_converter_session" not in hostile.cookies

        same_origin = client.post(
            "/api/v1/login",
            headers={"Origin": "https://testserver"},
            json=payload,
        )
        assert same_origin.status_code == 200
        client.cookies.clear()
        assert client.post("/api/v1/login", json=payload).status_code == 200


@pytest.mark.functional
def test_admin_alice_and_bob_lifecycle_preserves_identity_isolation(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        admin = login(client)
        admin_cookie = session_cookie(client)
        alice = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "Alice", "password": "alice-password"},
        ).json()
        bob = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin),
            json={"username": "Bob", "password": "bob-password"},
        ).json()

        client.cookies.clear()
        alice_login = login(client, "alice", "alice-password")
        alice_cookie = session_cookie(client)
        assert client.get("/api/v1/admin/users").status_code == 403
        client.cookies.clear()
        bob_login = login(client, "bob", "bob-password")
        bob_cookie = session_cookie(client)
        assert client.get("/api/v1/admin/users").status_code == 403
        assert alice_login["user"]["id"] != bob_login["user"]["id"]

        use_session(client, admin_cookie)
        cross_session = client.patch(
            f"/api/v1/admin/users/{alice['id']}/active",
            headers=csrf(alice_login),
            json={"active": False},
        )
        assert cross_session.status_code == 403
        assert (
            client.patch(
                f"/api/v1/admin/users/{alice['id']}/active",
                headers=csrf(admin),
                json={"active": False},
            ).status_code
            == 200
        )

        use_session(client, alice_cookie)
        assert client.get("/api/v1/session").status_code == 401
        use_session(client, bob_cookie)
        assert client.get("/api/v1/session").status_code == 200

        use_session(client, admin_cookie)
        assert (
            client.post(
                f"/api/v1/admin/users/{bob['id']}/password",
                headers=csrf(admin),
                json={"password": "new-bob-password"},
            ).status_code
            == 204
        )
        use_session(client, bob_cookie)
        assert client.get("/api/v1/session").status_code == 401
        client.cookies.clear()
        assert login(client, "bob", "new-bob-password")["user"]["id"] == bob["id"]
