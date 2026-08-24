"""T17 browser-facing administration coverage over real standalone boundaries."""

import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from md_converter.app import create_app
from md_converter.config import Settings
from md_converter.malware import TrustingUploadScanner
from tests.settings import template_settings
from tests.unit.test_template_validation import _docx

pytestmark = pytest.mark.functional


def _app(tmp_path: Path):
    engine = tmp_path / "template-engine"
    engine.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, shutil, sys\n"
        "args=sys.argv[1:]\n"
        "reference=next((x.split('=',1)[1] for x in args if x.startswith('--reference-doc=')), None)\n"
        "output=next((x.split('=',1)[1] for x in args if x.startswith('--output=')), None)\n"
        "if reference and output: shutil.copyfile(reference, output)\n"
        "elif '--outdir' in args:\n"
        " source=pathlib.Path(args[-1]); target=pathlib.Path(args[args.index('--outdir')+1])/source.name; shutil.copyfile(source,target)\n",
        encoding="utf-8",
    )
    engine.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return create_app(
        Settings(
            **template_settings(
                template_pandoc_executable=str(engine),
                template_libreoffice_executable=str(engine),
            ),
            initial_admin_username="Admin",
            initial_admin_password="admin-" + "password",
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="standalone",
            standalone_data_directory=tmp_path,
            conversion_upload_max_bytes=1_000_000,
            conversion_request_max_bytes=1_100_000,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=3_600,
        ),
        scanner=TrustingUploadScanner(),
    )


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _create_template(client: TestClient, csrf: str, name: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/templates",
        headers={"X-CSRF-Token": csrf},
        data={
            "name": name,
            "description": f"{name} description",
            "expected_fonts": ["Calibri", "Cambria", "Courier New"],
        },
        files={"content": (f"{name}.docx", _docx(), "application/octet-stream")},
    )
    assert response.status_code == 201
    return response.json()


def test_owner_and_admin_pages_expose_safe_role_specific_workflows(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as admin:
        assert (
            admin.get("/templates", follow_redirects=False).headers["location"]
            == "/login"
        )
        admin_csrf = _login(admin, "admin", "admin-password")
        for username in ("Alice", "Bob"):
            created = admin.post(
                "/api/v1/admin/users",
                headers={"X-CSRF-Token": admin_csrf},
                json={"username": username, "password": f"{username.lower()}-password"},
            )
            assert created.status_code == 201

        admin_template = _create_template(admin, admin_csrf, "Admin report")
        admin_page = admin.get("/templates")
        assert admin_page.status_code == 200
        assert "Manage templates" in admin_page.text
        assert "Local accounts" in admin_page.text
        assert 'data-user-role="admin"' in admin_page.text
        assert admin.get("/static/administration.js").status_code == 200
        assert admin.get("/static/administration.css").status_code == 200
        assert admin_page.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in admin_page.headers["content-security-policy"]

    with TestClient(app, base_url="https://testserver") as alice:
        alice_csrf = _login(alice, "alice", "alice-password")
        alice_template = _create_template(alice, alice_csrf, "Alice report")
        page = alice.get("/templates")
        assert 'data-user-role="user"' in page.text
        assert "Local accounts" not in page.text
        listing = alice.get("/api/v1/templates", params={"limit": 100})
        assert listing.status_code == 200
        by_id = {item["id"]: item for item in listing.json()["items"]}
        assert by_id[admin_template["id"]]["owner_username"] == "Admin"
        assert by_id[alice_template["id"]]["owner_username"] == "Alice"
        assert "password" not in listing.text.casefold()
        forbidden = alice.patch(
            f"/api/v1/templates/{admin_template['id']}",
            headers={
                "X-CSRF-Token": alice_csrf,
                "If-Match": (
                    f'"template-{admin_template["id"]}-{admin_template["revision"]}"'
                ),
            },
            json={"name": "Stolen", "description": "Forbidden"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json() == {
            "error": {
                "code": "FORBIDDEN",
                "message": "You are not authorized to perform this operation.",
            }
        }
        assert alice.get("/api/v1/admin/users").status_code == 403

    with TestClient(app, base_url="https://testserver") as bob:
        _login(bob, "bob", "bob-password")
        visible = bob.get("/api/v1/templates", params={"limit": 100}).json()["items"]
        assert {item["owner_username"] for item in visible} == {"Admin", "Alice"}


def test_administrator_account_actions_remain_server_enforced(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as admin:
        csrf = _login(admin, "admin", "admin-password")
        alice = admin.post(
            "/api/v1/admin/users",
            headers={"X-CSRF-Token": csrf},
            json={"username": "Alice", "password": "alice-password"},
        ).json()
        assert any(
            user["username"] == "Alice"
            for user in admin.get("/api/v1/admin/users").json()
        )
        for active in (False, True):
            changed = admin.patch(
                f"/api/v1/admin/users/{alice['id']}/active",
                headers={"X-CSRF-Token": csrf},
                json={"active": active},
            )
            assert changed.status_code == 200
            assert changed.json()["active"] is active
        reset = admin.post(
            f"/api/v1/admin/users/{alice['id']}/password",
            headers={"X-CSRF-Token": csrf},
            json={"password": "new-alice-password"},
        )
        assert reset.status_code == 204

    with TestClient(app, base_url="https://testserver") as alice_client:
        assert _login(alice_client, "alice", "new-alice-password")
