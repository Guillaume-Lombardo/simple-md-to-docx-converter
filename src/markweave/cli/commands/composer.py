"""HTTP-only Composer connection administration commands."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlencode
from uuid import UUID, uuid4

from markweave.cli.commands.conversion_http import (
    ConversionHttpClient,
    ConversionHttpResponse,
)
from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.profiles import ProfileStore, validate_profile_name
from markweave.cli.types import CommandContext

_DEFAULT_PROFILE = "default"
_OK = 200
_CREATED = 201
_MAXIMUM_PAGE_SIZE = 100
_READ_CHUNK_BYTES = 65_536
_MODEL_STEP_STATES = frozenset({"running", "completed", "failed", "cancelled"})
_MODEL_STEP_ERROR_CODES = frozenset(
    {
        "execution_unavailable",
        "provider_unavailable",
        "provider_invalid",
        "execution_failed",
        "step_expired",
        "capacity_exhausted",
    }
)
_MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS = 128
_FIRST_VISIBLE_ASCII = 33
_LAST_VISIBLE_ASCII = 126


@dataclass
class _Command:
    name: str
    values: dict[str, Any] = field(default_factory=dict)


class _Store(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        command = namespace.command_name
        if isinstance(command, _Command):
            command.values[self.dest] = values


class _Append(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        current = list(getattr(namespace, self.dest, None) or [])
        if isinstance(values, str):
            current.append(values)
        setattr(namespace, self.dest, current)
        command = namespace.command_name
        if isinstance(command, _Command):
            command.values[self.dest] = current


class _Flag(argparse.Action):
    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, values, option_string
        setattr(namespace, self.dest, True)
        command = namespace.command_name
        if isinstance(command, _Command):
            command.values[self.dest] = True


class _RejectSecret(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del namespace, values, option_string
        parser.error("Credential material must be entered through the secure prompt.")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register Composer capabilities and connection-management parity."""
    composer = subparsers.add_parser("composer", help="Manage Composer connections.")
    commands = composer.add_subparsers(dest="composer_command", metavar="COMMAND")

    capabilities = commands.add_parser(
        "capabilities", help="Show Composer availability for the current account."
    )
    _profile(capabilities)
    _bind(capabilities, "capabilities", _capabilities)

    _register_connections(commands)
    _register_personal_permissions(commands)
    _register_drafts(commands)
    _register_messages(commands)
    _register_model_steps(commands)
    _register_proposals(commands)
    _register_revisions(commands)


