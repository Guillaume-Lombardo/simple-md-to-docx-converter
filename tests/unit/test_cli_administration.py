"""Unit coverage for the T35 administration, audit, and health CLI family."""

from __future__ import annotations

import argparse
import json
from email.message import Message
from io import BytesIO, StringIO
from typing import Any, cast
from urllib.error import HTTPError, URLError
from uuid import UUID

import pytest

from markweave.cli.commands import administration
from markweave.cli.main import main
from markweave.cli.types import CommandContext, ConnectionProfile, OutputFormat

pytestmark = pytest.mark.unit

ADMIN_ID = "00000000-0000-0000-0000-000000000001"
ALICE_ID = "00000000-0000-0000-0000-000000000002"


def _profile() -> ConnectionProfile:
    return ConnectionProfile(
        "default",
        "https://converter.example",
        "md_converter_session=opaque",
        "csrf-opaque",
    )


def _user(
    identifier: str = ALICE_ID,
    username: str = "alice",
    *,
    active: bool = True,
    renewal: bool = False,
) -> dict[str, object]:
    return {
        "id": identifier,
        "username": username,
        "role": "user",
        "active": active,
        "password_change_required": renewal,
    }


def _response(
    status: int, payload: object = None, text: str = "", etag: str | None = None
):
    return administration._Response(status, payload, text, etag)


@pytest.fixture
def remote(mocker):
    store = mocker.Mock(load=mocker.Mock(return_value=_profile()))
    client = mocker.Mock()
    mocker.patch.object(administration, "ProfileStore", return_value=store)
    constructor = mocker.patch.object(
        administration, "_AdministrationClient", return_value=client
    )
    return store, constructor, client


def test_users_list_has_deterministic_human_and_json_output(remote, capsys) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(200, [_user(ADMIN_ID, "admin"), _user()])

    assert main(("users", "list")) == 0
    assert capsys.readouterr().out == (
        f"{ADMIN_ID}\tadmin\tuser\tactive\tcurrent\n"
        f"{ALICE_ID}\talice\tuser\tactive\tcurrent\n"
    )

    assert main(("--json", "users", "list")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "users": [_user(ADMIN_ID, "admin"), _user()]
    }
    client.request.assert_called_with("GET", "/api/v1/admin/users", profile=_profile())


def test_remote_usernames_are_escaped_in_every_human_output_but_not_json(
    remote, capsys
) -> None:
    _store, _constructor, client = remote
    hostile = "alice\tadmin\n\x1b[31mred\x00"
    payload = _user(username=hostile)
    escaped = r"alice\tadmin\n\u001b[31mred\u0000"

    client.request.return_value = _response(200, [payload])
    assert main(("users", "list")) == 0
    rendered = capsys.readouterr().out
    assert rendered == f"{ALICE_ID}\t{escaped}\tuser\tactive\tcurrent\n"
    assert "\x1b" not in rendered

    client.request.return_value = _response(200, [payload])
    assert main(("--json", "users", "list")) == 0
    assert json.loads(capsys.readouterr().out)["users"][0]["username"] == hostile

    mutations = (
        (("users", "activate", ALICE_ID, "--force"), "active"),
        (
            ("users", "require-password-change", ALICE_ID, "--force"),
            "Password renewal is required",
        ),
    )
    for arguments, expected in mutations:
        client.request.return_value = _response(200, payload)
        assert main(("--non-interactive", *arguments)) == 0
        rendered = capsys.readouterr().out
        assert escaped in rendered and expected in rendered
        assert "\x1b" not in rendered


def test_all_remote_user_fields_are_escaped_in_human_output_but_not_json(
    remote, capsys
) -> None:
    _store, _constructor, client = remote
    hostile_id = "id\tforged"
    hostile_role = "admin\n\x1b[31mforged"
    payload = _user(identifier=hostile_id)
    payload["role"] = hostile_role

    client.request.return_value = _response(200, [payload])
    assert main(("users", "list")) == 0
    rendered = capsys.readouterr().out
    assert rendered == (
        "id\\tforged\talice\tadmin\\n\\u001b[31mforged\tactive\tcurrent\n"
    )
    assert "\x1b" not in rendered

    client.request.return_value = _response(200, [payload])
    assert main(("--json", "users", "list")) == 0
    rendered_json = json.loads(capsys.readouterr().out)["users"][0]
    assert rendered_json["id"] == hostile_id
    assert rendered_json["role"] == hostile_role


