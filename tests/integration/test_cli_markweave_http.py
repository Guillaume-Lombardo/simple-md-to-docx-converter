"""CLI transport coverage against a running Markweave ASGI application."""

from __future__ import annotations

import socket
from pathlib import Path
from threading import Thread
from time import sleep

import pytest
import uvicorn

from markweave.app import create_app
from markweave.auth.models import normalize_username
from markweave.cli.commands import authentication as cli_authentication
from markweave.cli.http import HttpTransport
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile
from markweave.config import Settings
from tests.settings import template_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def running_markweave(tmp_path: Path):
    """Expose the real application over loopback TCP, not an in-process client."""
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
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
    )
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}", application
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_cli_transport_login_rotation_session_and_logout(running_markweave) -> None:
    """The real Markweave contract rotates, inspects, and revokes CLI sessions."""
    running_url, _application = running_markweave
    transport = HttpTransport(running_url, verify_tls=False, timeout=2)
    first = transport.login("admin", "admin-password")
    assert (
        first.status == 200 and first.session is not None and first.payload is not None
    )
    first_profile = ConnectionProfile(
        "default", running_url, first.session, first.payload["csrf_token"]
    )
    rotated = transport.login("admin", "admin-password", previous_profile=first_profile)
    assert (
        rotated.status == 200
        and rotated.session is not None
        and rotated.payload is not None
    )
    rotated_profile = ConnectionProfile(
        "default", running_url, rotated.session, rotated.payload["csrf_token"]
    )
    assert transport.session(first_profile).status == 401
    assert transport.session(rotated_profile).status == 200
    assert transport.logout(rotated_profile).status == 204
    assert transport.session(rotated_profile).status == 401


def test_real_app_restricted_renewal_preserves_failure_then_revokes_success(
    running_markweave,
) -> None:
    """A failed renewal keeps its restricted session; success requires fresh login."""
    running_url, application = running_markweave
    authentication = application.state.components.authentication
    admin = authentication.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    authentication.create_user(
        admin, "renewal-user", "temporary-password", password_change_required=True
    )
    transport = HttpTransport(running_url, verify_tls=False, timeout=2)
    login = transport.login("renewal-user", "temporary-password")
    assert (
        login.status == 200 and login.session is not None and login.payload is not None
    )
    profile = ConnectionProfile(
        "renewal", running_url, login.session, login.payload["csrf_token"]
    )
    assert transport.change_password(profile, "new-password", "different").status == 422
    assert transport.session(profile).status == 200
    assert (
        transport.change_password(profile, "new-password", "new-password").status == 204
    )
    assert transport.session(profile).status == 401
    assert transport.login("renewal-user", "temporary-password").status == 401
    assert transport.login("renewal-user", "new-password").status == 200


def test_delivered_password_change_command_renews_against_real_uvicorn(
    running_markweave, monkeypatch, mocker, tmp_path: Path, capsys
) -> None:
    """The installed command path prompts, renews, removes state, and requires login."""
    running_url, application = running_markweave
    authentication = application.state.components.authentication
    admin = authentication.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    authentication.create_user(
        admin, "cli-renewal", "temporary-password", password_change_required=True
    )
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    mocker.patch.object(cli_authentication, "_secure_tty_available", return_value=True)
    prompts = mocker.patch.object(
        cli_authentication.getpass,
        "getpass",
        side_effect=(
            "temporary-password",
            "temporary-password",
            "new-password",
            "new-password",
        ),
    )

    assert main(("login", "--url", running_url, "--username", "cli-renewal")) == 0
    assert main(("whoami",)) == 0
    assert main(("password", "change")) == 0
    assert prompts.call_args_list == [
        mocker.call("Password: "),
        mocker.call("Current password: "),
        mocker.call("New password: "),
        mocker.call("Confirm new password: "),
    ]
    assert not (tmp_path / "state" / "markweave" / "profiles" / "default.json").exists()
    assert main(("whoami",)) == 1
    assert main(("login", "--url", running_url, "--username", "cli-renewal")) == 1

    mocker.patch.object(
        cli_authentication.getpass, "getpass", return_value="new-password"
    )
    assert main(("login", "--url", running_url, "--username", "cli-renewal")) == 0
    output = capsys.readouterr()
    assert "temporary-password" not in output.out + output.err
    assert "new-password" not in output.out + output.err


def test_delivered_password_change_mismatch_and_reauthentication_failure_keep_profile(
    running_markweave, monkeypatch, mocker, tmp_path: Path
) -> None:
    """Local mismatch and remote current-password rejection preserve recoverable state."""
    running_url, application = running_markweave
    authentication = application.state.components.authentication
    admin = authentication.users.get_by_normalized_username(normalize_username("Admin"))
    assert admin is not None
    authentication.create_user(
        admin, "cli-failure", "temporary-password", password_change_required=True
    )
    profile_path = tmp_path / "state" / "markweave" / "profiles" / "default.json"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    mocker.patch.object(cli_authentication, "_secure_tty_available", return_value=True)
    prompt = mocker.patch.object(
        cli_authentication.getpass,
        "getpass",
        side_effect=("temporary-password", "current", "new", "different"),
    )
    assert main(("login", "--url", running_url, "--username", "cli-failure")) == 0
    assert main(("password", "change")) == 1
    assert profile_path.exists()

    prompt.side_effect = ("wrong-current", "new-password", "new-password")
    assert main(("password", "change")) == 1
    assert profile_path.exists()
    assert main(("whoami",)) == 0
