"""Real HTTP integration coverage for T35 remote command workflows."""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path
from threading import Thread
from time import sleep

import pytest
import uvicorn

from markweave.app import create_app
from markweave.auth.models import normalize_username
from markweave.cli.commands import administration
from markweave.cli.http import HttpTransport
from markweave.cli.main import main
from markweave.cli.profiles import ProfileStore
from markweave.cli.types import ConnectionProfile
from markweave.config import Settings
from tests.settings import template_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def administration_service(tmp_path: Path):
    """Run the complete standalone application across real loopback TCP."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        **template_settings(),
        initial_admin_username="Admin",
        initial_admin_password="admin-password",  # noqa: S106
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path / "data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    application = create_app(settings)
    application_logger = logging.getLogger("md_converter.application")
    previous_handlers = application_logger.handlers[:]
    application_logger.handlers[:] = [logging.NullHandler()]
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}", application
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        application_logger.handlers[:] = previous_handlers


def _save_login(url: str, name: str, username: str, password: str) -> None:
    response = HttpTransport(url, verify_tls=False, timeout=2).login(username, password)
    assert response.status == 200
    assert response.session is not None and response.payload is not None
    csrf = response.payload["csrf_token"]
    assert isinstance(csrf, str)
    ProfileStore().save(ConnectionProfile(name, url, response.session, csrf))


def _session_status(url: str, name: str) -> tuple[int, str | None]:
    profile = ProfileStore().load(name)
    response = HttpTransport(url, verify_tls=False, timeout=2).session(profile)
    error = response.payload.get("error") if response.payload is not None else None
    code = error.get("code") if isinstance(error, dict) else None
    return response.status, code if isinstance(code, str) else None


def test_admin_commands_cover_two_users_authorization_and_audit_pagination(
    administration_service, monkeypatch, mocker, tmp_path: Path, capsys
) -> None:
    url, application = administration_service
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    auth = application.state.components.authentication
    admin = auth.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    alice = auth.create_user(admin, "alice", "alice-password")
    restricted = auth.create_user(
        admin,
        "restricted",
        "restricted-password",
        password_change_required=True,
    )
    _save_login(url, "admin", "admin", "admin-password")
    _save_login(url, "alice", "alice", "alice-password")
    _save_login(url, "restricted", "restricted", "restricted-password")

    prompt = mocker.patch.object(
        administration,
        "_prompt",
        side_effect=("yes", "created-password", "created-password"),
    )
    assert (
        main(
            (
                "users",
                "create",
                "--username",
                "cli-created",
                "--require-password-change",
                "--profile",
                "admin",
            )
        )
        == 0
    )
    created_output = capsys.readouterr()
    assert "created-password" not in created_output.out + created_output.err

    assert main(("--json", "users", "list", "--profile", "admin")) == 0
    users = json.loads(capsys.readouterr().out)["users"]
    assert {item["username"] for item in users} == {
        "Admin",
        "alice",
        "cli-created",
        "restricted",
    }

    assert main(("users", "list", "--profile", "alice")) == 1
    assert capsys.readouterr().err == (
        "error: You are not authorized to perform this operation.\n"
    )
    assert main(("users", "list", "--profile", "restricted")) == 1
    assert capsys.readouterr().err == "error: A password change is required.\n"

    assert (
        main(
            (
                "--non-interactive",
                "users",
                "deactivate",
                str(alice.id),
                "--force",
                "--profile",
                "admin",
            )
        )
        == 0
    )
    capsys.readouterr()
    assert _session_status(url, "alice") == (401, "AUTHENTICATION_REQUIRED")
    assert (
        main(
            (
                "--non-interactive",
                "users",
                "activate",
                str(alice.id),
                "--force",
                "--profile",
                "admin",
            )
        )
        == 0
    )
    capsys.readouterr()
    _save_login(url, "alice", "alice", "alice-password")
    assert (
        main(
            (
                "--non-interactive",
                "users",
                "require-password-change",
                str(restricted.id),
                "--clear",
                "--force",
                "--profile",
                "admin",
            )
        )
        == 0
    )
    capsys.readouterr()

    prompt.reset_mock()
    prompt.side_effect = ("yes", "reset-password", "reset-password")
    assert main(("users", "reset-password", str(alice.id), "--profile", "admin")) == 0
    assert prompt.call_count == 3
    output = capsys.readouterr()
    assert "reset-password" not in output.out + output.err
    assert _session_status(url, "alice") == (401, "AUTHENTICATION_REQUIRED")

    assert (
        main(
            (
                "--json",
                "audit",
                "--offset",
                "1",
                "--limit",
                "2",
                "--profile",
                "admin",
            )
        )
        == 0
    )
    page = json.loads(capsys.readouterr().out)
    assert page["offset"] == 1 and page["limit"] == 2
    assert len(page["items"]) == 2
    assert "reset-password" not in json.dumps(page)
    assert all(
        set(item)
        == {
            "id",
            "actor_id",
            "owner_id",
            "operation",
            "target_id",
            "target_type",
            "target_version",
            "version_id",
            "administrator_intervention",
            "created_at",
        }
        for item in page["items"]
    )


def test_public_health_metrics_and_readiness_failure_use_real_http(
    administration_service, mocker, capsys
) -> None:
    url, application = administration_service
    assert main(("--json", "health", "live", "--url", url)) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}

    assert main(("health", "ready", "--url", url)) == 0
    assert capsys.readouterr().out == "Service is ready.\n"

    assert main(("health", "metrics", "--url", url)) == 0
    metrics = capsys.readouterr().out
    assert "md_converter_http_requests_total" in metrics

    mocker.patch.object(
        application.state.components.readiness, "is_ready", return_value=False
    )
    assert main(("--json", "health", "ready", "--url", url)) == 1
    assert json.loads(capsys.readouterr().err) == {
        "error": {"code": "not_ready", "message": "The service is not ready."}
    }