def test_created_remote_username_is_escaped_in_human_output(
    remote, mocker, capsys
) -> None:
    _store, _constructor, client = remote
    hostile = "created\r\n\x1b]8;;https://attacker.invalid\x07link"
    client.request.return_value = _response(201, _user(username=hostile))
    mocker.patch.object(
        administration,
        "_prompt",
        side_effect=("yes", "submitted-password", "submitted-password"),
    )

    assert main(("users", "create", "--username", "safe-request")) == 0
    rendered = capsys.readouterr().out
    assert (
        r"Created user created\r\n\u001b]8;;https://attacker.invalid\u0007link."
        in rendered
    )
    assert "\x1b" not in rendered


def test_create_prompts_securely_confirms_and_never_prints_password(
    remote, mocker, capsys
) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(201, _user(renewal=True))
    prompt = mocker.patch.object(
        administration,
        "_prompt",
        side_effect=("yes", "submitted-password", "submitted-password"),
    )

    assert (
        main(
            (
                "--json",
                "users",
                "create",
                "--username",
                "alice",
                "--require-password-change",
            )
        )
        == 0
    )
    assert prompt.call_args_list == [
        mocker.call(mocker.ANY, "Create user 'alice'? [y/N]: ", secret=False),
        mocker.call(mocker.ANY, "New password: ", secret=True),
        mocker.call(mocker.ANY, "Confirm new password: ", secret=True),
    ]
    assert client.request.call_args.kwargs["body"] == {
        "username": "alice",
        "password": "submitted-password",
        "password_change_required": True,
    }
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"user": _user(renewal=True)}
    assert "submitted-password" not in captured.out + captured.err


@pytest.mark.parametrize(
    ("arguments", "method", "path", "body", "payload"),
    (
        (
            ("users", "activate", ALICE_ID, "--force"),
            "PATCH",
            f"/api/v1/admin/users/{ALICE_ID}/active",
            {"active": True},
            _user(),
        ),
        (
            ("users", "deactivate", ALICE_ID, "--force"),
            "PATCH",
            f"/api/v1/admin/users/{ALICE_ID}/active",
            {"active": False},
            _user(active=False),
        ),
        (
            ("users", "require-password-change", ALICE_ID, "--force"),
            "PATCH",
            f"/api/v1/admin/users/{ALICE_ID}/password-change-required",
            {"required": True},
            _user(renewal=True),
        ),
        (
            (
                "users",
                "require-password-change",
                ALICE_ID,
                "--clear",
                "--force",
            ),
            "PATCH",
            f"/api/v1/admin/users/{ALICE_ID}/password-change-required",
            {"required": False},
            _user(),
        ),
    ),
)
def test_noninteractive_force_mutations_preserve_exact_http_contract(  # noqa: PLR0913, PLR0917
    remote, arguments, method, path, body, payload
) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(200, payload)

    assert main(("--non-interactive", *arguments)) == 0
    client.request.assert_called_once_with(
        method, path, profile=_profile(), csrf=True, body=body
    )


def test_reset_password_is_prompt_only_and_output_is_secret_free(
    remote, mocker, capsys
) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(204)
    mocker.patch.object(
        administration,
        "_prompt",
        side_effect=("yes", "new-secret", "new-secret"),
    )

    assert main(("users", "reset-password", ALICE_ID, "--require-password-change")) == 0
    client.request.assert_called_once_with(
        "POST",
        f"/api/v1/admin/users/{ALICE_ID}/password",
        profile=_profile(),
        csrf=True,
        body={"password": "new-secret", "password_change_required": True},
    )
    captured = capsys.readouterr()
    assert "new-secret" not in captured.out + captured.err
    assert "not displayed" in captured.out


