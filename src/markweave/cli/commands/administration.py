"""HTTP-only administration, audit, and service-inspection commands."""

from __future__ import annotations

import argparse
import getpass
import json
import ssl
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID

from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.profiles import (
    ProfileStore,
    validate_profile_name,
    validate_service_url,
)
from markweave.cli.types import CommandContext, ConnectionProfile

_DEFAULT_PROFILE = "default"
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_AUDIT_LIMIT = 100
_OK = 200
_CREATED = 201
_NO_CONTENT = 204


@dataclass
class _Command:
    """Parser-local values passed through the stable family-handler seam."""

    name: str
    values: dict[str, Any] = field(default_factory=dict)


class _StoreValue(argparse.Action):
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


class _StoreFlag(argparse.Action):
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
        parser.error("Passwords must be entered through the secure prompt.")


@dataclass(frozen=True)
class _Response:
    status: int
    payload: Any = field(repr=False)
    text: str = field(repr=False)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(  # noqa: PLR0913, PLR0917
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        del req, fp, code, msg, headers, newurl
        return None


class _AdministrationClient:
    """Bounded family-local HTTP boundary for JSON and Prometheus responses."""

    def __init__(self, service_url: str, *, timeout: float | None) -> None:
        self._service_url = validate_service_url(service_url, verify_tls=True)
        self._timeout = timeout

    def request(  # noqa: PLR0913 - one explicit HTTP boundary
        self,
        method: str,
        path: str,
        *,
        profile: ConnectionProfile | None = None,
        csrf: bool = False,
        body: Mapping[str, Any] | None = None,
        accept: str = "application/json",
    ) -> _Response:
        headers = {"Accept": accept}
        if profile is not None:
            headers["Cookie"] = profile.session_state or ""
            if csrf:
                headers["X-CSRF-Token"] = profile.csrf_state or ""
        encoded = None
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = Request(  # noqa: S310 - service URL is strictly validated
            f"{self._service_url}{path}",
            data=encoded,
            headers=headers,
            method=method,
        )
        try:
            handlers: list[Any] = [
                HTTPSHandler(context=ssl.create_default_context()),
                _NoRedirects(),
            ]
            if self._service_url.startswith("http://"):
                handlers.append(ProxyHandler({}))
            response = build_opener(*handlers).open(request, timeout=self._timeout)
            try:
                return _decode_response(response.status, response)
            finally:
                response.close()
        except HTTPError as error:
            try:
                return _decode_response(error.code, error)
            finally:
                error.close()
        except (TimeoutError, URLError, OSError) as error:
            raise CliError(
                "network_error", "The service could not be reached."
            ) from error


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the administration, audit, and health surface."""
    users = subparsers.add_parser("users", help="Administer local users.")
    user_commands = users.add_subparsers(dest="users_command", metavar="COMMAND")

    listing = user_commands.add_parser("list", help="List users.")
    _profile_argument(listing)
    _bind(listing, "users list", _list_users)

    create = user_commands.add_parser("create", help="Create a user.")
    create.add_argument("--username", required=True, action=_StoreValue)
    create.add_argument(
        "--require-password-change", action=_StoreFlag, help="Require renewal."
    )
    _mutation_options(create, password=True)
    _profile_argument(create)
    _bind(create, "users create", _create_user)

    for name, help_text, handler in (
        ("activate", "Activate a user.", _activate_user),
        ("deactivate", "Deactivate a user.", _deactivate_user),
    ):
        parser = user_commands.add_parser(name, help=help_text)
        parser.add_argument("user_id", action=_StoreValue, metavar="USER_ID")
        _mutation_options(parser)
        _profile_argument(parser)
        _bind(parser, f"users {name}", handler)

    reset = user_commands.add_parser("reset-password", help="Reset a user password.")
    reset.add_argument("user_id", action=_StoreValue, metavar="USER_ID")
    reset.add_argument(
        "--require-password-change", action=_StoreFlag, help="Require renewal."
    )
    _mutation_options(reset, password=True)
    _profile_argument(reset)
    _bind(reset, "users reset-password", _reset_password)

    renewal = user_commands.add_parser(
        "require-password-change", help="Change the next-login renewal requirement."
    )
    renewal.add_argument("user_id", action=_StoreValue, metavar="USER_ID")
    renewal.add_argument(
        "--clear", action=_StoreFlag, help="Clear rather than require renewal."
    )
    _mutation_options(renewal)
    _profile_argument(renewal)
    _bind(renewal, "users require-password-change", _set_password_requirement)

    audit = subparsers.add_parser("audit", help="Inspect audit records.")
    audit.add_argument("--offset", type=_nonnegative, action=_StoreValue, default=0)
    audit.add_argument("--limit", type=_audit_limit, action=_StoreValue, default=50)
    _profile_argument(audit)
    _bind(audit, "audit", _audit)

    health = subparsers.add_parser("health", help="Inspect service health.")
    health_commands = health.add_subparsers(dest="health_command", metavar="COMMAND")
    for name, help_text, handler in (
        ("live", "Inspect liveness.", _live),
        ("ready", "Inspect readiness.", _ready),
        ("metrics", "Inspect service metrics.", _metrics),
    ):
        parser = health_commands.add_parser(name, help=help_text)
        target = parser.add_mutually_exclusive_group()
        target.add_argument("--url", action=_StoreValue, help="Public service URL.")
        target.add_argument(
            "--profile",
            action=_StoreValue,
            default=_DEFAULT_PROFILE,
            metavar="NAME",
            help="Named connection profile (default: default).",
        )
        _bind(parser, f"health {name}", handler)


def _bind(parser: argparse.ArgumentParser, name: str, handler: Any) -> None:
    values = {"profile": _DEFAULT_PROFILE}
    if name == "audit":
        values.update(offset=0, limit=50)
    parser.set_defaults(command_name=_Command(name, values), command_handler=handler)


def _profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        action=_StoreValue,
        default=_DEFAULT_PROFILE,
        metavar="NAME",
        help="Named connection profile (default: default).",
    )


def _mutation_options(
    parser: argparse.ArgumentParser, *, password: bool = False
) -> None:
    parser.add_argument(
        "--force", action=_StoreFlag, help="Apply without an interactive confirmation."
    )
    if password:
        parser.add_argument("--password", action=_RejectSecret, help=argparse.SUPPRESS)


def _list_users(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    profile, client = _authenticated(context, command)
    response = client.request("GET", "/api/v1/admin/users", profile=profile)
    users = _require_list(response, expected=_OK, fallback="user_list_failed")
    normalized = [_user(item) for item in users]
    human = (
        "No users." if not normalized else "\n".join(_human_user(x) for x in normalized)
    )
    writer.success(human, {"users": normalized})


def _create_user(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    username = _string(command, "username")
    _confirm(context, command, f"Create user {username!r}?")
    password = _new_password(context)
    profile, client = _authenticated(context, command)
    response = client.request(
        "POST",
        "/api/v1/admin/users",
        profile=profile,
        csrf=True,
        body={
            "username": username,
            "password": password,
            "password_change_required": _flag(command, "require_password_change"),
        },
    )
    user = _user(
        _require_object(response, expected=_CREATED, fallback="user_create_failed")
    )
    writer.success(f"Created user {user['username']}.", {"user": user})


def _activate_user(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    _set_active(context, writer, command, active=True)


def _deactivate_user(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    _set_active(context, writer, command, active=False)


def _set_active(
    context: CommandContext, writer: OutputWriter, command: _Command, *, active: bool
) -> None:
    user_id = _user_id(command)
    verb = "Activate" if active else "Deactivate"
    _confirm(context, command, f"{verb} user {user_id}?")
    profile, client = _authenticated(context, command)
    response = client.request(
        "PATCH",
        f"/api/v1/admin/users/{user_id}/active",
        profile=profile,
        csrf=True,
        body={"active": active},
    )
    user = _user(_require_object(response, expected=_OK, fallback="user_update_failed"))
    state = "active" if active else "inactive"
    writer.success(f"User {user['username']} is {state}.", {"user": user})


def _reset_password(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    user_id = _user_id(command)
    _confirm(context, command, f"Reset password for user {user_id}?")
    password = _new_password(context)
    profile, client = _authenticated(context, command)
    response = client.request(
        "POST",
        f"/api/v1/admin/users/{user_id}/password",
        profile=profile,
        csrf=True,
        body={
            "password": password,
            "password_change_required": _flag(command, "require_password_change"),
        },
    )
    _require_status(response, expected=_NO_CONTENT, fallback="password_reset_failed")
    writer.success(
        "Password reset. The password was not displayed.",
        {"status": "password_reset", "user_id": user_id},
    )


def _set_password_requirement(
    context: CommandContext, writer: OutputWriter, command: _Command
) -> None:
    user_id = _user_id(command)
    required = not _flag(command, "clear")
    action = "Require" if required else "Clear"
    _confirm(context, command, f"{action} password renewal for user {user_id}?")
    profile, client = _authenticated(context, command)
    response = client.request(
        "PATCH",
        f"/api/v1/admin/users/{user_id}/password-change-required",
        profile=profile,
        csrf=True,
        body={"required": required},
    )
    user = _user(_require_object(response, expected=_OK, fallback="user_update_failed"))
    state = "required" if required else "not required"
    writer.success(
        f"Password renewal is {state} for {user['username']}.", {"user": user}
    )


def _audit(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    offset = _integer(command, "offset")
    limit = _integer(command, "limit")
    profile, client = _authenticated(context, command)
    query = urlencode({"offset": offset, "limit": limit})
    response = client.request("GET", f"/api/v1/audit?{query}", profile=profile)
    records = _require_list(response, expected=_OK, fallback="audit_list_failed")
    normalized = [_audit_record(item) for item in records]
    human = (
        "No audit records."
        if not normalized
        else "\n".join(_human_audit(record) for record in normalized)
    )
    writer.success(human, {"items": normalized, "limit": limit, "offset": offset})


def _live(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    _health_json(context, writer, command, path="/health/live", label="live")


def _ready(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    _health_json(context, writer, command, path="/health/ready", label="ready")


def _health_json(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    *,
    path: str,
    label: str,
) -> None:
    client = _public_client(context, command)
    response = client.request("GET", path)
    payload = _require_object(response, expected=_OK, fallback=f"health_{label}_failed")
    status = payload.get("status")
    if not isinstance(status, str):
        raise CliError("response_invalid", "The service returned an invalid response.")
    writer.success(f"Service is {status}.", {"status": status})


def _metrics(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    response = _public_client(context, command).request(
        "GET", "/metrics", accept="text/plain"
    )
    _require_status(response, expected=_OK, fallback="metrics_failed")
    metrics = response.text.rstrip("\n")
    if not metrics:
        raise CliError("response_invalid", "The service returned an invalid response.")
    writer.success(metrics, {"metrics": metrics})


def _authenticated(
    context: CommandContext, command: _Command
) -> tuple[ConnectionProfile, _AdministrationClient]:
    name = validate_profile_name(_string(command, "profile"))
    profile = ProfileStore().load(name)
    return profile, _AdministrationClient(
        profile.service_url, timeout=context.timeout_seconds
    )


def _public_client(context: CommandContext, command: _Command) -> _AdministrationClient:
    url = command.values.get("url")
    if isinstance(url, str):
        return _AdministrationClient(url, timeout=context.timeout_seconds)
    profile = ProfileStore().load(validate_profile_name(_string(command, "profile")))
    return _AdministrationClient(profile.service_url, timeout=context.timeout_seconds)


def _decode_response(status: int, response: HTTPResponse | HTTPError) -> _Response:
    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(body) > _MAX_RESPONSE_BYTES:
        raise CliError("response_too_large", "The service response is too large.")
    try:
        text = body.decode("utf-8")
    except UnicodeError as error:
        raise CliError(
            "response_invalid", "The service returned an invalid response."
        ) from error
    payload: Any = None
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return _Response(status, payload, text)


def _api_error(response: _Response, fallback: str) -> CliError:
    if isinstance(response.payload, dict):
        error = response.payload.get("error")
        if isinstance(error, dict):
            code, message = error.get("code"), error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                return CliError(code.lower(), message)
    return CliError(fallback, "The service rejected the request.")


def _require_status(response: _Response, *, expected: int, fallback: str) -> None:
    if response.status != expected:
        raise _api_error(response, fallback)


def _require_object(
    response: _Response, *, expected: int, fallback: str
) -> dict[str, Any]:
    _require_status(response, expected=expected, fallback=fallback)
    if not isinstance(response.payload, dict):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return response.payload


def _require_list(response: _Response, *, expected: int, fallback: str) -> list[Any]:
    _require_status(response, expected=expected, fallback=fallback)
    if not isinstance(response.payload, list):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return response.payload


def _user(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError("response_invalid", "The service returned an invalid response.")
    fields = ("id", "username", "role")
    if not all(isinstance(value.get(key), str) for key in fields) or not all(
        isinstance(value.get(key), bool)
        for key in ("active", "password_change_required")
    ):
        raise CliError("response_invalid", "The service returned an invalid response.")
    return {key: value[key] for key in (*fields, "active", "password_change_required")}


def _audit_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CliError("response_invalid", "The service returned an invalid response.")
    string_fields = (
        "id",
        "actor_id",
        "owner_id",
        "operation",
        "target_id",
        "target_type",
        "created_at",
    )
    if not all(isinstance(value.get(key), str) for key in string_fields):
        raise CliError("response_invalid", "The service returned an invalid response.")
    if not isinstance(value.get("administrator_intervention"), bool):
        raise CliError("response_invalid", "The service returned an invalid response.")
    for key in ("target_version", "version_id"):
        if value.get(key) is not None and not isinstance(value.get(key), str):
            raise CliError(
                "response_invalid", "The service returned an invalid response."
            )
    keys = (
        *string_fields,
        "target_version",
        "version_id",
        "administrator_intervention",
    )
    return {key: value.get(key) for key in keys}


def _human_user(user: Mapping[str, Any]) -> str:
    active = "active" if user["active"] else "inactive"
    renewal = "renewal-required" if user["password_change_required"] else "current"
    return f"{user['id']}\t{user['username']}\t{user['role']}\t{active}\t{renewal}"


def _human_audit(record: Mapping[str, Any]) -> str:
    intervention = "admin" if record["administrator_intervention"] else "owner"
    return (
        f"{record['created_at']}\t{record['operation']}\t{record['target_type']}\t"
        f"{record['target_id']}\t{intervention}"
    )


def _confirm(context: CommandContext, command: _Command, prompt: str) -> None:
    if _flag(command, "force"):
        return
    if context.non_interactive:
        raise CliError("confirmation_required", "Use --force to confirm this mutation.")
    answer = _prompt(context, f"{prompt} [y/N]: ", secret=False)
    if answer.casefold() not in {"y", "yes"}:
        raise CliError("confirmation_declined", "Operation cancelled.")


def _new_password(context: CommandContext) -> str:
    password = _prompt(context, "New password: ", secret=True)
    confirmation = _prompt(context, "Confirm new password: ", secret=True)
    if password != confirmation:
        raise CliError("password_mismatch", "The password confirmation does not match.")
    return password


def _prompt(context: CommandContext, prompt: str, *, secret: bool) -> str:
    if context.non_interactive:
        raise CliError(
            "interactive_required", "This command requires interactive input."
        )
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise CliError(
            "interactive_tty_required", "A secure interactive terminal is required."
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            if secret:
                value = getpass.getpass(prompt)
            else:
                sys.stderr.write(prompt)
                sys.stderr.flush()
                value = sys.stdin.readline().rstrip("\r\n")
    except (EOFError, getpass.GetPassWarning) as error:
        raise CliError("input_required", "A non-empty value is required.") from error
    if not value:
        raise CliError("input_required", "A non-empty value is required.")
    return value


def _user_id(command: _Command) -> str:
    value = _string(command, "user_id")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CliError("invalid_user_id", "The user identifier is invalid.") from error


def _string(command: _Command, key: str) -> str:
    value = command.values.get(key)
    if not isinstance(value, str):
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _integer(command: _Command, key: str) -> int:
    value = command.values.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _flag(command: _Command, key: str) -> bool:
    return command.values.get(key) is True


def _nonnegative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("offset must be non-negative")
    return parsed


def _audit_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= _MAX_AUDIT_LIMIT:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return parsed
