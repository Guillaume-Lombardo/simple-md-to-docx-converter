"""Unit coverage for the T32 authentication command family."""

from __future__ import annotations

import json

import pytest

from markweave.cli.commands import authentication
from markweave.cli.http import ApiResponse
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def secure_tty(mocker) -> None:
    """Exercise prompt behavior through the secure terminal branch."""
    mocker.patch.object(authentication, "_secure_tty_available", return_value=True)


def _profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="default",
        service_url="https://converter.example",
        session_state="md_converter_session=old-session",
        csrf_state="old-csrf",
    )


def _login_response(*, renewal: bool = False) -> ApiResponse:
    return ApiResponse(
        200,
        {
            "csrf_token": "new-csrf",
            "user": {"username": "alice", "password_change_required": renewal},
        },
        "md_converter_session=new-session",
    )


def test_login_prompts_without_echo_and_persists_only_session(mocker, capsys) -> None:
    """Login has no password argument and only saves returned opaque state."""
    store = mocker.Mock()
    transport = mocker.Mock(login=mocker.Mock(return_value=_login_response()))
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    prompt = mocker.patch.object(
        authentication.getpass, "getpass", return_value="password"
    )

    assert (
        main(
            (
                "--json",
                "login",
                "--url",
                "https://converter.example",
                "--username",
                "alice",
            )
        )
        == 0
    )
    assert prompt.call_args.args == ("Password: ",)
    assert transport.login.call_args.args == ("alice", "password")
    assert transport.login.call_args.kwargs == {"previous_profile": None}
    saved = store.save.call_args.args[0]
    assert saved.session_state == "md_converter_session=new-session"
    assert saved.csrf_state == "new-csrf"
    assert "password" not in json.dumps(capsys.readouterr().out)

    with pytest.raises(SystemExit) as raised:
        main(("login", "--url", "https://converter.example", "--password", "secret"))
    assert raised.value.code == 2
    assert "secret" not in capsys.readouterr().err


def test_non_interactive_login_and_password_change_fail_before_prompt(
    mocker, capsys
) -> None:
    """Automation cannot accidentally read secrets from a terminal or environment."""
    prompt = mocker.patch.object(authentication.getpass, "getpass")
    assert (
        main(("--non-interactive", "login", "--url", "https://converter.example")) == 1
    )
    assert main(("--non-interactive", "password", "change")) == 1
    assert prompt.call_count == 0
    assert "interactive input" in capsys.readouterr().err


def test_piped_prompt_is_rejected_without_calling_getpass(mocker, capsys) -> None:
    """The stdlib fallback cannot read or echo a password from a pipe."""
    mocker.patch.object(authentication, "_secure_tty_available", return_value=False)
    prompt = mocker.patch.object(authentication.getpass, "getpass")
    assert main(("login", "--url", "https://converter.example")) == 1
    assert prompt.call_count == 0
    assert (
        capsys.readouterr().err == "error: A secure interactive terminal is required.\n"
    )


def test_relogin_reuses_only_the_same_service_profile(mocker) -> None:
    """A same-service re-login carries the prior cookie for server-side rotation."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(login=mocker.Mock(return_value=_login_response()))
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    mocker.patch.object(authentication.getpass, "getpass", return_value="password")
    assert (
        main(
            (
                "login",
                "--url",
                "https://converter.example",
                "--username",
                "alice",
            )
        )
        == 0
    )
    assert transport.login.call_args.kwargs == {"previous_profile": _profile()}


def test_whoami_reports_only_safe_session_fields(mocker, capsys) -> None:
    """Session inspection neither emits cookies nor CSRF state."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(
        session=mocker.Mock(
            return_value=ApiResponse(
                200,
                {"username": "alice", "role": "user", "password_change_required": True},
            )
        )
    )
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    assert main(("--json", "whoami")) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "password_change_required": True,
        "profile": "default",
        "role": "user",
        "username": "alice",
    }
    assert "old-session" not in output
    assert "old-csrf" not in output


def test_logout_deletes_expired_or_successful_profile(mocker, capsys) -> None:
    """A server-expired session is cleaned up locally as well."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(logout=mocker.Mock(return_value=ApiResponse(401, None)))
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    assert main(("logout",)) == 0
    store.delete.assert_called_once_with("default")
    assert capsys.readouterr().out == "Signed out.\n"


def test_password_change_reauthenticates_restricted_session_and_forgets_profile(
    mocker, capsys
) -> None:
    """Renewal uses the existing CSRF endpoint, then demands fresh login."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(
        session=mocker.Mock(
            return_value=ApiResponse(
                200,
                {"username": "alice", "role": "user", "password_change_required": True},
            )
        ),
        login=mocker.Mock(return_value=_login_response(renewal=True)),
        change_password=mocker.Mock(return_value=ApiResponse(204, None)),
    )
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    mocker.patch.object(
        authentication.getpass,
        "getpass",
        side_effect=("current", "new", "new"),
    )
    assert main(("password", "change")) == 0
    assert transport.login.call_args.args == ("alice", "current")
    renewal_profile = transport.change_password.call_args.args[0]
    assert renewal_profile.session_state == "md_converter_session=new-session"
    assert renewal_profile.csrf_state == "new-csrf"
    store.delete.assert_called_once_with("default")
    assert capsys.readouterr().out == "Password changed. Sign in again.\n"


def test_password_change_rejects_mismatch_without_a_network_request(
    mocker, capsys
) -> None:
    """A mismatched confirmation leaves the stored restricted session intact."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(
        session=mocker.Mock(
            return_value=ApiResponse(
                200,
                {"username": "alice", "role": "user", "password_change_required": True},
            )
        )
    )
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    mocker.patch.object(
        authentication.getpass,
        "getpass",
        side_effect=("current", "new", "different"),
    )
    assert main(("password", "change")) == 1
    assert transport.login.call_count == 0
    assert store.delete.call_count == 0
    assert (
        capsys.readouterr().err
        == "error: The new password confirmation does not match.\n"
    )


def test_server_error_envelopes_are_safe(mocker, capsys) -> None:
    """Remote errors retain stable code/message but never local profile values."""
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    transport = mocker.Mock(
        session=mocker.Mock(
            return_value=ApiResponse(
                401,
                {"error": {"code": "SESSION_INVALID", "message": "Sign in again."}},
            )
        )
    )
    mocker.patch.object(authentication, "ProfileStore", return_value=store)
    mocker.patch.object(authentication, "HttpTransport", return_value=transport)
    assert main(("--json", "whoami")) == 1
    assert capsys.readouterr().err == (
        '{"error":{"code":"session_invalid","message":"Sign in again."}}\n'
    )
    store.delete.assert_called_once_with("default")