def test_mutations_require_confirmation_and_secret_arguments_are_rejected(
    remote, capsys
) -> None:
    _store, constructor, client = remote
    assert main(("--non-interactive", "users", "deactivate", ALICE_ID)) == 1
    assert "Use --force" in capsys.readouterr().err
    constructor.assert_not_called()
    client.request.assert_not_called()

    with pytest.raises(SystemExit) as raised:
        main(
            (
                "users",
                "reset-password",
                ALICE_ID,
                "--password",
                "must-not-leak",
            )
        )
    assert raised.value.code == 2
    assert "must-not-leak" not in capsys.readouterr().err


def test_password_mismatch_stops_before_http(remote, mocker, capsys) -> None:
    _store, constructor, client = remote
    mocker.patch.object(
        administration, "_prompt", side_effect=("yes", "first", "second")
    )
    assert main(("users", "create", "--username", "alice")) == 1
    assert "confirmation does not match" in capsys.readouterr().err
    constructor.assert_not_called()
    client.request.assert_not_called()


def test_audit_pagination_and_json_output_are_exact(remote, capsys) -> None:
    _store, _constructor, client = remote
    record = {
        "id": "00000000-0000-0000-0000-000000000010",
        "actor_id": ADMIN_ID,
        "owner_id": ALICE_ID,
        "operation": "user_deactivated",
        "target_id": ALICE_ID,
        "target_type": "user",
        "target_version": "2",
        "version_id": None,
        "administrator_intervention": True,
        "created_at": "2026-08-30T12:00:00Z",
    }
    normalized = record | {
        "old_user_idle_minutes": None,
        "old_admin_idle_minutes": None,
        "new_user_idle_minutes": None,
        "new_admin_idle_minutes": None,
    }
    client.request.return_value = _response(200, [record])

    assert main(("--json", "audit", "--offset", "2", "--limit", "1")) == 0
    assert json.loads(capsys.readouterr().out) == {
        "items": [normalized],
        "limit": 1,
        "offset": 2,
    }
    client.request.assert_called_once_with(
        "GET", "/api/v1/audit?offset=2&limit=1", profile=_profile()
    )


def test_all_remote_audit_fields_are_escaped_in_human_output_but_not_json(
    remote, capsys
) -> None:
    _store, _constructor, client = remote
    record = {
        "id": "audit-id",
        "actor_id": ADMIN_ID,
        "owner_id": ALICE_ID,
        "operation": "user\tdeactivated",
        "target_id": "target\nforged",
        "target_type": "user\r\x1b[31m",
        "target_version": None,
        "version_id": None,
        "administrator_intervention": False,
        "created_at": "2026-08-30T12:00:00Z\nforged",
    }
    client.request.return_value = _response(200, [record])

    assert main(("audit",)) == 0
    rendered = capsys.readouterr().out
    assert rendered == (
        "2026-08-30T12:00:00Z\\nforged\tuser\\tdeactivated\t"
        "user\\r\\u001b[31m\ttarget\\nforged\towner\n"
    )
    assert "\x1b" not in rendered

    client.request.return_value = _response(200, [record])
    assert main(("--json", "audit")) == 0
    rendered_json = json.loads(capsys.readouterr().out)["items"][0]
    assert rendered_json["created_at"] == record["created_at"]
    assert rendered_json["operation"] == record["operation"]
    assert rendered_json["target_type"] == record["target_type"]
    assert rendered_json["target_id"] == record["target_id"]


