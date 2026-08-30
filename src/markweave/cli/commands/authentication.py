"""Remote authentication commands backed only by the public HTTP API."""

from __future__ import annotations

import argparse
import getpass
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from markweave.cli.errors import CliError
from markweave.cli.http import HttpTransport, api_error
from markweave.cli.output import OutputWriter
from markweave.cli.profiles import (
    ProfileStore,
    validate_profile_name,
    validate_service_url,
)
from markweave.cli.types import CommandContext, ConnectionProfile

_DEFAULT_PROFILE = "default"
_OK = 200
_NO_CONTENT = 204
_UNAUTHORIZED = 401


@dataclass
class _Request:
    """Parser-local authentication options passed through T31's stable handler seam."""

    command: str
    values: dict[str, str] = field(default_factory=dict)


class _RequestOption(argparse.Action):
    """Store a parser option and make it available to the family handler."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        request = namespace.command_name
        if isinstance(request, _Request) and isinstance(values, str):
            request.values[self.dest] = values


class _RejectPasswordArgument(argparse.Action):
    """Reject secret command arguments without reflecting their supplied value."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del namespace, values, option_string
        parser.error("Passwords must be entered through the secure prompt.")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable authentication surface."""
    login = subparsers.add_parser("login", help="Sign in to a remote service.")
    login.add_argument(
        "--url",
        action=_RequestOption,
        required=True,
        help="HTTPS base URL of the service.",
    )
    login.add_argument(
        "--username", action=_RequestOption, help="Local account username."
    )
    _profile_argument(login)
    _reject_password_arguments(login)
    login.set_defaults(
        command_name=_Request("login", {"profile": _DEFAULT_PROFILE}),
        command_handler=_login,
    )

    logout = subparsers.add_parser(
        "logout",
        help="Sign out of a remote service.",
    )
    _profile_argument(logout)
    _reject_password_arguments(logout)
    logout.set_defaults(
        command_name=_Request("logout", {"profile": _DEFAULT_PROFILE}),
        command_handler=_logout,
    )

    whoami = subparsers.add_parser("whoami", help="Show the active remote session.")
    _profile_argument(whoami)
    _reject_password_arguments(whoami)
    whoami.set_defaults(
        command_name=_Request("whoami", {"profile": _DEFAULT_PROFILE}),
        command_handler=_whoami,
    )

    password = subparsers.add_parser(
        "password", help="Manage the current account password."
    )
    password_commands = password.add_subparsers(
        dest="password_command", metavar="COMMAND"
    )
    change = password_commands.add_parser(
        "change", help="Change the current account password."
    )
    _profile_argument(change)
    _reject_password_arguments(change)
    change.set_defaults(
        command_name=_Request("password change", {"profile": _DEFAULT_PROFILE}),
        command_handler=_change_password,
    )


def _profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        action=_RequestOption,
        default=_DEFAULT_PROFILE,
        metavar="NAME",
        help="Named local connection profile (default: default).",
    )


def _reject_password_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--password", action=_RejectPasswordArgument)
    parser.add_argument("--current-password", action=_RejectPasswordArgument)
    parser.add_argument("--new-password", action=_RejectPasswordArgument)


def _login(context: CommandContext, writer: OutputWriter, command: _Request) -> None:
    profile_name = validate_profile_name(_value(command, "profile"))
    service_url = validate_service_url(_value(command, "url"), verify_tls=True)
    username = command.values.get("username") or _prompt(
        context, "Username: ", secret=False
    )
    password = _prompt(context, "Password: ", secret=True)
    response = HttpTransport(
        service_url, verify_tls=True, timeout=context.timeout_seconds
    ).login(username, password)
    if response.status != _OK or response.payload is None or response.session is None:
        raise api_error(response, fallback="login_failed")
    csrf = response.payload.get("csrf_token")
    user = response.payload.get("user")
    if not isinstance(csrf, str) or not csrf or not isinstance(user, dict):
        raise CliError(
            "login_failed", "The service returned an invalid login response."
        )
    ProfileStore().save(
        ConnectionProfile(
            name=profile_name,
            service_url=service_url,
            session_state=response.session,
            csrf_state=csrf,
        )
    )
    username_value = user.get("username")
    safe_username = username_value if isinstance(username_value, str) else "user"
    writer.success(
        f"Signed in as {safe_username}.",
        {"profile": profile_name, "status": "signed_in", "username": safe_username},
    )


def _logout(context: CommandContext, writer: OutputWriter, command: _Request) -> None:
    profile_name = validate_profile_name(_value(command, "profile"))
    store = ProfileStore()
    profile = store.load(profile_name)
    response = _transport(profile, context).logout(profile)
    if response.status not in {204, 401}:
        raise api_error(response, fallback="logout_failed")
    store.delete(profile_name)
    writer.success("Signed out.", {"profile": profile_name, "status": "signed_out"})


def _whoami(context: CommandContext, writer: OutputWriter, command: _Request) -> None:
    profile_name = validate_profile_name(_value(command, "profile"))
    store = ProfileStore()
    profile = store.load(profile_name)
    response = _transport(profile, context).session(profile)
    if response.status != _OK or response.payload is None:
        if response.status == _UNAUTHORIZED:
            store.delete(profile_name)
        raise api_error(response, fallback="session_expired")
    username = response.payload.get("username")
    role = response.payload.get("role")
    renewal = response.payload.get("password_change_required")
    if (
        not isinstance(username, str)
        or not isinstance(role, str)
        or not isinstance(renewal, bool)
    ):
        raise CliError(
            "session_invalid", "The service returned an invalid session response."
        )
    writer.success(
        f"Signed in as {username} ({role}).",
        {
            "password_change_required": renewal,
            "profile": profile_name,
            "role": role,
            "username": username,
        },
    )


def _change_password(
    context: CommandContext, writer: OutputWriter, command: _Request
) -> None:
    profile_name = validate_profile_name(_value(command, "profile"))
    store = ProfileStore()
    profile = store.load(profile_name)
    session = _transport(profile, context).session(profile)
    if session.status != _OK or session.payload is None:
        if session.status == _UNAUTHORIZED:
            store.delete(profile_name)
        raise api_error(session, fallback="session_expired")
    username = session.payload.get("username")
    required = session.payload.get("password_change_required")
    if not isinstance(username, str) or required is not True:
        raise CliError(
            "password_change_not_required",
            "The current session is not restricted for password renewal.",
        )
    current_password = _prompt(context, "Current password: ", secret=True)
    new_password = _prompt(context, "New password: ", secret=True)
    confirmation = _prompt(context, "Confirm new password: ", secret=True)
    if new_password != confirmation:
        raise CliError(
            "password_mismatch", "The new password confirmation does not match."
        )
    renewed_session = _transport(profile, context).login(username, current_password)
    if (
        renewed_session.status != _OK
        or renewed_session.payload is None
        or renewed_session.session is None
    ):
        raise api_error(renewed_session, fallback="current_password_invalid")
    renewed_csrf = renewed_session.payload.get("csrf_token")
    renewed_user = renewed_session.payload.get("user")
    if (
        not isinstance(renewed_csrf, str)
        or not renewed_csrf
        or not isinstance(renewed_user, dict)
        or renewed_user.get("password_change_required") is not True
    ):
        raise CliError(
            "password_change_failed", "The service did not create a renewal session."
        )
    renewal_profile = ConnectionProfile(
        name=profile.name,
        service_url=profile.service_url,
        session_state=renewed_session.session,
        csrf_state=renewed_csrf,
    )
    response = _transport(renewal_profile, context).change_password(
        renewal_profile, new_password, confirmation
    )
    if response.status != _NO_CONTENT:
        raise api_error(response, fallback="password_change_failed")
    store.delete(profile_name)
    writer.success(
        "Password changed. Sign in again.",
        {"profile": profile_name, "status": "password_changed"},
    )


def _transport(profile: ConnectionProfile, context: CommandContext) -> HttpTransport:
    return HttpTransport(
        profile.service_url, verify_tls=True, timeout=context.timeout_seconds
    )


def _prompt(context: CommandContext, prompt: str, *, secret: bool) -> str:
    if context.non_interactive:
        raise CliError(
            "interactive_required", "This command requires interactive input."
        )
    value = getpass.getpass(prompt) if secret else input(prompt)
    if not value:
        raise CliError("input_required", "A non-empty value is required.")
    return value


def _value(command: _Request, key: str) -> str:
    value = command.values.get(key)
    if not isinstance(value, str):
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value
