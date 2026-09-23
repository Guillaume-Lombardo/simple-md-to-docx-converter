"""Behavior and secret-safety coverage for Composer connection CLI parity."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from markweave.cli.commands import composer
from markweave.cli.commands.conversion_http import ConversionHttpResponse
from markweave.cli.main import main
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit

CONNECTION_ID = "00000000-0000-4000-8000-000000000101"
USER_ID = "00000000-0000-4000-8000-000000000202"
DRAFT_ID = "00000000-0000-4000-8000-000000000303"
PROPOSAL_ID = "00000000-0000-4000-8000-000000000404"
REVISION_ID = "00000000-0000-4000-8000-000000000505"
STEP_ID = "00000000-0000-4000-8000-000000000606"


def _connection(**overrides: object) -> dict[str, object]:
    return {
        "allowed_user_ids": [],
        "authorized": True,
        "client_certificate_present": False,
        "credential_present": False,
        "enabled": True,
        "endpoint": "https://llm.example/v1",
        "etag": '"composer-connection-1"',
        "id": CONNECTION_ID,
        "identity_mode": "individual",
        "internal_ca_present": False,
        "name": "Primary",
        "permitted_models": ["small-model"],
        "scope": "personal",
        "selected_model": "small-model",
        "status": "ready",
        "status_message": None,
        **overrides,
    }


def _model_step(**overrides: object) -> dict[str, object]:
    return {
        "id": STEP_ID,
        "draft_id": DRAFT_ID,
        "connection_id": CONNECTION_ID,
        "model_identity": "small-model",
        "base_version": 2,
        "status": "running",
        "proposal_id": None,
        "error_code": None,
        "created_at": "2026-09-23T00:00:00Z",
        "updated_at": "2026-09-23T00:00:00Z",
        **overrides,
    }


@pytest.fixture
def remote(mocker):
    profile = ConnectionProfile(
        "default", "https://markweave.example", "session=opaque", "csrf-opaque"
    )
    store = mocker.Mock(load=mocker.Mock(return_value=profile))
    client = mocker.Mock()
    mocker.patch.object(composer, "ProfileStore", return_value=store)
    constructor = mocker.patch.object(
        composer, "ConversionHttpClient", return_value=client
    )
    return profile, constructor, client


def test_capabilities_and_list_use_authenticated_safe_http(remote, capsys) -> None:
    profile, constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(
            200,
            {
                "instance_connections_manageable": False,
                "personal_connections_allowed": True,
                "status": "ready",
                "status_message": None,
            },
        ),
        ConversionHttpResponse(
            200, {"connections": [_connection()], "limit": 50, "offset": 0}
        ),
    )

    assert main(("--json", "composer", "capabilities")) == 0
    assert json.loads(capsys.readouterr().out)["capabilities"]["status"] == "ready"
    assert main(("composer", "connections", "list")) == 0
    assert "Primary\tpersonal\tready" in capsys.readouterr().out
    assert constructor.call_args.args == (profile,)
    assert client.request.call_args_list[0].args == (
        "GET",
        "/api/v1/composer/capabilities",
    )
    assert client.request.call_args_list[1].args == (
        "GET",
        "/api/v1/composer/connections?offset=0&limit=50",
    )


def test_create_can_write_prompted_secrets_without_argv_or_output_disclosure(
    remote, mocker, capsys, tmp_path
) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": 4096}),
        ConversionHttpResponse(201, _connection(credential_present=False)),
        ConversionHttpResponse(
            200,
            _connection(
                credential_present=True,
                client_certificate_present=True,
                internal_ca_present=True,
            ),
        ),
    )
    mocker.patch.object(composer.sys.stdin, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=True)
    prompt = mocker.patch.object(composer.getpass, "getpass", return_value="api-secret")
    paths = {}
    for name, value in (
        (
            "client-certificate",
            "-----BEGIN CERTIFICATE-----\ncertificate-secret\n-----END CERTIFICATE-----\n",
        ),
        (
            "client-private-key",
            "-----BEGIN PRIVATE KEY-----\nkey-secret\n-----END PRIVATE KEY-----\n",
        ),
        (
            "internal-ca",
            "-----BEGIN CERTIFICATE-----\nca-secret\n-----END CERTIFICATE-----\n",
        ),
    ):
        path = tmp_path / f"{name}.pem"
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        paths[name] = (path, value)

    assert (
        main(
            (
                "--json",
                "composer",
                "connections",
                "create",
                "--name",
                "Primary",
                "--endpoint",
                "https://llm.example/v1",
                "--scope",
                "personal",
                "--identity-mode",
                "individual",
                "--permitted-model",
                "small-model",
                "--with-credentials",
                "--client-certificate-file",
                str(paths["client-certificate"][0]),
                "--client-private-key-file",
                str(paths["client-private-key"][0]),
                "--internal-ca-file",
                str(paths["internal-ca"][0]),
            )
        )
        == 0
    )
    assert prompt.call_count == 1
    capabilities_call, create_call, credential_call = client.request.call_args_list
    assert capabilities_call.args == ("GET", "/api/v1/composer/capabilities")
    assert create_call.args == ("POST", "/api/v1/composer/connections")
    assert create_call.kwargs["csrf"] is True
    assert json.loads(create_call.kwargs["body"]) == {
        "allowed_user_ids": [],
        "enabled": False,
        "endpoint": "https://llm.example/v1",
        "identity_mode": "individual",
        "name": "Primary",
        "permitted_models": ["small-model"],
        "scope": "personal",
        "selected_model": None,
    }
    assert json.loads(credential_call.kwargs["body"]) == {
        "api_key": "api-secret",
        "client_certificate": paths["client-certificate"][1],
        "client_private_key": paths["client-private-key"][1],
        "internal_ca": paths["internal-ca"][1],
    }
    rendered = capsys.readouterr().out
    assert all(
        secret not in rendered for secret in ("api-secret", "key-secret", "ca-secret")
    )


def test_secret_arguments_are_rejected_without_echoing_values(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            (
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--api-key",
                "must-not-appear",
            )
        )
    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert "must-not-appear" not in captured.out + captured.err
    assert "secure prompt" in captured.err


def test_personal_shared_identity_is_rejected_before_network(remote, capsys) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "composer",
                "connections",
                "create",
                "--name",
                "Invalid",
                "--endpoint",
                "https://llm.example/v1",
                "--scope",
                "personal",
                "--identity-mode",
                "shared",
            )
        )
        == 1
    )
    assert "require individual identity" in capsys.readouterr().err
    client.request.assert_not_called()


def test_unsafe_credential_file_and_missing_create_flag_fail_before_http(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    secret = tmp_path / "private.pem"
    secret.write_text("-----BEGIN CERTIFICATE-----\nopaque\n", encoding="utf-8")
    secret.chmod(0o644)
    base = (
        "composer",
        "connections",
        "create",
        "--name",
        "Private",
        "--endpoint",
        "https://llm.example/v1",
        "--scope",
        "personal",
        "--identity-mode",
        "individual",
        "--client-certificate-file",
        str(secret),
    )
    assert main(base) == 1
    assert "require --with-credentials" in capsys.readouterr().err
    assert main(("--non-interactive", *base, "--with-credentials")) == 1
    assert "accessible only by the current user" in capsys.readouterr().err
    client.request.assert_not_called()


def test_noninteractive_private_pem_file_rotation_uses_no_terminal(
    remote, tmp_path, mocker, capsys
) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": 4096}),
        ConversionHttpResponse(200, _connection()),
    )
    certificate = tmp_path / "client.pem"
    value = "-----BEGIN CERTIFICATE-----\nmultiline\n-----END CERTIFICATE-----\n"
    certificate.write_text(value, encoding="utf-8")
    certificate.chmod(0o600)
    prompt = mocker.patch.object(composer.getpass, "getpass")
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
                "--client-certificate-file",
                str(certificate),
            )
        )
        == 0
    )
    prompt.assert_not_called()
    assert json.loads(client.request.call_args_list[1].kwargs["body"]) == {
        "client_certificate": value
    }
    assert value not in capsys.readouterr().out


@pytest.mark.parametrize(
    "case",
    ((8, "12345678", True), (8, "123456789", False), (None, "12345678", False)),
)
def test_credential_file_uses_authoritative_limit_and_fails_closed(
    remote, tmp_path, capsys, case
) -> None:
    service_limit, value, expected_success = case
    _profile, _constructor, client = remote
    key = tmp_path / "api-key"
    key.write_text(value, encoding="utf-8")
    key.chmod(0o600)
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": service_limit}),
        ConversionHttpResponse(200, _connection()),
    )
    result = main(
        (
            "--non-interactive",
            "composer",
            "connections",
            "credentials",
            "rotate",
            CONNECTION_ID,
            "--etag",
            '"composer-connection-1"',
            "--api-key-file",
            str(key),
        )
    )
    assert (result == 0) is expected_success
    assert client.request.call_args_list[0].args == (
        "GET",
        "/api/v1/composer/capabilities",
    )
    if expected_success:
        assert json.loads(client.request.call_args_list[1].kwargs["body"]) == {
            "api_key": value
        }
    else:
        assert client.request.call_count == 1
    assert value not in capsys.readouterr().out


def test_create_prompt_failure_sends_no_metadata(remote, capsys, mocker) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "connections",
                "create",
                "--name",
                "Primary",
                "--endpoint",
                "https://llm.example/v1",
                "--scope",
                "personal",
                "--identity-mode",
                "individual",
                "--with-credentials",
            )
        )
        == 1
    )
    assert "secure prompt or file" in capsys.readouterr().err
    client.request.assert_not_called()
    mocker.patch.object(composer.sys.stdin, "isatty", return_value=False)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=False)
    assert (
        main(
            (
                "composer",
                "connections",
                "create",
                "--name",
                "Primary",
                "--endpoint",
                "https://llm.example/v1",
                "--scope",
                "personal",
                "--identity-mode",
                "individual",
                "--with-credentials",
            )
        )
        == 1
    )
    assert "Enter at least one credential" in capsys.readouterr().err
    client.request.assert_not_called()


def test_create_credential_failure_reports_disabled_connection_for_recovery(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    key = tmp_path / "key"
    key.write_text("secret-value", encoding="utf-8")
    key.chmod(0o600)
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": 4096}),
        ConversionHttpResponse(201, _connection(enabled=False)),
        ConversionHttpResponse(
            503, {"error": {"code": "DOWN", "message": "Unavailable."}}
        ),
    )
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "connections",
                "create",
                "--name",
                "Primary",
                "--endpoint",
                "https://llm.example/v1",
                "--scope",
                "personal",
                "--identity-mode",
                "individual",
                "--with-credentials",
                "--api-key-file",
                str(key),
            )
        )
        == 1
    )
    output = capsys.readouterr()
    assert CONNECTION_ID in output.err
    assert "created disabled" in output.err
    assert "Read its current ETag" in output.err
    assert "secret-value" not in output.out + output.err
    assert client.request.call_count == 3


def test_update_test_models_and_revocations_bind_csrf_etag_and_exact_paths(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection(enabled=False)),
        ConversionHttpResponse(200, {"status": "ready", "status_message": None}),
        ConversionHttpResponse(200, {"models": ["small-model"]}),
        ConversionHttpResponse(200, _connection(credential_present=False)),
        ConversionHttpResponse(200, _connection(enabled=False)),
    )
    etag = '"composer-connection-1"'
    commands = (
        ("composer", "connections", "disable", CONNECTION_ID, "--etag", etag),
        ("composer", "connections", "test", CONNECTION_ID),
        ("composer", "connections", "models", CONNECTION_ID),
        (
            "composer",
            "connections",
            "credentials",
            "revoke",
            CONNECTION_ID,
            "--etag",
            etag,
            "--force",
        ),
        (
            "composer",
            "connections",
            "revoke",
            CONNECTION_ID,
            "--etag",
            etag,
            "--force",
        ),
    )
    assert all(main(command) == 0 for command in commands)
    capsys.readouterr()
    calls = client.request.call_args_list
    assert json.loads(calls[0].kwargs["body"]) == {"enabled": False}
    assert calls[0].kwargs["headers"]["If-Match"] == etag
    assert calls[1].args[0] == "POST" and calls[1].kwargs["csrf"] is True
    assert json.loads(calls[1].kwargs["body"]) == {}
    assert calls[2].args[0] == "GET"
    assert json.loads(calls[3].kwargs["body"]) == {"revoke": True}
    assert calls[3].kwargs["csrf"] is True
    assert calls[3].kwargs["headers"]["If-Match"] == etag
    assert calls[4].args[0] == "DELETE"
    assert calls[4].kwargs["headers"]["If-Match"] == etag


def test_admin_personal_permission_list_and_grant_use_revision_precondition(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    permission = {
        "allowed": False,
        "etag": '"personal-permission-1"',
        "user_id": USER_ID,
        "username": "Alice",
    }
    client.request.side_effect = (
        ConversionHttpResponse(
            200, {"permissions": [permission], "limit": 50, "offset": 0}
        ),
        ConversionHttpResponse(200, {**permission, "allowed": True}),
    )
    assert main(("composer", "personal-permissions", "list")) == 0
    assert "Alice\tdenied" in capsys.readouterr().out
    assert (
        main(
            (
                "--json",
                "composer",
                "personal-permissions",
                "grant",
                USER_ID,
                "--etag",
                '"personal-permission-1"',
                "--force",
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["permission"]["allowed"] is True
    request = client.request.call_args
    assert request.args == (
        "PUT",
        f"/api/v1/composer/personal-permissions/{USER_ID}",
    )
    assert request.kwargs["csrf"] is True
    assert request.kwargs["headers"]["If-Match"] == '"personal-permission-1"'
    assert json.loads(request.kwargs["body"]) == {"allowed": True}


def test_non_interactive_credential_rotation_fails_before_http(remote, capsys) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 1
    )
    assert "secure prompt or file" in capsys.readouterr().err
    client.request.assert_not_called()


def test_show_update_select_and_permission_show_cover_read_write_parity(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    permission = {
        "allowed": True,
        "etag": '"personal-permission-2"',
        "user_id": USER_ID,
        "username": "Alice",
    }
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(200, _connection(name="Changed")),
        ConversionHttpResponse(200, _connection(selected_model="other-model")),
        ConversionHttpResponse(200, permission),
        ConversionHttpResponse(200, {**permission, "allowed": False}),
    )
    etag = '"composer-connection-1"'
    assert main(("composer", "connections", "show", CONNECTION_ID)) == 0
    assert (
        main(
            (
                "composer",
                "connections",
                "update",
                CONNECTION_ID,
                "--name",
                "Changed",
                "--allowed-user",
                USER_ID,
                "--etag",
                etag,
            )
        )
        == 0
    )
    assert (
        main(
            (
                "composer",
                "connections",
                "select-model",
                CONNECTION_ID,
                "--model",
                "other-model",
                "--etag",
                etag,
            )
        )
        == 0
    )
    assert main(("composer", "personal-permissions", "show", USER_ID)) == 0
    assert (
        main(
            (
                "composer",
                "personal-permissions",
                "revoke",
                USER_ID,
                "--etag",
                '"personal-permission-2"',
                "--force",
            )
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(client.request.call_args_list[1].kwargs["body"]) == {
        "allowed_user_ids": [USER_ID],
        "name": "Changed",
    }
    assert json.loads(client.request.call_args_list[2].kwargs["body"]) == {
        "selected_model": "other-model"
    }
    assert json.loads(client.request.call_args_list[4].kwargs["body"]) == {
        "allowed": False
    }


def test_direct_rotation_omits_blank_secret_slots(remote, mocker, capsys) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": 4096}),
        ConversionHttpResponse(200, _connection(credential_present=True)),
    )
    mocker.patch.object(composer.sys.stdin, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=True)
    mocker.patch.object(composer.getpass, "getpass", return_value="api-secret")
    assert (
        main(
            (
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 0
    )
    assert json.loads(client.request.call_args.kwargs["body"]) == {
        "api_key": "api-secret"
    }
    assert "api-secret" not in capsys.readouterr().out


def test_empty_lists_and_empty_credentials_have_stable_messages(
    remote, mocker, capsys
) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, {"connections": [], "limit": 50, "offset": 0}),
        ConversionHttpResponse(200, {"models": []}),
        ConversionHttpResponse(200, {"permissions": [], "limit": 50, "offset": 0}),
    )
    assert main(("composer", "connections", "list")) == 0
    assert capsys.readouterr().out == "No authorized connections.\n"
    assert main(("composer", "connections", "models", CONNECTION_ID)) == 0
    assert capsys.readouterr().out == "No permitted models.\n"
    assert main(("composer", "personal-permissions", "list")) == 0
    assert capsys.readouterr().out == "No personal-connection permissions.\n"

    mocker.patch.object(composer.sys.stdin, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=True)
    mocker.patch.object(composer.getpass, "getpass", return_value="")
    assert (
        main(
            (
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 1
    )
    assert "Enter at least one" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (("composer", "connections", "show", "not-a-uuid"), "identifier is invalid"),
        (
            (
                "composer",
                "connections",
                "disable",
                CONNECTION_ID,
                "--etag",
                "bad\netag",
            ),
            "ETag is invalid",
        ),
        (
            (
                "--non-interactive",
                "composer",
                "connections",
                "revoke",
                CONNECTION_ID,
                "--etag",
                '"e"',
            ),
            "Use --force",
        ),
    ),
)
def test_invalid_identity_revision_and_confirmation_fail_before_http(
    remote, capsys, arguments, message
) -> None:
    _profile, _constructor, client = remote
    assert main(arguments) == 1
    assert message in capsys.readouterr().err
    client.request.assert_not_called()


def test_safe_api_errors_and_invalid_success_payloads(remote, capsys) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(
            503, {"error": {"code": "PROVIDER_OUTAGE", "message": "Try later."}}
        ),
        ConversionHttpResponse(200, None),
        ConversionHttpResponse(200, {"models": [1]}),
    )
    assert main(("composer", "connections", "test", CONNECTION_ID)) == 1
    assert capsys.readouterr().err == "error: Try later.\n"
    assert main(("composer", "connections", "show", CONNECTION_ID)) == 1
    assert "invalid response" in capsys.readouterr().err
    assert main(("composer", "connections", "models", CONNECTION_ID)) == 1
    assert "invalid response" in capsys.readouterr().err


def test_draft_upload_handoff_save_and_pagination_use_owner_http_only(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "source.md"
    source.write_text("# Draft\n", encoding="utf-8")
    draft = {"id": DRAFT_ID, "title": "Draft", "version": 1, "etag": '"draft-1"'}
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_upload_bytes": 1024}),
        ConversionHttpResponse(201, draft),
        ConversionHttpResponse(201, draft),
        ConversionHttpResponse(200, {**draft, "version": 2, "content": "saved"}),
        ConversionHttpResponse(200, {"drafts": [draft], "limit": 7, "offset": 3}),
    )
    assert (
        main(
            (
                "composer",
                "drafts",
                "create",
                str(source),
                "--title",
                "Draft",
            )
        )
        == 0
    )
    assert main(("composer", "drafts", "handoff", CONNECTION_ID)) == 0
    assert (
        main(
            (
                "composer",
                "drafts",
                "save",
                DRAFT_ID,
                "--title",
                "Draft",
                "--content",
                "saved",
                "--etag",
                '"draft-1"',
            )
        )
        == 0
    )
    assert (
        main(("--json", "composer", "drafts", "list", "--offset", "3", "--limit", "7"))
        == 0
    )
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["offset"] == 3
    calls = client.request.call_args_list
    assert calls[0].args == ("GET", "/api/v1/composer/capabilities")
    assert calls[1].args == ("POST", "/api/v1/composer/drafts")
    assert b'filename="source.md"' in calls[1].kwargs["body"]
    assert str(tmp_path).encode() not in calls[1].kwargs["body"]
    assert calls[2].args[1].endswith(f"from-conversion/{CONNECTION_ID}")
    assert calls[3].kwargs["headers"]["If-Match"] == '"draft-1"'
    assert calls[4].args[1].endswith("?offset=3&limit=7")


def test_draft_upload_uses_server_limit_and_fails_closed_when_missing(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "source.md"
    source.write_bytes(b"12345")
    command = ("composer", "drafts", "create", str(source))
    client.request.return_value = ConversionHttpResponse(
        200, {"maximum_upload_bytes": 4}
    )
    assert main(command) == 1
    assert "too large" in capsys.readouterr().err
    assert client.request.call_count == 1
    client.reset_mock()
    client.request.return_value = ConversionHttpResponse(
        200, {"maximum_upload_bytes": None}
    )
    assert main(command) == 1
    assert "invalid response" in capsys.readouterr().err
    assert client.request.call_count == 1


def test_messages_and_proposals_bind_pagination_etag_and_idempotency(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    message = {"id": CONNECTION_ID, "role": "user", "created_at": "now"}
    proposal = {
        "id": PROPOSAL_ID,
        "state": "accepted",
        "base_version": 1,
    }
    client.request.side_effect = (
        ConversionHttpResponse(200, {"messages": [message], "limit": 50, "offset": 0}),
        ConversionHttpResponse(201, message),
        ConversionHttpResponse(
            200, {"proposals": [proposal], "limit": 5, "offset": 10}
        ),
        ConversionHttpResponse(200, proposal),
    )
    assert main(("composer", "messages", "list", DRAFT_ID)) == 0
    assert (
        main(
            (
                "composer",
                "messages",
                "create",
                DRAFT_ID,
                "--content",
                "Revise this.",
                "--etag",
                '"draft-1"',
                "--idempotency-key",
                "message-1",
            )
        )
        == 0
    )
    assert (
        main(
            (
                "composer",
                "proposals",
                "list",
                DRAFT_ID,
                "--offset",
                "10",
                "--limit",
                "5",
            )
        )
        == 0
    )
    assert (
        main(
            (
                "composer",
                "proposals",
                "decide",
                DRAFT_ID,
                PROPOSAL_ID,
                "--state",
                "accepted",
                "--etag",
                '"draft-2"',
            )
        )
        == 0
    )
    capsys.readouterr()
    calls = client.request.call_args_list
    assert calls[1].kwargs["headers"] == {
        "Content-Type": "application/json",
        "If-Match": '"draft-1"',
        "Idempotency-Key": "message-1",
    }
    assert calls[2].args[1].endswith("?offset=10&limit=5")
    assert calls[3].kwargs["headers"]["If-Match"] == '"draft-2"'


def test_model_step_start_binds_exact_visible_connection_and_private_text(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    content = "Summarize the approved findings.\nKeep the dates exact.\n"
    path = tmp_path / "approved.txt"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 64,
            },
        ),
        ConversionHttpResponse(202, _model_step(unexpected_field="must-not-appear")),
    )
    assert (
        main(
            (
                "--json",
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(path),
                "--max-output-tokens",
                "32",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "model-1",
                "--force",
            )
        )
        == 0
    )
    output = capsys.readouterr()
    assert content not in output.out + output.err
    assert "must-not-appear" not in output.out + output.err
    assert json.loads(output.out)["model_step"]["id"] == STEP_ID
    read, capabilities, start = client.request.call_args_list
    assert read.args == ("GET", f"/api/v1/composer/connections/{CONNECTION_ID}")
    assert capabilities.args == ("GET", "/api/v1/composer/capabilities")
    assert start.args == ("POST", f"/api/v1/composer/drafts/{DRAFT_ID}/model-steps")
    assert start.kwargs["headers"]["If-Match"] == '"draft-2"'
    assert start.kwargs["headers"]["Idempotency-Key"] == "model-1"
    assert json.loads(start.kwargs["body"]) == {
        "connection_id": CONNECTION_ID,
        "approved_endpoint": "https://llm.example/v1",
        "approved_model": "small-model",
        "content": content,
        "max_output_tokens": 32,
    }


def test_model_step_requires_explicit_approval_and_service_limits(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    path = tmp_path / "approved.txt"
    path.write_text("approve me", encoding="utf-8")
    path.chmod(0o600)
    base = (
        "--non-interactive",
        "composer",
        "model-steps",
        "start",
        DRAFT_ID,
        CONNECTION_ID,
        "--content-file",
        str(path),
        "--max-output-tokens",
        "4",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "model-1",
    )
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 4,
            },
        ),
    )
    assert main(base) == 1
    assert "Use --force" in capsys.readouterr().err
    assert client.request.call_count == 2
    client.reset_mock()
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": None,
                "maximum_output_tokens": 4,
            },
        ),
    )
    assert main((*base, "--force")) == 1
    assert "invalid response" in capsys.readouterr().err
    assert client.request.call_count == 2


def test_model_step_rejects_unsafe_and_oversized_content_before_post(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    content_file = tmp_path / "approved.txt"
    content_file.write_text("content", encoding="utf-8")
    content_file.chmod(0o644)
    command = (
        "--non-interactive",
        "composer",
        "model-steps",
        "start",
        DRAFT_ID,
        CONNECTION_ID,
        "--content-file",
        str(content_file),
        "--max-output-tokens",
        "4",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "step-1",
        "--force",
    )
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 10,
                "maximum_output_tokens": 4,
            },
        ),
    )
    assert main(command) == 1
    assert "accessible only by the current user" in capsys.readouterr().err
    assert client.request.call_count == 2
    content_file.chmod(0o600)
    client.reset_mock()
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 10,
                "maximum_output_tokens": 4,
            },
        ),
    )
    assert main(command) == 1
    assert "model request exceeds" in capsys.readouterr().err
    assert client.request.call_count == 2


def test_model_step_interactive_preview_requires_yes_before_transmission(
    remote, tmp_path, mocker, capsys
) -> None:
    _profile, _constructor, client = remote
    content = "Exact text\nSecond line\n"
    path = tmp_path / "approved.txt"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    mocker.patch.object(composer.sys.stdin, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stdin, "readline", return_value="no\n")
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 32,
            },
        ),
    )
    assert (
        main(
            (
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(path),
                "--max-output-tokens",
                "8",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "preview-1",
            )
        )
        == 1
    )
    output = capsys.readouterr()
    assert "Destination: https://llm.example/v1" in output.err
    assert "Model: small-model" in output.err
    assert f"--- BEGIN TEXT ---\n{content}\n--- END TEXT ---" in output.err
    assert content not in output.out
    assert client.request.call_count == 2


def test_model_step_rejects_text_on_argv_without_echo(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            (
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content",
                "must-not-appear",
            )
        )
    assert raised.value.code == 2
    output = capsys.readouterr()
    assert "must-not-appear" not in output.out + output.err


def test_model_step_start_redacts_echoing_provider_error(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    content = "private-content-that-must-not-appear"
    source = tmp_path / "content.txt"
    source.write_text(content, encoding="utf-8")
    source.chmod(0o600)
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 32,
            },
        ),
        ConversionHttpResponse(
            422, {"error": {"code": content, "message": f"Rejected: {content}"}}
        ),
    )
    assert (
        main(
            (
                "--json",
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(source),
                "--max-output-tokens",
                "8",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "error-1",
                "--force",
            )
        )
        == 1
    )
    output = capsys.readouterr()
    assert content not in output.out + output.err
    assert json.loads(output.err)["error"]["code"] == "model_step_start_failed"
    assert client.request.call_count == 3


def test_model_step_start_rejects_echoed_text_in_model_identity(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    content = "private-content-that-must-not-appear"
    source = tmp_path / "content.txt"
    source.write_text(content, encoding="utf-8")
    source.chmod(0o600)
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 32,
            },
        ),
        ConversionHttpResponse(202, _model_step(model_identity=content)),
    )
    assert (
        main(
            (
                "--json",
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(source),
                "--max-output-tokens",
                "8",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "identity-1",
                "--force",
            )
        )
        == 1
    )
    output = capsys.readouterr()
    assert content not in output.out + output.err
    assert json.loads(output.err)["error"]["code"] == "response_invalid"


@pytest.mark.parametrize(
    "invalid_fields",
    (
        {"status": "private-content"},
        {"status": "failed", "error_code": "private_content"},
        {"status": "completed", "proposal_id": "private-content"},
        {"status": "running", "proposal_id": PROPOSAL_ID},
    ),
)
def test_model_step_rejects_content_bearing_or_inconsistent_status_fields(
    remote, capsys, invalid_fields
) -> None:
    _profile, _constructor, client = remote
    client.request.return_value = ConversionHttpResponse(
        200, _model_step(**invalid_fields)
    )
    assert main(("--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 1
    output = capsys.readouterr()
    assert "private-content" not in output.out + output.err
    assert json.loads(output.err)["error"]["code"] == "response_invalid"


@pytest.mark.parametrize(
    "fields",
    (
        {"status": "completed", "proposal_id": PROPOSAL_ID},
        {"status": "failed", "error_code": "provider_invalid"},
        {"status": "failed", "error_code": "capacity_exhausted"},
    ),
)
def test_model_step_accepts_valid_terminal_metadata(remote, capsys, fields) -> None:
    _profile, _constructor, client = remote
    client.request.return_value = ConversionHttpResponse(200, _model_step(**fields))
    assert main(("--json", "composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 0
    step = json.loads(capsys.readouterr().out)["model_step"]
    assert step["status"] == fields["status"]


def test_model_step_status_and_cancel_use_owner_paths_without_model_service(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, _model_step()),
        ConversionHttpResponse(200, _model_step(status="cancelled")),
    )
    assert main(("composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 0
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "model-steps",
                "cancel",
                DRAFT_ID,
                STEP_ID,
                "--force",
            )
        )
        == 0
    )
    output = capsys.readouterr()
    assert "cancelled" in output.out
    assert [call.args for call in client.request.call_args_list] == [
        ("GET", f"/api/v1/composer/drafts/{DRAFT_ID}/model-steps/{STEP_ID}"),
        ("DELETE", f"/api/v1/composer/drafts/{DRAFT_ID}/model-steps/{STEP_ID}"),
    ]
    assert client.request.call_args_list[1].kwargs["csrf"] is True


def test_model_step_cancel_requires_confirmation_before_http(remote, capsys) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "model-steps",
                "cancel",
                DRAFT_ID,
                STEP_ID,
            )
        )
        == 1
    )
    assert "Use --force" in capsys.readouterr().err
    client.request.assert_not_called()


def test_model_step_content_file_symlink_is_rejected_before_post(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "source.txt"
    source.write_text("private text", encoding="utf-8")
    source.chmod(0o600)
    link = tmp_path / "link.txt"
    link.symlink_to(source)
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 32,
            },
        ),
    )
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(link),
                "--max-output-tokens",
                "8",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "symlink-1",
                "--force",
            )
        )
        == 1
    )
    assert "accessible only by the current user" in capsys.readouterr().err
    assert client.request.call_count == 2


def test_revision_capture_listing_download_and_restore_preserve_exact_artifact(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    revision = {"id": REVISION_ID, "number": 1, "operation": "capture_source"}
    client.request.side_effect = (
        ConversionHttpResponse(201, revision),
        ConversionHttpResponse(
            200, {"revisions": [revision], "limit": 50, "offset": 0}
        ),
        ConversionHttpResponse(201, {**revision, "number": 2}),
    )
    output = tmp_path / "revision.md"
    client.download.return_value = ConversionHttpResponse(
        200,
        headers={
            "x-composer-revision": REVISION_ID,
            "x-content-type-options": "nosniff",
        },
        bytes_written=8,
    )
    base = (DRAFT_ID, "--etag", '"draft-1"', "--idempotency-key")
    assert main(("composer", "revisions", "capture", *base, "capture-1")) == 0
    assert main(("composer", "revisions", "list", DRAFT_ID)) == 0
    assert (
        main(
            (
                "--json",
                "composer",
                "revisions",
                "download",
                DRAFT_ID,
                REVISION_ID,
                "--kind",
                "download",
                "--output",
                str(output),
            )
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["bytes_written"] == 8
    assert (
        main(
            (
                "composer",
                "revisions",
                "restore",
                DRAFT_ID,
                REVISION_ID,
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "restore-1",
            )
        )
        == 0
    )
    capture, _listing, restore = client.request.call_args_list
    assert capture.kwargs["headers"]["Idempotency-Key"] == "capture-1"
    assert restore.kwargs["headers"]["Idempotency-Key"] == "restore-1"
    client.download.assert_called_once_with(
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}/artifacts/download",
        output,
        overwrite=False,
        validate_headers=client.download.call_args.kwargs["validate_headers"],
    )
    client.download.call_args.kwargs["validate_headers"](
        {"x-content-type-options": "nosniff", "x-composer-revision": REVISION_ID}
    )


def test_draft_proposal_and_revision_show_commands_use_exact_owner_paths(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    draft = {"id": DRAFT_ID, "title": "Draft", "version": 2}
    proposal = {"id": PROPOSAL_ID, "state": "pending", "base_version": 2}
    revision = {"id": REVISION_ID, "number": 3, "operation": "restore"}
    client.request.side_effect = tuple(
        ConversionHttpResponse(200, payload) for payload in (draft, proposal, revision)
    )
    assert main(("composer", "drafts", "show", DRAFT_ID)) == 0
    assert main(("composer", "proposals", "show", DRAFT_ID, PROPOSAL_ID)) == 0
    assert main(("composer", "revisions", "show", DRAFT_ID, REVISION_ID)) == 0
    capsys.readouterr()
    assert [call.args[1] for call in client.request.call_args_list] == [
        f"/api/v1/composer/drafts/{DRAFT_ID}",
        f"/api/v1/composer/drafts/{DRAFT_ID}/proposals/{PROPOSAL_ID}",
        f"/api/v1/composer/drafts/{DRAFT_ID}/revisions/{REVISION_ID}",
    ]


def test_invalid_pagination_and_edited_proposal_fail_before_http(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    assert main(("composer", "drafts", "list", "--limit", "101")) == 1
    assert "between 1 and 100" in capsys.readouterr().err
    assert (
        main(
            (
                "composer",
                "proposals",
                "decide",
                DRAFT_ID,
                PROPOSAL_ID,
                "--state",
                "edited",
                "--etag",
                '"draft-2"',
            )
        )
        == 1
    )
    assert "require --value" in capsys.readouterr().err
    client.request.assert_not_called()


def test_malformed_connection_and_permission_metadata_fail_closed(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    client.request.return_value = ConversionHttpResponse(
        200, {"connections": "not-a-list", "offset": 0, "limit": 50}
    )
    assert main(("composer", "connections", "list")) == 1
    assert "invalid response" in capsys.readouterr().err

    client.request.return_value = ConversionHttpResponse(
        200, {"permissions": "not-a-list", "offset": 0, "limit": 50}
    )
    assert main(("composer", "personal-permissions", "list")) == 1
    assert "invalid response" in capsys.readouterr().err

    client.request.return_value = ConversionHttpResponse(200, {"status": None})
    assert main(("composer", "capabilities")) == 1
    assert "invalid response" in capsys.readouterr().err

    client.request.return_value = ConversionHttpResponse(
        200, {"user_id": USER_ID, "username": "Alice", "allowed": "yes"}
    )
    assert main(("composer", "personal-permissions", "show", USER_ID)) == 1
    assert "invalid response" in capsys.readouterr().err


def test_empty_update_and_invalid_idempotency_fail_before_mutation(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "composer",
                "connections",
                "update",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 1
    )
    assert "At least one update" in capsys.readouterr().err
    assert (
        main(
            (
                "composer",
                "messages",
                "create",
                DRAFT_ID,
                "--content",
                "message",
                "--etag",
                '"draft-1"',
                "--idempotency-key",
                "bad key",
            )
        )
        == 1
    )
    assert "idempotency key is invalid" in capsys.readouterr().err
    client.request.assert_not_called()


def test_model_step_rejects_disabled_connection_and_excess_output_tokens(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "approved.txt"
    source.write_text("safe text", encoding="utf-8")
    source.chmod(0o600)
    command = (
        "--non-interactive",
        "composer",
        "model-steps",
        "start",
        DRAFT_ID,
        CONNECTION_ID,
        "--content-file",
        str(source),
        "--max-output-tokens",
        "33",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "bound-1",
        "--force",
    )
    client.request.return_value = ConversionHttpResponse(
        200, _connection(enabled=False)
    )
    assert main(command) == 1
    assert "invalid connection" in capsys.readouterr().err
    assert client.request.call_count == 1

    client.reset_mock()
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 32,
            },
        ),
    )
    assert main(command) == 1
    assert "Output tokens exceed" in capsys.readouterr().err
    assert client.request.call_count == 2


def test_model_step_rejects_malformed_identity_and_terminal_metadata(
    remote, capsys
) -> None:
    _profile, _constructor, client = remote
    for fields in (
        {"id": "invalid-id"},
        {"status": "running", "error_code": "provider_invalid"},
    ):
        client.request.return_value = ConversionHttpResponse(200, _model_step(**fields))
        assert main(("composer", "model-steps", "status", DRAFT_ID, STEP_ID)) == 1
        assert "invalid response" in capsys.readouterr().err


def test_credential_file_missing_empty_and_invalid_utf8_fail_before_put(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "api-key"
    command = (
        "--non-interactive",
        "composer",
        "connections",
        "credentials",
        "rotate",
        CONNECTION_ID,
        "--etag",
        '"composer-connection-1"',
        "--api-key-file",
        str(source),
    )
    assert main(command) == 1
    assert "could not be read" in capsys.readouterr().err
    client.request.assert_not_called()

    for value, expected in ((b"", "empty or too large"), (b"\xff", "not UTF-8")):
        source.write_bytes(value)
        source.chmod(0o600)
        client.reset_mock()
        client.request.return_value = ConversionHttpResponse(
            200, {"maximum_credential_bytes": 16}
        )
        assert main(command) == 1
        assert expected in capsys.readouterr().err
        assert client.request.call_count == 1


def test_secure_prompt_warning_stops_credential_mutation(
    remote, mocker, capsys
) -> None:
    _profile, _constructor, client = remote
    mocker.patch.object(composer.sys.stdin, "isatty", return_value=True)
    mocker.patch.object(composer.sys.stderr, "isatty", return_value=True)
    mocker.patch.object(
        composer.getpass,
        "getpass",
        side_effect=composer.getpass.GetPassWarning("insecure prompt"),
    )
    assert (
        main(
            (
                "composer",
                "connections",
                "credentials",
                "rotate",
                CONNECTION_ID,
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 1
    )
    assert "secure interactive terminal" in capsys.readouterr().err
    client.request.assert_not_called()


def test_model_text_empty_oversized_and_invalid_utf8_fail_before_post(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "approved.txt"
    command = (
        "--non-interactive",
        "composer",
        "model-steps",
        "start",
        DRAFT_ID,
        CONNECTION_ID,
        "--content-file",
        str(source),
        "--max-output-tokens",
        "4",
        "--etag",
        '"draft-2"',
        "--idempotency-key",
        "content-1",
        "--force",
    )
    for value, maximum, expected in (
        (b"", 16, "model text is empty"),
        (b"123456789", 8, "model text exceeds"),
        (b"\xff", 16, "not valid UTF-8"),
    ):
        source.write_bytes(value)
        source.chmod(0o600)
        client.reset_mock()
        client.request.side_effect = (
            ConversionHttpResponse(200, _connection()),
            ConversionHttpResponse(
                200,
                {
                    "maximum_model_request_bytes": maximum,
                    "maximum_output_tokens": 4,
                },
            ),
        )
        assert main(command) == 1
        assert expected in capsys.readouterr().err
        assert client.request.call_count == 2


def test_model_text_missing_file_fails_before_post(remote, tmp_path, capsys) -> None:
    _profile, _constructor, client = remote
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 4,
            },
        ),
    )
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--content-file",
                str(tmp_path / "missing.txt"),
                "--max-output-tokens",
                "4",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "missing-1",
                "--force",
            )
        )
        == 1
    )
    assert "could not be read" in capsys.readouterr().err
    assert client.request.call_count == 2


def test_draft_upload_rejects_missing_empty_symlink_and_unsupported_sources(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    source = tmp_path / "source.md"
    source.write_bytes(b"")
    symlink = tmp_path / "alias.md"
    symlink.symlink_to(source)
    unsupported = tmp_path / "source.txt"
    unsupported.write_bytes(b"content")
    for path, expected in (
        (tmp_path / "missing.md", "could not be read"),
        (source, "upload file is empty"),
        (symlink, "regular file"),
        (unsupported, "source type is unsupported"),
    ):
        client.reset_mock()
        client.request.return_value = ConversionHttpResponse(
            200, {"maximum_upload_bytes": 4096}
        )
        assert main(("composer", "drafts", "create", str(path))) == 1
        assert expected in capsys.readouterr().err
        assert client.request.call_count == 1


def test_server_error_envelopes_fall_back_to_safe_cli_message(remote, capsys) -> None:
    _profile, _constructor, client = remote
    for payload in (
        None,
        {"error": "invalid envelope"},
        {"error": {"code": 7, "message": "untrusted text"}},
    ):
        client.request.return_value = ConversionHttpResponse(503, payload)
        assert main(("composer", "connections", "test", CONNECTION_ID)) == 1
        error = capsys.readouterr().err
        assert "service rejected the request" in error
        assert "untrusted text" not in error


def test_connection_create_rejects_invalid_response_identity_and_etag(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    base = (
        "composer",
        "connections",
        "create",
        "--name",
        "Safe",
        "--endpoint",
        "https://llm.example/v1",
        "--scope",
        "personal",
        "--identity-mode",
        "individual",
    )
    client.request.return_value = ConversionHttpResponse(201, {"id": None})
    assert main(base) == 1
    assert "invalid response" in capsys.readouterr().err

    credential = tmp_path / "api-key"
    credential.write_text("opaque", encoding="utf-8")
    credential.chmod(0o600)
    client.reset_mock()
    client.request.side_effect = (
        ConversionHttpResponse(200, {"maximum_credential_bytes": 64}),
        ConversionHttpResponse(201, {"id": CONNECTION_ID, "etag": None}),
    )
    assert (
        main(
            (
                "--non-interactive",
                *base,
                "--with-credentials",
                "--api-key-file",
                str(credential),
            )
        )
        == 1
    )
    error = capsys.readouterr().err
    assert CONNECTION_ID in error and "created disabled" in error
    assert "opaque" not in error
    assert client.request.call_count == 2


def test_empty_model_name_fails_before_http(remote, capsys) -> None:
    _profile, _constructor, client = remote
    assert (
        main(
            (
                "composer",
                "connections",
                "select-model",
                CONNECTION_ID,
                "--model",
                "",
                "--etag",
                '"composer-connection-1"',
            )
        )
        == 1
    )
    assert "command arguments are invalid" in capsys.readouterr().err
    client.request.assert_not_called()


def test_private_file_identity_change_is_rejected_after_open(tmp_path, mocker) -> None:
    source = tmp_path / "private.txt"
    source.write_text("private content", encoding="utf-8")
    source.chmod(0o600)
    actual = source.lstat()
    changed = SimpleNamespace(
        st_mode=actual.st_mode,
        st_uid=actual.st_uid,
        st_dev=actual.st_dev,
        st_ino=actual.st_ino + 1,
    )
    mocker.patch.object(composer.Path, "lstat", return_value=changed)
    with pytest.raises(composer.CliError, match="accessible only by the current user"):
        composer._read_credential_file(source, maximum_bytes=64)
    with pytest.raises(composer.CliError, match="accessible only by the current user"):
        composer._read_model_content_file(source, maximum_bytes=64)


def test_model_text_stdin_uses_bounded_read_in_process(remote, mocker, capsys) -> None:
    _profile, _constructor, client = remote
    content = "Approved stdin text\n"
    mocker.patch.object(
        composer.sys, "stdin", io.TextIOWrapper(io.BytesIO(content.encode()))
    )
    client.request.side_effect = (
        ConversionHttpResponse(200, _connection()),
        ConversionHttpResponse(
            200,
            {
                "maximum_model_request_bytes": 4096,
                "maximum_output_tokens": 8,
            },
        ),
        ConversionHttpResponse(202, _model_step()),
    )
    assert (
        main(
            (
                "--non-interactive",
                "composer",
                "model-steps",
                "start",
                DRAFT_ID,
                CONNECTION_ID,
                "--stdin",
                "--max-output-tokens",
                "8",
                "--etag",
                '"draft-2"',
                "--idempotency-key",
                "stdin-unit",
                "--force",
            )
        )
        == 0
    )
    assert json.loads(client.request.call_args.kwargs["body"])["content"] == content
    assert content not in capsys.readouterr().out


def test_revision_download_error_and_header_validation_preserve_output(
    remote, tmp_path, capsys
) -> None:
    _profile, _constructor, client = remote
    output = tmp_path / "revision.md"
    client.download.return_value = ConversionHttpResponse(503, None)
    assert (
        main(
            (
                "composer",
                "revisions",
                "download",
                DRAFT_ID,
                REVISION_ID,
                "--kind",
                "download",
                "--output",
                str(output),
            )
        )
        == 1
    )
    assert "service rejected the request" in capsys.readouterr().err
    assert not output.exists()
    with pytest.raises(composer.CliError, match="invalid artifact response"):
        composer._validate_revision_headers({}, REVISION_ID)


def test_draft_list_rejects_missing_display_field(remote, capsys) -> None:
    _profile, _constructor, client = remote
    client.request.return_value = ConversionHttpResponse(
        200,
        {
            "drafts": [{"id": DRAFT_ID, "title": None, "version": 1}],
            "offset": 0,
            "limit": 50,
        },
    )
    assert main(("composer", "drafts", "list")) == 1
    assert "invalid response" in capsys.readouterr().err


def test_multipart_rejects_injection_and_regenerates_colliding_boundary(mocker) -> None:
    with pytest.raises(composer.CliError, match="filename is invalid"):
        composer._draft_multipart(b"content", "bad\nname.md", title=None, content=None)
    mocker.patch.object(
        composer,
        "uuid4",
        side_effect=(SimpleNamespace(hex="first"), SimpleNamespace(hex="second")),
    )
    body, content_type = composer._draft_multipart(
        b"markweave-first", "source.md", title=None, content=None
    )
    assert content_type.endswith("boundary=markweave-second")
    assert b"markweave-first" in body