def test_session_policy_get_update_preserves_etag_csrf_and_audit_fields(
    remote, capsys
) -> None:
    _store, _constructor, client = remote
    initial = {
        "user_idle_minutes": 30,
        "admin_idle_minutes": 15,
        "revision": 0,
        "absolute_lifetime_seconds": 28_800,
    }
    updated = {
        "user_idle_minutes": 25,
        "admin_idle_minutes": 10,
        "revision": 1,
        "absolute_lifetime_seconds": 28_800,
    }
    client.request.side_effect = (
        _response(200, initial, etag='"idle-session-policy-0"'),
        _response(200, initial, etag='"idle-session-policy-0"'),
        _response(200, updated, etag='"idle-session-policy-1"'),
    )

    assert main(("--json", "session-policy", "get")) == 0
    assert json.loads(capsys.readouterr().out) == {"session_policy": initial}
    assert (
        main(
            (
                "--non-interactive",
                "session-policy",
                "update",
                "--user-idle-minutes",
                "25",
                "--admin-idle-minutes",
                "10",
                "--force",
            )
        )
        == 0
    )
    assert capsys.readouterr().out == (
        "Users: 25 minutes; administrators: 10 minutes; absolute lifetime: "
        "28800 seconds; revision: 1.\n"
    )
    update = client.request.call_args_list[-1]
    assert update.args == ("PUT", "/api/v1/admin/session-policy")
    assert update.kwargs == {
        "profile": _profile(),
        "csrf": True,
        "if_match": '"idle-session-policy-0"',
        "body": {"user_idle_minutes": 25, "admin_idle_minutes": 10},
    }

    audit = {
        "id": "audit-id",
        "actor_id": ADMIN_ID,
        "owner_id": ADMIN_ID,
        "operation": "idle_session_policy_update",
        "target_id": "00000000-0000-0000-0000-000000000001",
        "target_type": "session_policy",
        "target_version": "1",
        "version_id": None,
        "administrator_intervention": True,
        "created_at": "2026-09-01T12:00:00Z",
        "old_user_idle_minutes": 30,
        "old_admin_idle_minutes": 15,
        "new_user_idle_minutes": 25,
        "new_admin_idle_minutes": 10,
    }
    assert administration._audit_record(audit) == audit


def test_session_policy_help_exposes_http_only_read_and_update(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(("session-policy", "--help"))
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "get" in output
    assert "update" in output
    assert "role-specific idle-session" in output


@pytest.mark.parametrize(
    "payload",
    (
        None,
        {},
        {"user_idle_minutes": True, "admin_idle_minutes": 15, "revision": 0},
        {"user_idle_minutes": 30, "admin_idle_minutes": "15", "revision": 0},
        {"user_idle_minutes": 30, "admin_idle_minutes": 15, "revision": False},
    ),
)
def test_session_policy_rejects_malformed_service_responses(
    remote, capsys, payload: Any
) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(
        200, payload, etag='"idle-session-policy-0"'
    )

    assert main(("session-policy", "get")) == 1
    assert "invalid response" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("operation", None),
        ("administrator_intervention", "yes"),
        ("target_version", 1),
        ("new_user_idle_minutes", True),
        ("new_admin_idle_minutes", "10"),
    ),
)
def test_policy_audit_rejects_malformed_service_fields(field: str, value: Any) -> None:
    record = {
        "id": "audit-id",
        "actor_id": ADMIN_ID,
        "owner_id": ADMIN_ID,
        "operation": "idle_session_policy_update",
        "target_id": "00000000-0000-0000-0000-000000000001",
        "target_type": "session_policy",
        "target_version": "1",
        "version_id": None,
        "administrator_intervention": True,
        "created_at": "2026-09-01T12:00:00Z",
        "old_user_idle_minutes": 30,
        "old_admin_idle_minutes": 15,
        "new_user_idle_minutes": 25,
        "new_admin_idle_minutes": 10,
    }
    record[field] = value

    with pytest.raises(administration.CliError, match="invalid response"):
        administration._audit_record(record)


def test_policy_audit_requires_an_object() -> None:
    with pytest.raises(administration.CliError, match="invalid response"):
        administration._audit_record([])


def test_policy_update_rejects_a_missing_etag_without_mutation(remote, capsys) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(
        200, {"user_idle_minutes": 30, "admin_idle_minutes": 15, "revision": 0}
    )

    assert (
        main(
            (
                "--non-interactive",
                "session-policy",
                "update",
                "--user-idle-minutes",
                "25",
                "--admin-idle-minutes",
                "10",
                "--force",
            )
        )
        == 1
    )
    assert "invalid response" in capsys.readouterr().err
    client.request.assert_called_once()


