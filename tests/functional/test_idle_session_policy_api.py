"""Functional HTTP coverage for administrator idle-session policy management."""

from pathlib import Path

import pytest

from tests.functional.test_auth_api import (
    csrf,
    login,
    make_client,
    session_cookie,
    use_session,
)


@pytest.mark.functional
def test_policy_defaults_authorization_bounds_concurrency_and_audit(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        admin_login = login(client)
        admin_token = session_cookie(client)
        created = client.post(
            "/api/v1/admin/users",
            headers=csrf(admin_login),
            json={"username": "Alice", "password": "alice-password"},
        )
        assert created.status_code == 201

        client.cookies.clear()
        user_login = login(client, "alice", "alice-password")
        assert client.get("/api/v1/admin/session-policy").status_code == 403
        assert (
            client.put(
                "/api/v1/admin/session-policy",
                headers={**csrf(user_login), "If-Match": '"idle-session-policy-0"'},
                json={"user_idle_minutes": 5, "admin_idle_minutes": 5},
            ).status_code
            == 403
        )

        use_session(client, admin_token)
        defaults = client.get("/api/v1/admin/session-policy")
        assert defaults.status_code == 200
        assert defaults.json() == {
            "user_idle_minutes": 30,
            "admin_idle_minutes": 15,
            "revision": 0,
        }
        assert defaults.headers["etag"] == '"idle-session-policy-0"'

        for payload in (
            {"user_idle_minutes": 4, "admin_idle_minutes": 5},
            {"user_idle_minutes": 301, "admin_idle_minutes": 5},
            {"user_idle_minutes": 5, "admin_idle_minutes": 4},
            {"user_idle_minutes": 5, "admin_idle_minutes": 61},
            {"user_idle_minutes": 5.5, "admin_idle_minutes": 5},
        ):
            invalid = client.put(
                "/api/v1/admin/session-policy",
                headers={**csrf(admin_login), "If-Match": defaults.headers["etag"]},
                json=payload,
            )
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "REQUEST_INVALID"

        missing = client.put(
            "/api/v1/admin/session-policy",
            headers=csrf(admin_login),
            json={"user_idle_minutes": 5, "admin_idle_minutes": 60},
        )
        assert missing.status_code == 428
        updated = client.put(
            "/api/v1/admin/session-policy",
            headers={**csrf(admin_login), "If-Match": defaults.headers["etag"]},
            json={"user_idle_minutes": 5, "admin_idle_minutes": 60},
        )
        assert updated.status_code == 200
        assert updated.json() == {
            "user_idle_minutes": 5,
            "admin_idle_minutes": 60,
            "revision": 1,
        }
        assert updated.headers["etag"] == '"idle-session-policy-1"'

        stale = client.put(
            "/api/v1/admin/session-policy",
            headers={**csrf(admin_login), "If-Match": defaults.headers["etag"]},
            json={"user_idle_minutes": 300, "admin_idle_minutes": 5},
        )
        assert stale.status_code == 412
        assert client.get("/api/v1/admin/session-policy").json() == updated.json()

        audit = client.get("/api/v1/audit").json()[0]
        assert audit["actor_id"] == admin_login["user"]["id"]
        assert audit["operation"] == "idle_session_policy_update"
        assert audit["target_type"] == "session_policy"
        assert audit["target_version"] == "1"
        assert (
            audit["old_user_idle_minutes"],
            audit["old_admin_idle_minutes"],
        ) == (30, 15)
        assert (
            audit["new_user_idle_minutes"],
            audit["new_admin_idle_minutes"],
        ) == (5, 60)


@pytest.mark.functional
def test_policy_persists_across_application_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as first:
        admin = login(first)
        current = first.get("/api/v1/admin/session-policy")
        response = first.put(
            "/api/v1/admin/session-policy",
            headers={**csrf(admin), "If-Match": current.headers["etag"]},
            json={"user_idle_minutes": 300, "admin_idle_minutes": 5},
        )
        assert response.status_code == 200

    with make_client(tmp_path) as restarted:
        login(restarted)
        persisted = restarted.get("/api/v1/admin/session-policy")
        assert persisted.json() == {
            "user_idle_minutes": 300,
            "admin_idle_minutes": 5,
            "revision": 1,
        }