def _register_drafts(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    drafts = commands.add_parser("drafts", help="Manage owner-scoped drafts.")
    actions = drafts.add_subparsers(dest="drafts_command", metavar="COMMAND")
    listing = actions.add_parser("list", help="List drafts.")
    _pagination(listing)
    _profile(listing)
    _bind(listing, "drafts list", _list_drafts, offset=0, limit=50)
    show = actions.add_parser("show", help="Show a draft.")
    _draft_id(show)
    _profile(show)
    _bind(show, "drafts show", _show_draft)
    create = actions.add_parser("create", help="Create a draft from a local source.")
    create.add_argument("source", action=_Store)
    create.add_argument("--title", action=_Store)
    create.add_argument("--content", action=_Store)
    _profile(create)
    _bind(create, "drafts create", _create_draft)
    handoff = actions.add_parser("handoff", help="Create a draft from a conversion.")
    handoff.add_argument("job_id", action=_Store, metavar="JOB_ID")
    handoff.add_argument("--title", action=_Store)
    _profile(handoff)
    _bind(handoff, "drafts handoff", _handoff_draft)
    save = actions.add_parser("save", help="Save complete draft text.")
    _draft_id(save)
    save.add_argument("--title", required=True, action=_Store)
    save.add_argument("--content", required=True, action=_Store)
    _etag(save)
    _profile(save)
    _bind(save, "drafts save", _save_draft)


def _register_messages(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    messages = commands.add_parser("messages", help="Manage draft messages.")
    actions = messages.add_subparsers(dest="messages_command", metavar="COMMAND")
    listing = actions.add_parser("list", help="List draft messages.")
    _draft_id(listing)
    _pagination(listing)
    _profile(listing)
    _bind(listing, "messages list", _list_messages, offset=0, limit=50)
    create = actions.add_parser("create", help="Add a user message.")
    _draft_id(create)
    create.add_argument("--content", required=True, action=_Store)
    _etag(create)
    _idempotency_key(create)
    _profile(create)
    _bind(create, "messages create", _create_message)


def _register_model_steps(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    steps = commands.add_parser("model-steps", help="Run approved model steps.")
    actions = steps.add_subparsers(dest="model_steps_command", metavar="COMMAND")

    start = actions.add_parser("start", help="Approve and start a bounded model step.")
    _draft_id(start)
    _connection_id(start)
    source = start.add_mutually_exclusive_group(required=True)
    source.add_argument("--content-file", action=_Store, metavar="PATH")
    source.add_argument("--stdin", action=_Flag, help="Read approved text from stdin.")
    start.add_argument("--content", action=_RejectSecret, help=argparse.SUPPRESS)
    start.add_argument("--max-output-tokens", type=int, required=True, action=_Store)
    _etag(start)
    _idempotency_key(start)
    _force(start)
    _profile(start)
    _bind(start, "model-steps start", _start_model_step)

    status = actions.add_parser("status", help="Show a durable model step.")
    _draft_id(status)
    status.add_argument("step_id", action=_Store, metavar="STEP_ID")
    _profile(status)
    _bind(status, "model-steps status", _status_model_step)

    cancel = actions.add_parser("cancel", help="Cancel a durable model step.")
    _draft_id(cancel)
    cancel.add_argument("step_id", action=_Store, metavar="STEP_ID")
    _force(cancel)
    _profile(cancel)
    _bind(cancel, "model-steps cancel", _cancel_model_step)


def _register_proposals(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    proposals = commands.add_parser("proposals", help="Review draft proposals.")
    actions = proposals.add_subparsers(dest="proposals_command", metavar="COMMAND")
    listing = actions.add_parser("list", help="List draft proposals.")
    _draft_id(listing)
    _pagination(listing)
    _profile(listing)
    _bind(listing, "proposals list", _list_proposals, offset=0, limit=50)
    show = actions.add_parser("show", help="Show a draft proposal.")
    _draft_id(show)
    _proposal_id(show)
    _profile(show)
    _bind(show, "proposals show", _show_proposal)
    decide = actions.add_parser("decide", help="Accept, edit, or reject a proposal.")
    _draft_id(decide)
    _proposal_id(decide)
    decide.add_argument(
        "--state",
        choices=("accepted", "edited", "rejected"),
        required=True,
        action=_Store,
    )
    decide.add_argument("--value", action=_Store)
    _etag(decide)
    _profile(decide)
    _bind(decide, "proposals decide", _decide_proposal)


def _register_revisions(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    revisions = commands.add_parser("revisions", help="Manage immutable revisions.")
    actions = revisions.add_subparsers(dest="revisions_command", metavar="COMMAND")
    capture = actions.add_parser("capture", help="Capture the current source.")
    _draft_id(capture)
    _etag(capture)
    _idempotency_key(capture)
    _profile(capture)
    _bind(capture, "revisions capture", _capture_revision)
    listing = actions.add_parser("list", help="List draft revisions.")
    _draft_id(listing)
    _pagination(listing)
    _profile(listing)
    _bind(listing, "revisions list", _list_revisions, offset=0, limit=50)
    show = actions.add_parser("show", help="Show a draft revision.")
    _draft_id(show)
    _revision_id(show)
    _profile(show)
    _bind(show, "revisions show", _show_revision)
    download = actions.add_parser("download", help="Download an exact artifact.")
    _draft_id(download)
    _revision_id(download)
    download.add_argument(
        "--kind", choices=("download", "preview"), required=True, action=_Store
    )
    download.add_argument("--output", required=True, action=_Store)
    _force(download)
    _profile(download)
    _bind(download, "revisions download", _download_revision)
    restore = actions.add_parser("restore", help="Restore an immutable revision.")
    _draft_id(restore)
    _revision_id(restore)
    _etag(restore)
    _idempotency_key(restore)
    _profile(restore)
    _bind(restore, "revisions restore", _restore_revision)


def _register_connections(  # noqa: PLR0915 - declarative argparse registry
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the connection lifecycle below the Composer family."""

    connections = commands.add_parser("connections", help="Manage LLM connections.")
    actions = connections.add_subparsers(dest="connections_command", metavar="COMMAND")

    listing = actions.add_parser("list", help="List authorized connections.")
    _pagination(listing)
    _profile(listing)
    _bind(listing, "connections list", _list, offset=0, limit=50)

    show = actions.add_parser("show", help="Show one authorized connection.")
    _connection_id(show)
    _profile(show)
    _bind(show, "connections show", _show)

    create = actions.add_parser("create", help="Create a connection.")
    _metadata_arguments(create, create=True)
    create.add_argument(
        "--with-credentials",
        action=_Flag,
        help="Prompt securely for write-only credential material after creation.",
    )
    _credential_file_arguments(create)
    _reject_secret_arguments(create)
    _profile(create)
    _bind(create, "connections create", _create)

    update = actions.add_parser("update", help="Update connection metadata.")
    _connection_id(update)
    _metadata_arguments(update, create=False)
    _etag(update)
    _profile(update)
    _bind(update, "connections update", _update)

    for name, enabled in (("enable", True), ("disable", False)):
        parser = actions.add_parser(name, help=f"{name.title()} a connection.")
        _connection_id(parser)
        _etag(parser)
        _profile(parser)
        _bind(parser, f"connections {name}", _set_enabled, enabled=enabled)

    test = actions.add_parser("test", help="Run the bounded production-path test.")
    _connection_id(test)
    _profile(test)
    _bind(test, "connections test", _test)

    models = actions.add_parser("models", help="Discover permitted models.")
    _connection_id(models)
    _profile(models)
    _bind(models, "connections models", _models)

    select = actions.add_parser("select-model", help="Select a permitted model.")
    _connection_id(select)
    select.add_argument("--model", required=True, action=_Store)
    _etag(select)
    _profile(select)
    _bind(select, "connections select-model", _select_model)

    credentials = actions.add_parser(
        "credentials", help="Rotate or revoke write-only credentials."
    )
    credential_actions = credentials.add_subparsers(
        dest="credentials_command", metavar="COMMAND"
    )
    rotate = credential_actions.add_parser(
        "rotate", help="Prompt securely for replacement credential material."
    )
    _connection_id(rotate)
    _etag(rotate)
    _credential_file_arguments(rotate)
    _reject_secret_arguments(rotate)
    _profile(rotate)
    _bind(rotate, "connections credentials rotate", _rotate_credentials)

    revoke_credentials = credential_actions.add_parser(
        "revoke", help="Revoke the current account's credential material."
    )
    _connection_id(revoke_credentials)
    _etag(revoke_credentials)
    _force(revoke_credentials)
    _profile(revoke_credentials)
    _bind(
        revoke_credentials,
        "connections credentials revoke",
        _revoke_credentials,
    )

    revoke = actions.add_parser(
        "revoke", help="Revoke a connection while retaining owner archives."
    )
    _connection_id(revoke)
    _etag(revoke)
    _force(revoke)
    _profile(revoke)
    _bind(revoke, "connections revoke", _revoke)


def _register_personal_permissions(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    permissions = commands.add_parser(
        "personal-permissions", help="Manage personal-connection grants."
    )
    actions = permissions.add_subparsers(
        dest="personal_permissions_command", metavar="COMMAND"
    )
    listing = actions.add_parser("list", help="List personal-connection grants.")
    _pagination(listing)
    _profile(listing)
    _bind(
        listing,
        "personal-permissions list",
        _list_personal_permissions,
        offset=0,
        limit=50,
    )

    show = actions.add_parser("show", help="Show one personal-connection grant.")
    show.add_argument("user_id", action=_Store, metavar="USER_ID")
    _profile(show)
    _bind(show, "personal-permissions show", _show_personal_permission)

    for name, allowed in (("grant", True), ("revoke", False)):
        parser = actions.add_parser(name, help=f"{name.title()} personal connections.")
        parser.add_argument("user_id", action=_Store, metavar="USER_ID")
        _etag(parser)
        _force(parser)
        _profile(parser)
        _bind(
            parser,
            f"personal-permissions {name}",
            _set_personal_permission,
            allowed=allowed,
        )


def _metadata_arguments(parser: argparse.ArgumentParser, *, create: bool) -> None:
    parser.add_argument("--name", required=create, action=_Store)
    parser.add_argument("--endpoint", required=create, action=_Store)
    parser.add_argument(
        "--scope",
        choices=("instance", "personal"),
        required=create,
        action=_Store,
    )
    parser.add_argument(
        "--identity-mode",
        choices=("shared", "individual"),
        required=create,
        action=_Store,
    )
    parser.add_argument("--permitted-model", action=_Append)
    if not create:
        parser.add_argument("--selected-model", action=_Store)
    parser.add_argument("--allowed-user", action=_Append)


def _connection_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("connection_id", action=_Store, metavar="CONNECTION_ID")


def _draft_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("draft_id", action=_Store, metavar="DRAFT_ID")


def _proposal_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("proposal_id", action=_Store, metavar="PROPOSAL_ID")


def _revision_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("revision_id", action=_Store, metavar="REVISION_ID")


def _etag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--etag", required=True, action=_Store)


def _idempotency_key(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--idempotency-key", required=True, action=_Store)


def _pagination(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--offset", type=int, default=0, action=_Store)
    parser.add_argument("--limit", type=int, default=50, action=_Store)


def _profile(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        action=_Store,
        default=_DEFAULT_PROFILE,
        help="Named connection profile (default: default).",
    )


def _force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action=_Flag, help="Skip interactive confirmation.")


def _reject_secret_arguments(parser: argparse.ArgumentParser) -> None:
    for option in (
        "--api-key",
        "--client-certificate",
        "--client-private-key",
        "--internal-ca",
    ):
        parser.add_argument(option, action=_RejectSecret, help=argparse.SUPPRESS)


def _credential_file_arguments(parser: argparse.ArgumentParser) -> None:
    for option, label in (
        ("--api-key-file", "API key"),
        ("--client-certificate-file", "client certificate PEM"),
        ("--client-private-key-file", "client private key PEM"),
        ("--internal-ca-file", "internal CA bundle PEM"),
    ):
        parser.add_argument(
            option,
            action=_Store,
            metavar="PATH",
            help=f"Read {label} from a current-user-only regular file.",
        )


def _bind(
    parser: argparse.ArgumentParser,
    name: str,
    handler: Any,
    **defaults: Any,
) -> None:
    parser.set_defaults(
        command_name=_Command(name, {"profile": _DEFAULT_PROFILE, **defaults}),
        command_handler=handler,
    )


def _client(context: CommandContext, command: _Command) -> ConversionHttpClient:
    profile = ProfileStore().load(
        validate_profile_name(_required_string(command, "profile"))
    )
    return ConversionHttpClient(profile, timeout=context.timeout_seconds)


def _request(  # noqa: PLR0913 - explicit bounded HTTP request contract
    context: CommandContext,
    command: _Command,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    csrf: bool = False,
    etag: str | None = None,
    idempotency_key: str | None = None,
    encoded_body: bytes | None = None,
    content_type: str | None = None,
) -> ConversionHttpResponse:
    headers: dict[str, str] = {}
    encoded = encoded_body
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded = json.dumps(body, separators=(",", ":")).encode()
    elif content_type is not None:
        headers["Content-Type"] = content_type
    if etag is not None:
        if not etag or any(character in etag for character in "\r\n"):
            raise CliError("invalid_etag", "The resource ETag is invalid.")
        headers["If-Match"] = etag
    if idempotency_key is not None:
        if (
            not idempotency_key
            or len(idempotency_key) > _MAXIMUM_IDEMPOTENCY_KEY_CHARACTERS
            or any(
                ord(character) < _FIRST_VISIBLE_ASCII
                or ord(character) > _LAST_VISIBLE_ASCII
                for character in idempotency_key
            )
        ):
            raise CliError("invalid_idempotency_key", "The idempotency key is invalid.")
        headers["Idempotency-Key"] = idempotency_key
    return _client(context, command).request(
        method, path, csrf=csrf, headers=headers, body=encoded
    )


def _capabilities(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    response = _request(context, command, "GET", "/api/v1/composer/capabilities")
    payload = _object(response, _OK, "composer_capabilities_failed")
    writer.success(
        f"Composer status: {_human(payload.get('status'))}.",
        {"capabilities": payload},
    )


def _list(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    payload = _paginated_get(
        context, command, "/api/v1/composer/connections", "connections"
    )
    connections = payload.get("connections")
    if not isinstance(connections, list) or any(
        not isinstance(item, dict) for item in connections
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    human = "No authorized connections."
    if connections:
        human = "\n".join(_human_connection(item) for item in connections)
    writer.success(human, payload)


def _show(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    connection_id = _id(command)
    payload = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/connections/{connection_id}",
        ),
        _OK,
        "connection_read_failed",
    )
    writer.success(_human_connection(payload), {"connection": payload})


def _create(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    body = _metadata(command, create=True)
    if not _flag(command, "with_credentials") and _has_credential_file(command):
        raise CliError(
            "invalid_request", "Credential files require --with-credentials."
        )
    credentials = (
        _prompt_credentials(context, command)
        if _flag(command, "with_credentials")
        else None
    )
    response = _request(
        context,
        command,
        "POST",
        "/api/v1/composer/connections",
        body=body,
        csrf=True,
    )
    connection = _object(response, _CREATED, "connection_create_failed")
    connection_id = _response_id(connection)
    if credentials is not None:
        try:
            connection = _object(
                _request(
                    context,
                    command,
                    "PUT",
                    f"/api/v1/composer/connections/{connection_id}/credentials",
                    body=credentials,
                    csrf=True,
                    etag=_response_etag(connection),
                ),
                _OK,
                "credential_rotation_failed",
            )
        except CliError as error:
            raise CliError(
                "credential_setup_failed",
                f"Connection {connection_id} was created disabled. "
                "Read its current ETag, then rotate its credentials to finish setup.",
            ) from error
    writer.success(
        f"Created Composer connection {_human(connection_id)}.",
        {"connection": connection},
    )


def _update(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    _write_update(context, writer, command, _metadata(command, create=False))


def _set_enabled(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    enabled = _flag(command, "enabled")
    _write_update(context, writer, command, {"enabled": enabled})


def _select_model(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    _write_update(
        context,
        writer,
        command,
        {"selected_model": _required_string(command, "model")},
    )


def _write_update(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    body: dict[str, Any],
) -> None:
    if not body:
        raise CliError("invalid_request", "At least one update is required.")
    connection_id = _id(command)
    connection = _object(
        _request(
            context,
            command,
            "PATCH",
            f"/api/v1/composer/connections/{connection_id}",
            body=body,
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "connection_update_failed",
    )
    writer.success(
        f"Updated Composer connection {_human(connection_id)}.",
        {"connection": connection},
    )


def _test(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    connection_id = _id(command)
    result = _object(
        _request(
            context,
            command,
            "POST",
            f"/api/v1/composer/connections/{connection_id}/test",
            body={},
            csrf=True,
        ),
        _OK,
        "connection_test_failed",
    )
    writer.success(
        f"Connection test status: {_human(result.get('status'))}.",
        {"test": result},
    )


def _models(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    connection_id = _id(command)
    result = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/connections/{connection_id}/models",
        ),
        _OK,
        "model_discovery_failed",
    )
    models = result.get("models")
    if not isinstance(models, list) or any(
        not isinstance(item, str) for item in models
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    writer.success(
        "No permitted models." if not models else "\n".join(_human(x) for x in models),
        {"models": models},
    )


def _rotate_credentials(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    connection_id = _id(command)
    connection = _object(
        _request(
            context,
            command,
            "PUT",
            f"/api/v1/composer/connections/{connection_id}/credentials",
            body=_prompt_credentials(context, command),
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "credential_rotation_failed",
    )
    writer.success(
        "Credentials rotated; submitted values were not stored by the CLI.",
        {"connection": connection},
    )


def _revoke_credentials(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    connection_id = _id(command)
    _confirm(context, command, "Revoke credentials?")
    connection = _object(
        _request(
            context,
            command,
            "PUT",
            f"/api/v1/composer/connections/{connection_id}/credentials",
            body={"revoke": True},
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "credential_revocation_failed",
    )
    writer.success("Credentials revoked.", {"connection": connection})


def _revoke(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    connection_id = _id(command)
    _confirm(context, command, "Revoke this connection?")
    connection = _object(
        _request(
            context,
            command,
            "DELETE",
            f"/api/v1/composer/connections/{connection_id}",
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "connection_revocation_failed",
    )
    writer.success(
        "Connection revoked; retained owner archives remain available.",
        {"connection": connection},
    )


def _list_personal_permissions(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    payload = _paginated_get(
        context,
        command,
        "/api/v1/composer/personal-permissions",
        "permissions",
    )
    permissions = payload.get("permissions")
    if not isinstance(permissions, list) or any(
        not isinstance(item, dict) for item in permissions
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    human = "No personal-connection permissions."
    if permissions:
        human = "\n".join(_human_permission(item) for item in permissions)
    writer.success(human, payload)


def _show_personal_permission(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    user_id = _user_id(command)
    permission = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/personal-permissions/{user_id}",
        ),
        _OK,
        "personal_permission_read_failed",
    )
    writer.success(_human_permission(permission), {"permission": permission})


def _set_personal_permission(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    user_id = _user_id(command)
    allowed = _flag(command, "allowed")
    _confirm(
        context,
        command,
        "Grant personal connections?" if allowed else "Revoke personal connections?",
    )
    permission = _object(
        _request(
            context,
            command,
            "PUT",
            f"/api/v1/composer/personal-permissions/{user_id}",
            body={"allowed": allowed},
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "personal_permission_update_failed",
    )
    writer.success(
        "Personal connection permission granted."
        if allowed
        else "Personal connection permission revoked.",
        {"permission": permission},
    )


def _list_drafts(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    payload = _paginated_get(context, command, "/api/v1/composer/drafts", "drafts")
    writer.success(
        _human_items(payload["drafts"], ("id", "title", "version"), "No drafts."),
        payload,
    )


def _show_draft(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft = _object(
        _request(context, command, "GET", f"/api/v1/composer/drafts/{_draft(command)}"),
        _OK,
        "draft_read_failed",
    )
    writer.success(_human_record(draft, ("id", "title", "version")), {"draft": draft})


def _create_draft(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    source_path = Path(_required_string(command, "source"))
    maximum_upload_bytes = _maximum_upload_bytes(context, command)
    source = _read_source(source_path, maximum_bytes=maximum_upload_bytes)
    body, content_type = _draft_multipart(
        source,
        source_path.name,
        title=command.values.get("title"),
        content=command.values.get("content"),
    )
    draft = _object(
        _request(
            context,
            command,
            "POST",
            "/api/v1/composer/drafts",
            csrf=True,
            encoded_body=body,
            content_type=content_type,
        ),
        _CREATED,
        "draft_create_failed",
    )
    writer.success(
        f"Created Composer draft {_human(_response_id(draft))}.", {"draft": draft}
    )


def _handoff_draft(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    job_id = _resource_id(command, "job_id", "conversion job")
    title = command.values.get("title")
    draft = _object(
        _request(
            context,
            command,
            "POST",
            f"/api/v1/composer/drafts/from-conversion/{job_id}",
            body={"title": title if isinstance(title, str) else None},
            csrf=True,
        ),
        _CREATED,
        "draft_handoff_failed",
    )
    writer.success(
        f"Created Composer draft {_human(_response_id(draft))}.", {"draft": draft}
    )


def _save_draft(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    draft = _object(
        _request(
            context,
            command,
            "PUT",
            f"/api/v1/composer/drafts/{draft_id}",
            body={
                "title": _required_string(command, "title"),
                "content": _required_string(command, "content", allow_empty=True),
            },
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "draft_save_failed",
    )
    writer.success(f"Saved Composer draft {_human(draft_id)}.", {"draft": draft})


def _list_messages(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    payload = _paginated_get(
        context, command, f"/api/v1/composer/drafts/{draft_id}/messages", "messages"
    )
    writer.success(
        _human_items(payload["messages"], ("id", "role", "created_at"), "No messages."),
        payload,
    )


def _create_message(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    message = _object(
        _request(
            context,
            command,
            "POST",
            f"/api/v1/composer/drafts/{draft_id}/messages",
            body={"content": _required_string(command, "content")},
            csrf=True,
            etag=_required_string(command, "etag"),
            idempotency_key=_required_string(command, "idempotency_key"),
        ),
        _CREATED,
        "message_create_failed",
    )
    writer.success("Composer message added.", {"message": message})


def _start_model_step(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, connection_id = _draft(command), _id(command)
    connection = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/connections/{connection_id}",
        ),
        _OK,
        "connection_read_failed",
    )
    endpoint, model = _approved_connection(connection, connection_id)
    capabilities = _composer_capabilities(context, command)
    request_limit = _capability_value(capabilities, "maximum_model_request_bytes")
    output_limit = _capability_value(capabilities, "maximum_output_tokens")
    output_tokens = command.values.get("max_output_tokens")
    if (
        not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or not 1 <= output_tokens <= output_limit
    ):
        raise CliError(
            "invalid_output_tokens", "Output tokens exceed the service limit."
        )
    content = _model_content(command, maximum_bytes=request_limit)
    body = {
        "connection_id": connection_id,
        "approved_endpoint": endpoint,
        "approved_model": model,
        "content": content,
        "max_output_tokens": output_tokens,
    }
    try:
        encoded = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except UnicodeEncodeError as error:
        raise CliError(
            "invalid_content", "The model text is not valid UTF-8."
        ) from error
    if len(encoded) > request_limit:
        raise CliError(
            "content_too_large", "The model request exceeds the service limit."
        )
    _confirm_model_step(
        context, command, endpoint=endpoint, model=model, content=content
    )
    try:
        response = _request(
            context,
            command,
            "POST",
            f"/api/v1/composer/drafts/{draft_id}/model-steps",
            body=body,
            csrf=True,
            etag=_required_string(command, "etag"),
            idempotency_key=_required_string(command, "idempotency_key"),
        )
    except CliError as error:
        raise CliError(
            "model_step_start_failed", "The model step could not be started."
        ) from error
    step = _model_step_record(
        _safe_model_step_response(response, 202, "model_step_start_failed"),
        draft_id=draft_id,
    )
    if step["connection_id"] != connection_id or step["model_identity"] != model:
        raise CliError("response_invalid", "The service returned an invalid response.")
    writer.success(
        f"Started Composer model step {_human(step['id'])}.", {"model_step": step}
    )


def _status_model_step(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    step_id = _resource_id(command, "step_id", "model step")
    step = _model_step_record(
        _safe_model_step_response(
            _request(
                context,
                command,
                "GET",
                f"/api/v1/composer/drafts/{draft_id}/model-steps/{step_id}",
            ),
            _OK,
            "model_step_read_failed",
        ),
        draft_id=draft_id,
        step_id=step_id,
    )
    writer.success(
        _human_record(step, ("id", "status", "created_at")), {"model_step": step}
    )


def _cancel_model_step(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    step_id = _resource_id(command, "step_id", "model step")
    _confirm(context, command, "Cancel this Composer model step?")
    step = _model_step_record(
        _safe_model_step_response(
            _request(
                context,
                command,
                "DELETE",
                f"/api/v1/composer/drafts/{draft_id}/model-steps/{step_id}",
                csrf=True,
            ),
            _OK,
            "model_step_cancel_failed",
        ),
        draft_id=draft_id,
        step_id=step_id,
    )
    writer.success("Composer model step cancelled.", {"model_step": step})


def _approved_connection(
    connection: dict[str, Any], connection_id: str
) -> tuple[str, str]:
    endpoint, model = connection.get("endpoint"), connection.get("selected_model")
    if (
        connection.get("id") != connection_id
        or connection.get("authorized") is not True
        or connection.get("enabled") is not True
        or not isinstance(endpoint, str)
        or not endpoint
        or not isinstance(model, str)
        or not model
    ):
        raise CliError(
            "response_invalid", "The service returned an invalid connection."
        )
    return endpoint, model


def _model_step_record(
    payload: dict[str, Any], *, draft_id: str, step_id: str | None = None
) -> dict[str, Any]:
    fields = (
        "id",
        "draft_id",
        "connection_id",
        "model_identity",
        "base_version",
        "status",
        "proposal_id",
        "error_code",
        "created_at",
        "updated_at",
    )
    record = {field: payload.get(field) for field in fields}
    try:
        identifier = str(UUID(record["id"]))
        connection_id = str(UUID(record["connection_id"]))
    except (TypeError, ValueError) as error:
        raise CliError(
            "response_invalid", "The service returned an invalid response."
        ) from error
    if (
        identifier != (step_id or identifier)
        or record["draft_id"] != draft_id
        or not isinstance(record["model_identity"], str)
        or not isinstance(record["base_version"], int)
        or isinstance(record["base_version"], bool)
        or not isinstance(record["status"], str)
        or record["status"] not in _MODEL_STEP_STATES
        or not isinstance(record["created_at"], str)
        or not isinstance(record["updated_at"], str)
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    record["id"] = identifier
    record["connection_id"] = connection_id
    proposal_id, error_code = record["proposal_id"], record["error_code"]
    if record["status"] == "completed":
        try:
            record["proposal_id"] = str(UUID(proposal_id))
        except (TypeError, ValueError) as error:
            raise CliError(
                "response_invalid", "The service returned an invalid response."
            ) from error
    elif proposal_id is not None:
        raise CliError("response_invalid", "The service returned an invalid response.")
    if record["status"] == "failed":
        if not isinstance(error_code, str) or error_code not in _MODEL_STEP_ERROR_CODES:
            raise CliError(
                "response_invalid", "The service returned an invalid response."
            )
    elif error_code is not None:
        raise CliError("response_invalid", "The service returned an invalid response.")
    return record


def _safe_model_step_response(
    response: ConversionHttpResponse, expected: int, code: str
) -> dict[str, Any]:
    if response.status != expected or not isinstance(response.payload, dict):
        raise CliError(code, "The model step request failed.")
    return response.payload


def _list_proposals(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    payload = _paginated_get(
        context, command, f"/api/v1/composer/drafts/{draft_id}/proposals", "proposals"
    )
    writer.success(
        _human_items(
            payload["proposals"], ("id", "state", "base_version"), "No proposals."
        ),
        payload,
    )


def _show_proposal(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, proposal_id = _draft(command), _proposal(command)
    proposal = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}",
        ),
        _OK,
        "proposal_read_failed",
    )
    writer.success(
        _human_record(proposal, ("id", "state", "base_version")), {"proposal": proposal}
    )


def _decide_proposal(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, proposal_id = _draft(command), _proposal(command)
    state = _required_string(command, "state")
    decided_value = command.values.get("value")
    if state == "edited" and not isinstance(decided_value, str):
        raise CliError("invalid_request", "Edited proposals require --value.")
    proposal = _object(
        _request(
            context,
            command,
            "POST",
            f"/api/v1/composer/drafts/{draft_id}/proposals/{proposal_id}/decision",
            body={"state": state, "decided_value": decided_value},
            csrf=True,
            etag=_required_string(command, "etag"),
        ),
        _OK,
        "proposal_decision_failed",
    )
    writer.success("Composer proposal decided.", {"proposal": proposal})


def _capture_revision(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    revision = _revision_mutation(
        context,
        command,
        f"/api/v1/composer/drafts/{draft_id}/revisions/from-source",
        "revision_capture_failed",
    )
    writer.success("Composer source revision captured.", {"revision": revision})


def _list_revisions(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id = _draft(command)
    payload = _paginated_get(
        context, command, f"/api/v1/composer/drafts/{draft_id}/revisions", "revisions"
    )
    writer.success(
        _human_items(
            payload["revisions"], ("id", "number", "operation"), "No revisions."
        ),
        payload,
    )


def _show_revision(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, revision_id = _draft(command), _revision(command)
    revision = _object(
        _request(
            context,
            command,
            "GET",
            f"/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}",
        ),
        _OK,
        "revision_read_failed",
    )
    writer.success(
        _human_record(revision, ("id", "number", "operation")), {"revision": revision}
    )


def _download_revision(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, revision_id = _draft(command), _revision(command)
    kind = _required_string(command, "kind")
    destination = Path(_required_string(command, "output"))
    response = _client(context, command).download(
        f"/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/artifacts/{kind}",
        destination,
        overwrite=_flag(command, "force"),
        validate_headers=lambda headers: _validate_revision_headers(
            headers, revision_id
        ),
    )
    if response.status != _OK:
        raise _api_error(response, "revision_download_failed")
    writer.success(
        f"Downloaded Composer artifact to {destination}.",
        {
            "bytes_written": response.bytes_written,
            "kind": kind,
            "output": str(destination),
        },
    )


def _validate_revision_headers(headers: dict[str, str], revision_id: str) -> None:
    if (
        headers.get("x-content-type-options", "").lower() != "nosniff"
        or headers.get("x-composer-revision") != revision_id
    ):
        raise CliError(
            "response_invalid", "The service returned an invalid artifact response."
        )


def _restore_revision(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    draft_id, revision_id = _draft(command), _revision(command)
    revision = _revision_mutation(
        context,
        command,
        f"/api/v1/composer/drafts/{draft_id}/revisions/{revision_id}/restore",
        "revision_restore_failed",
    )
    writer.success("Composer revision restored.", {"revision": revision})


def _revision_mutation(
    context: CommandContext, command: _Command, path: str, fallback: str
) -> dict[str, Any]:
    return _object(
        _request(
            context,
            command,
            "POST",
            path,
            body={},
            csrf=True,
            etag=_required_string(command, "etag"),
            idempotency_key=_required_string(command, "idempotency_key"),
        ),
        _CREATED,
        fallback,
    )


def _metadata(command: _Command, *, create: bool) -> dict[str, Any]:
    keys = ("name", "endpoint", "scope", "identity_mode", "selected_model")
    body = {key: command.values[key] for key in keys if key in command.values}
    for source, target in (
        ("permitted_model", "permitted_models"),
        ("allowed_user", "allowed_user_ids"),
    ):
        if source in command.values:
            body[target] = command.values[source]
    if create:
        body.setdefault("permitted_models", [])
        body.setdefault("allowed_user_ids", [])
        body["enabled"] = False
        body.setdefault("selected_model", None)
    if body.get("scope") == "personal" and body.get("identity_mode") == "shared":
        raise CliError(
            "invalid_identity_mode", "Personal connections require individual identity."
        )
    return body


def _prompt_credentials(context: CommandContext, command: _Command) -> dict[str, str]:
    if context.non_interactive and not _has_credential_file(command):
        raise CliError(
            "interactive_required", "Credential entry requires a secure prompt or file."
        )
    values: dict[str, str] = {}
    files: dict[str, Path] = {}
    for key, label in (
        ("api_key", "API key"),
        ("client_certificate", "Client certificate PEM"),
        ("client_private_key", "Client private key PEM"),
        ("internal_ca", "Internal CA bundle PEM"),
    ):
        path = command.values.get(f"{key}_file")
        if isinstance(path, str):
            credential_path = Path(path)
            _credential_file_metadata(credential_path)
            files[key] = credential_path
        elif (
            key == "api_key"
            and not context.non_interactive
            and sys.stdin.isatty()
            and sys.stderr.isatty()
        ):
            values[key] = _secret(f"{label} (leave blank if unused): ")
    if not files and not any(values.values()):
        raise CliError("credential_required", "Enter at least one credential value.")
    maximum_bytes = _capability_limit(context, command, "maximum_credential_bytes")
    for key, path in files.items():
        values[key] = _read_credential_file(path, maximum_bytes=maximum_bytes)
    submitted = {key: value for key, value in values.items() if value}
    if any(len(value.encode("utf-8")) > maximum_bytes for value in submitted.values()):
        raise CliError(
            "credential_too_large", "The credential exceeds the service limit."
        )
    return submitted


def _has_credential_file(command: _Command) -> bool:
    return any(
        f"{key}_file" in command.values
        for key in (
            "api_key",
            "client_certificate",
            "client_private_key",
            "internal_ca",
        )
    )


def _credential_file_metadata(path: Path) -> os.stat_result:
    try:
        initial = path.lstat()
    except OSError as error:
        raise CliError(
            "credential_file_unreadable", "The credential file could not be read."
        ) from error
    if (
        not stat.S_ISREG(initial.st_mode)
        or initial.st_uid != os.getuid()
        or initial.st_mode & 0o077
    ):
        raise CliError(
            "credential_file_unsafe",
            "Credential files must be regular and accessible only by the current user.",
        )
    return initial


def _read_credential_file(path: Path, *, maximum_bytes: int) -> str:
    initial = _credential_file_metadata(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino)
                != (initial.st_dev, initial.st_ino)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise CliError(
                    "credential_file_unsafe",
                    "Credential files must be regular and accessible only by the current user.",
                )
            value = _read_limited_bytes(stream, maximum_bytes)
    except CliError:
        raise
    except OSError as error:
        raise CliError(
            "credential_file_unreadable", "The credential file could not be read."
        ) from error
    if not value or len(value) > maximum_bytes:
        raise CliError(
            "credential_file_invalid", "The credential file is empty or too large."
        )
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CliError(
            "credential_file_invalid", "The credential file is not UTF-8 text."
        ) from error


def _secret(prompt: str) -> str:
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise CliError(
            "interactive_tty_required", "A secure interactive terminal is required."
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(prompt)
    except (EOFError, getpass.GetPassWarning) as error:
        raise CliError(
            "interactive_tty_required", "A secure interactive terminal is required."
        ) from error


def _model_content(command: _Command, *, maximum_bytes: int) -> str:
    path = command.values.get("content_file")
    if isinstance(path, str):
        content = _read_model_content_file(Path(path), maximum_bytes=maximum_bytes)
    elif _flag(command, "stdin"):
        try:
            content = _read_limited_bytes(sys.stdin.buffer, maximum_bytes)
        except OSError as error:
            raise CliError(
                "content_unreadable", "The model text could not be read."
            ) from error
    else:
        raise CliError("invalid_request", "A model text source is required.")
    if not content:
        raise CliError("invalid_content", "The model text is empty.")
    if len(content) > maximum_bytes:
        raise CliError("content_too_large", "The model text exceeds the service limit.")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CliError(
            "invalid_content", "The model text is not valid UTF-8."
        ) from error


def _read_model_content_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        initial = path.lstat()
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.getuid()
            or initial.st_mode & 0o077
        ):
            raise CliError(
                "content_file_unsafe",
                "Model text files must be regular and accessible only by the current user.",
            )
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            current = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino)
                or current.st_uid != os.getuid()
                or current.st_mode & 0o077
            ):
                raise CliError(
                    "content_file_unsafe",
                    "Model text files must be regular and accessible only by the current user.",
                )
            return _read_limited_bytes(stream, maximum_bytes)
    except CliError:
        raise
    except OSError as error:
        raise CliError(
            "content_unreadable", "The model text could not be read."
        ) from error


def _read_limited_bytes(stream: BinaryIO, maximum_bytes: int) -> bytes:
    remaining = maximum_bytes + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = stream.read(min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _confirm_model_step(
    context: CommandContext,
    command: _Command,
    *,
    endpoint: str,
    model: str,
    content: str,
) -> None:
    if _flag(command, "force"):
        return
    if context.non_interactive or not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise CliError(
            "confirmation_required", "Use --force to approve this model transmission."
        )
    sys.stderr.write(
        f"Destination: {_human(endpoint)}\nModel: {_human(model)}\n"
        f"Text to transmit:\n--- BEGIN TEXT ---\n{content}\n--- END TEXT ---\n"
    )
    sys.stderr.flush()
    _confirm(context, command, "Send this exact text to the model?")


def _confirm(context: CommandContext, command: _Command, prompt: str) -> None:
    if _flag(command, "force"):
        return
    if context.non_interactive or not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise CliError("confirmation_required", "Use --force to confirm this mutation.")
    sys.stderr.write(f"{prompt} [y/N]: ")
    sys.stderr.flush()
    if sys.stdin.readline().strip().casefold() not in {"y", "yes"}:
        raise CliError("confirmation_declined", "Operation cancelled.")


def _object(
    response: ConversionHttpResponse, expected: int, fallback: str
) -> dict[str, Any]:
    if response.status != expected:
        raise _api_error(response, fallback)
    if not isinstance(response.payload, dict):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return response.payload


def _paginated_get(
    context: CommandContext, command: _Command, path: str, key: str
) -> dict[str, Any]:
    offset, limit = _page(command)
    separator = "&" if "?" in path else "?"
    payload = _object(
        _request(
            context,
            command,
            "GET",
            f"{path}{separator}{urlencode({'offset': offset, 'limit': limit})}",
        ),
        _OK,
        f"{key.rstrip('s')}_list_failed",
    )
    items = payload.get(key)
    if (
        not isinstance(items, list)
        or any(not isinstance(item, dict) for item in items)
        or payload.get("offset") != offset
        or payload.get("limit") != limit
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return payload


def _page(command: _Command) -> tuple[int, int]:
    offset, limit = command.values.get("offset"), command.values.get("limit")
    if (
        not isinstance(offset, int)
        or isinstance(offset, bool)
        or offset < 0
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= _MAXIMUM_PAGE_SIZE
    ):
        raise CliError(
            "invalid_pagination",
            "Offset must be non-negative and limit must be between 1 and 100.",
        )
    return offset, limit


def _api_error(response: ConversionHttpResponse, fallback: str) -> CliError:
    if isinstance(response.payload, dict):
        error = response.payload.get("error")
        if isinstance(error, dict):
            code, message = error.get("code"), error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                return CliError(code.lower(), message)
    return CliError(fallback, "The service rejected the request.")


def _id(command: _Command) -> str:
    value = _required_string(command, "connection_id")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CliError(
            "invalid_connection_id", "The connection identifier is invalid."
        ) from error


def _user_id(command: _Command) -> str:
    value = _required_string(command, "user_id")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CliError("invalid_user_id", "The user identifier is invalid.") from error


def _draft(command: _Command) -> str:
    return _resource_id(command, "draft_id", "draft")


def _proposal(command: _Command) -> str:
    return _resource_id(command, "proposal_id", "proposal")


def _revision(command: _Command) -> str:
    return _resource_id(command, "revision_id", "revision")


def _resource_id(command: _Command, key: str, label: str) -> str:
    value = _required_string(command, key)
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CliError(
            f"invalid_{key}", f"The {label} identifier is invalid."
        ) from error


def _response_id(value: dict[str, Any]) -> str:
    identifier = value.get("id")
    if not isinstance(identifier, str):
        raise CliError("response_invalid", "The service returned an invalid response.")
    try:
        return str(UUID(identifier))
    except ValueError as error:
        raise CliError(
            "response_invalid", "The service returned an invalid response."
        ) from error


def _response_etag(value: dict[str, Any]) -> str:
    etag = value.get("etag")
    if (
        not isinstance(etag, str)
        or not etag
        or any(character in etag for character in "\r\n")
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return etag


def _required_string(command: _Command, key: str, *, allow_empty: bool = False) -> str:
    value = command.values.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _flag(command: _Command, key: str) -> bool:
    return command.values.get(key) is True


def _human(value: Any) -> str:
    if not isinstance(value, str):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _human_connection(value: dict[str, Any]) -> str:
    required = ("id", "name", "scope", "status", "endpoint")
    return "\t".join(_human(value.get(key)) for key in required)


def _human_permission(value: dict[str, Any]) -> str:
    user_id = _human(value.get("user_id"))
    username = _human(value.get("username"))
    allowed = value.get("allowed")
    if not isinstance(allowed, bool):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return f"{user_id}\t{username}\t{'allowed' if allowed else 'denied'}"


def _human_record(value: dict[str, Any], keys: tuple[str, ...]) -> str:
    return "\t".join(_human_cell(value.get(key)) for key in keys)


def _human_items(value: Any, keys: tuple[str, ...], empty: str) -> str:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return (
        empty if not value else "\n".join(_human_record(item, keys) for item in value)
    )


def _human_cell(value: Any) -> str:
    if isinstance(value, str):
        return _human(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise CliError("response_invalid", "The service returned an invalid response.")


def _maximum_upload_bytes(context: CommandContext, command: _Command) -> int:
    return _capability_limit(context, command, "maximum_upload_bytes")


def _capability_limit(
    context: CommandContext, command: _Command, field_name: str
) -> int:
    return _capability_value(_composer_capabilities(context, command), field_name)


def _composer_capabilities(
    context: CommandContext, command: _Command
) -> dict[str, Any]:
    return _object(
        _request(context, command, "GET", "/api/v1/composer/capabilities"),
        _OK,
        "composer_capabilities_failed",
    )


def _capability_value(payload: dict[str, Any], field_name: str) -> int:
    maximum = payload.get(field_name)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise CliError("response_invalid", "The service returned an invalid response.")
    return maximum


def _read_source(path: Path, *, maximum_bytes: int) -> bytes:
    if path.suffix.lower() not in {".md", ".docx", ".pptx", ".pdf"}:
        raise CliError("invalid_upload", "The Composer source type is unsupported.")
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise CliError("invalid_upload", "The upload must be a regular file.")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CliError("invalid_upload", "The upload must be a regular file.")
            content = stream.read(maximum_bytes + 1)
    except CliError:
        raise
    except OSError as error:
        raise CliError("upload_unreadable", "The upload could not be read.") from error
    if not content:
        raise CliError("invalid_upload", "The upload file is empty.")
    if len(content) > maximum_bytes:
        raise CliError("upload_too_large", "The upload is too large.")
    return content


def _draft_multipart(
    source: bytes,
    filename: str,
    *,
    title: Any,
    content: Any,
) -> tuple[bytes, str]:
    if any(character in filename for character in '\\"\r\n'):
        raise CliError("invalid_upload", "The source filename is invalid.")
    boundary = f"markweave-{uuid4().hex}"
    while boundary.encode() in source:
        boundary = f"markweave-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in (("title", title), ("content", content)):
        if not isinstance(value, str):
            continue
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            )
        )
    chunks.extend(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="source"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            source,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