def test_policy_response_requires_an_object() -> None:
    with pytest.raises(administration.CliError, match="invalid response"):
        administration._session_policy([])


def test_policy_error_falls_back_for_a_malformed_error_envelope() -> None:
    error = administration._api_error(
        administration._Response(400, {"error": []}, "", None), "policy_failed"
    )
    assert error.code == "policy_failed"


def test_human_policy_audit_rejects_non_text_remote_fields() -> None:
    with pytest.raises(administration.CliError, match="invalid response"):
        administration._human_text(1)


def test_health_url_is_public_and_metrics_support_human_and_json(
    remote, capsys
) -> None:
    store, constructor, client = remote
    client.request.side_effect = (
        _response(200, {"status": "ok"}, '{"status":"ok"}'),
        _response(200, None, "metric_a 1\nmetric_b 2\n"),
        _response(200, None, "metric_a 1\n"),
    )

    assert main(("health", "live", "--url", "https://public.example")) == 0
    assert capsys.readouterr().out == "Service is ok.\n"
    store.load.assert_not_called()
    constructor.assert_called_with("https://public.example", timeout=None)
    assert client.request.call_args_list[0].args == ("GET", "/health/live")

    assert main(("health", "metrics", "--url", "https://public.example")) == 0
    assert capsys.readouterr().out == "metric_a 1\nmetric_b 2\n"
    assert main(("--json", "health", "metrics", "--url", "https://public.example")) == 0
    assert json.loads(capsys.readouterr().out) == {"metrics": "metric_a 1"}


def test_readiness_and_restricted_session_errors_are_sanitized(remote, capsys) -> None:
    _store, _constructor, client = remote
    client.request.side_effect = (
        _response(
            503,
            {"error": {"code": "NOT_READY", "message": "The service is not ready."}},
        ),
        _response(
            403,
            {
                "error": {
                    "code": "PASSWORD_CHANGE_REQUIRED",
                    "message": "Password renewal is required.",
                }
            },
        ),
    )
    assert main(("--json", "health", "ready")) == 1
    error = json.loads(capsys.readouterr().err)
    assert error == {
        "error": {"code": "not_ready", "message": "The service is not ready."}
    }

    assert main(("users", "list")) == 1
    captured = capsys.readouterr()
    assert captured.err == "error: Password renewal is required.\n"
    assert "traceback" not in captured.err.casefold()


def test_invalid_user_identifier_fails_before_profile_or_http(remote, capsys) -> None:
    store, constructor, client = remote
    assert main(("users", "activate", "not-a-uuid", "--force")) == 1
    assert capsys.readouterr().err == "error: The user identifier is invalid.\n"
    store.load.assert_not_called()
    constructor.assert_not_called()
    client.request.assert_not_called()
    assert UUID(ALICE_ID)


def test_malformed_remote_contracts_fail_closed(remote, capsys) -> None:
    _store, _constructor, client = remote
    client.request.side_effect = (
        _response(200, {"unexpected": "value"}),
        _response(200, None, ""),
        _response(200, {"not": "a list"}),
        _response(502, "untrusted upstream body"),
    )

    assert main(("health", "live")) == 1
    assert capsys.readouterr().err == (
        "error: The service returned an invalid response.\n"
    )
    assert main(("health", "metrics")) == 1
    assert capsys.readouterr().err == (
        "error: The service returned an invalid response.\n"
    )
    assert main(("users", "list")) == 1
    assert capsys.readouterr().err == (
        "error: The service returned an invalid response.\n"
    )
    assert main(("users", "list")) == 1
    captured = capsys.readouterr()
    assert captured.err == "error: The service rejected the request.\n"
    assert "untrusted" not in captured.err


@pytest.mark.parametrize(
    "payload",
    (
        ["not-an-object"],
        [{"id": ALICE_ID}],
    ),
)
def test_invalid_user_items_are_not_partially_rendered(remote, payload, capsys) -> None:
    _store, _constructor, client = remote
    client.request.return_value = _response(200, payload)

    assert main(("users", "list")) == 1
    assert capsys.readouterr().out == ""


def test_prompt_and_confirmation_boundaries_are_explicit(mocker, monkeypatch) -> None:
    context = CommandContext(OutputFormat.HUMAN, False, None)
    non_interactive = CommandContext(OutputFormat.HUMAN, True, None)

    with pytest.raises(administration.CliError, match="interactive input"):
        administration._prompt(non_interactive, "Value: ", secret=False)
    with pytest.raises(administration.CliError, match="secure interactive terminal"):
        administration._prompt(context, "Value: ", secret=False)

    class _TTY(StringIO):
        def isatty(self) -> bool:
            return True

    stdin = _TTY("yes\n")
    stderr = _TTY()
    monkeypatch.setattr(administration.sys, "stdin", stdin)
    monkeypatch.setattr(administration.sys, "stderr", stderr)
    assert administration._prompt(context, "Confirm: ", secret=False) == "yes"
    assert stderr.getvalue() == "Confirm: "

    getpass = mocker.patch.object(
        administration.getpass, "getpass", return_value="secret"
    )
    assert administration._prompt(context, "Secret: ", secret=True) == "secret"
    getpass.assert_called_once_with("Secret: ")
    getpass.side_effect = administration.getpass.GetPassWarning("unsafe")
    with pytest.raises(administration.CliError) as insecure:
        administration._prompt(context, "Secret: ", secret=True)
    assert insecure.value.code == "interactive_tty_required"
    assert insecure.value.message == "A secure interactive terminal is required."

    getpass.side_effect = EOFError
    with pytest.raises(administration.CliError) as exhausted:
        administration._prompt(context, "Secret: ", secret=True)
    assert exhausted.value.code == "input_required"
    assert exhausted.value.message == "A non-empty value is required."

    mocker.patch.object(administration, "_prompt", return_value="no")
    with pytest.raises(administration.CliError, match="cancelled"):
        administration._confirm(context, administration._Command("test"), "Proceed?")


def test_response_and_argument_validation_failure_branches_are_safe() -> None:
    fallback = administration._api_error(
        _response(500, {"error": {"code": 1, "message": []}}), "fallback"
    )
    assert fallback.code == "fallback"
    assert "rejected" in fallback.message

    with pytest.raises(administration.CliError, match="too large"):
        administration._decode_response(
            200,
            cast(Any, BytesIO(b"x" * (administration._MAX_RESPONSE_BYTES + 1))),
        )
    with pytest.raises(administration.CliError, match="invalid response"):
        administration._decode_response(200, cast(Any, BytesIO(b"\xff")))
    assert administration._decode_response(204, cast(Any, BytesIO(b""))) == _response(
        204, None, ""
    )
    assert administration._decode_response(
        502, cast(Any, BytesIO(b"not-json"))
    ) == _response(502, None, "not-json")

    invalid_audit_records = (
        "not-an-object",
        {"id": "missing-fields"},
        {
            "id": "audit-id",
            "actor_id": ADMIN_ID,
            "owner_id": ALICE_ID,
            "operation": "created",
            "target_id": ALICE_ID,
            "target_type": "user",
            "created_at": "2026-08-30T12:00:00Z",
            "administrator_intervention": "yes",
        },
        {
            "id": "audit-id",
            "actor_id": ADMIN_ID,
            "owner_id": ALICE_ID,
            "operation": "created",
            "target_id": ALICE_ID,
            "target_type": "user",
            "created_at": "2026-08-30T12:00:00Z",
            "administrator_intervention": True,
            "target_version": 1,
        },
    )
    for record in invalid_audit_records:
        with pytest.raises(administration.CliError, match="invalid response"):
            administration._audit_record(record)

    with pytest.raises(administration.CliError, match="invalid"):
        administration._string(administration._Command("test"), "missing")
    with pytest.raises(administration.CliError, match="invalid"):
        administration._integer(
            administration._Command("test", {"value": True}), "value"
        )
    with pytest.raises(argparse.ArgumentTypeError, match="non-negative"):
        administration._nonnegative("-1")
    with pytest.raises(argparse.ArgumentTypeError, match="between 1 and 100"):
        administration._audit_limit("101")


def test_administration_client_sends_profile_csrf_and_json_body(mocker) -> None:
    response = mocker.Mock(status=200)
    response.read.return_value = b'{"status":"ok"}'
    opener = mocker.Mock()
    opener.open.return_value = response
    build_opener = mocker.patch.object(
        administration, "build_opener", return_value=opener
    )
    context = mocker.patch.object(
        administration.ssl, "create_default_context", return_value=mocker.sentinel.tls
    )
    client = administration._AdministrationClient(
        "https://converter.example", timeout=2.5
    )

    result = client.request(
        "PATCH",
        "/api/v1/admin/users/id",
        profile=_profile(),
        csrf=True,
        if_match='"idle-session-policy-7"',
        body={"active": False},
    )

    assert result == _response(200, {"status": "ok"}, '{"status":"ok"}')
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://converter.example/api/v1/admin/users/id"
    assert request.method == "PATCH"
    assert request.data == b'{"active":false}'
    assert dict(request.header_items()) == {
        "Accept": "application/json",
        "Content-type": "application/json",
        "Cookie": "md_converter_session=opaque",
        "If-match": '"idle-session-policy-7"',
        "X-csrf-token": "csrf-opaque",
    }
    opener.open.assert_called_once_with(request, timeout=2.5)
    context.assert_called_once_with()
    assert len(build_opener.call_args.args) == 2
    response.close.assert_called_once_with()


def test_administration_client_omits_csrf_without_mutation(mocker) -> None:
    response = mocker.Mock(status=204)
    response.read.return_value = b""
    opener = mocker.Mock()
    opener.open.return_value = response
    mocker.patch.object(administration, "build_opener", return_value=opener)
    mocker.patch.object(administration.ssl, "create_default_context")
    client = administration._AdministrationClient(
        "https://converter.example", timeout=None
    )

    assert client.request("GET", "/api/v1/audit", profile=_profile()) == _response(
        204, None, ""
    )
    request = opener.open.call_args.args[0]
    assert request.get_header("Cookie") == "md_converter_session=opaque"
    assert request.get_header("X-csrf-token") is None
    assert request.data is None


def test_administration_client_disables_http_proxy_and_returns_http_error(
    mocker,
) -> None:
    error = HTTPError(
        "http://127.0.0.1:8000/health/ready",
        503,
        "Unavailable",
        Message(),
        BytesIO(b'{"error":{"code":"NOT_READY","message":"not ready"}}'),
    )
    opener = mocker.Mock()
    opener.open.side_effect = error
    build_opener = mocker.patch.object(
        administration, "build_opener", return_value=opener
    )
    mocker.patch.object(administration.ssl, "create_default_context")
    client = administration._AdministrationClient("http://127.0.0.1:8000", timeout=None)

    result = client.request("GET", "/health/ready")

    assert result.status == 503
    assert result.payload["error"]["code"] == "NOT_READY"
    assert any(
        isinstance(handler, administration.ProxyHandler)
        for handler in build_opener.call_args.args
    )
    assert error.fp.closed


def test_administration_client_maps_url_errors_to_sanitized_cli_error(mocker) -> None:
    opener = mocker.Mock()
    opener.open.side_effect = URLError("private transport detail")
    mocker.patch.object(administration, "build_opener", return_value=opener)
    mocker.patch.object(administration.ssl, "create_default_context")
    client = administration._AdministrationClient(
        "https://converter.example", timeout=None
    )

    with pytest.raises(administration.CliError) as raised:
        client.request("GET", "/health/live")

    assert raised.value.code == "network_error"
    assert raised.value.message == "The service could not be reached."
