"""Remote template, immutable-version, and preference commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import stat
import urllib.parse
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID, uuid4

from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.profiles import ProfileStore, validate_profile_name
from markweave.cli.types import CommandContext, ConnectionProfile

_DEFAULT_PROFILE = "default"
_JSON_RESPONSE_LIMIT = 1_048_576
_DOCUMENT_LIMIT = 268_435_456
_SHA256_HEX_LENGTH = 64
_MAX_PAGE_SIZE = 100
_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@dataclass
class _Command:
    """Parser-local values passed through T31's stable family-handler seam."""

    name: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Response:
    status: int
    headers: Mapping[str, str]
    payload: Any = field(repr=False)
    content: bytes = field(repr=False)


class _Option(argparse.Action):
    """Store one parsed value in the family command object."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        command = namespace.command_name
        if isinstance(command, _Command):
            command.values[self.dest] = values


class _AppendOption(_Option):
    """Collect repeatable values without leaking parser state between invocations."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        current = list(getattr(namespace, self.dest, None) or ())
        current.append(values)
        setattr(namespace, self.dest, current)
        command = namespace.command_name
        if isinstance(command, _Command):
            command.values[self.dest] = current


class _Flag(_Option):
    """Record an opt-in boolean flag."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        del values
        super().__call__(parser, namespace, True, option_string)


class _NoRedirect(HTTPRedirectHandler):
    """Keep authenticated headers on the configured service origin."""

    def redirect_request(  # noqa: PLR0913, PLR0917 - stdlib signature
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl


def register(  # noqa: PLR0915 - the fixed public family is registered together
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the complete HTTP-only template command surface."""
    parser = subparsers.add_parser(
        "templates", help="Discover and manage document templates."
    )
    commands = parser.add_subparsers(dest="templates_command", metavar="COMMAND")

    listing = _leaf(commands, "list", "List visible templates.")
    _listing_options(listing)

    search = _leaf(commands, "search", "Search visible templates.")
    _listing_options(search)
    search.add_argument("--name", action=_Option, help="Match a normalized name.")
    search.add_argument(
        "--description", action=_Option, help="Match a normalized description."
    )

    show = _leaf(commands, "show", "Show one visible template.")
    _identifier(show, "template_id", "Template UUID.")

    create = _leaf(commands, "create", "Create a template and initial version.")
    create.add_argument("--name", action=_Option, required=True)
    create.add_argument("--description", action=_Option, default="")
    _file(create)
    _fonts(create)

    download = _leaf(commands, "download", "Download the current version.")
    _identifier(download, "template_id", "Template UUID.")
    _download_options(download)

    update = _leaf(commands, "update", "Update template metadata.")
    _identifier(update, "template_id", "Template UUID.")
    update.add_argument("--name", action=_Option, required=True)
    update.add_argument("--description", action=_Option, required=True)
    _etag(update)

    replace = _leaf(commands, "replace", "Publish replacement template content.")
    _identifier(replace, "template_id", "Template UUID.")
    _file(replace)
    _fonts(replace)
    _etag(replace)

    archive = _leaf(commands, "archive", "Archive an active template.")
    _identifier(archive, "template_id", "Template UUID.")
    _etag(archive)
    _force(archive)

    delete = _leaf(commands, "delete", "Permanently delete an archived template.")
    _identifier(delete, "template_id", "Template UUID.")
    _etag(delete)
    _force(delete)

    versions = _leaf(commands, "versions", "List immutable template versions.")
    _identifier(versions, "template_id", "Template UUID.")

    version_download = _leaf(
        commands, "version-download", "Download one immutable template version."
    )
    _identifier(version_download, "template_id", "Template UUID.")
    _identifier(version_download, "version_id", "Version UUID.")
    _download_options(version_download)

    restore = _leaf(commands, "restore", "Copy a historical version forward.")
    _identifier(restore, "template_id", "Template UUID.")
    _identifier(restore, "version_id", "Version UUID.")
    _etag(restore)

    preferred = _leaf(
        commands, "preferred", "Set or clear the current user's preferred template."
    )
    preferred_choice = preferred.add_mutually_exclusive_group(required=True)
    preferred_choice.add_argument(
        "--template-id", action=_Option, type=_uuid, metavar="UUID"
    )
    preferred_choice.add_argument("--clear", action=_Flag)

    fallback = _leaf(
        commands, "fallback", "Set the administrator system fallback template."
    )
    _identifier(fallback, "template_id", "Template UUID.")


def _leaf(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    description: str,
) -> argparse.ArgumentParser:
    parser = commands.add_parser(name, help=description, description=description)
    parser.set_defaults(
        command_name=_Command(name, {"profile": _DEFAULT_PROFILE}),
        command_handler=_run,
    )
    parser.add_argument(
        "--profile",
        action=_Option,
        default=_DEFAULT_PROFILE,
        metavar="NAME",
        help="Named connection profile (default: default).",
    )
    return parser


def _identifier(parser: argparse.ArgumentParser, name: str, help_text: str) -> None:
    parser.add_argument(
        name, action=_Option, type=_uuid, metavar="UUID", help=help_text
    )


def _listing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner-id", action=_Option, type=_uuid, metavar="UUID")
    parser.add_argument("--status", action=_Option, choices=("active", "archived"))
    parser.add_argument("--offset", action=_Option, type=_non_negative, default=0)
    parser.add_argument("--limit", action=_Option, type=_page_size, default=20)


def _file(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--file", action=_Option, required=True, type=Path, metavar="PATH"
    )


def _fonts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--font",
        action=_AppendOption,
        required=True,
        metavar="NAME",
        help="Expected font; repeat for every font used by the template.",
    )


def _etag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--etag",
        action=_Option,
        help="Exact identity ETag; fetch the current ETag when omitted.",
    )


def _force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--force", action=_Flag, help="Skip the interactive destructive-action prompt."
    )


def _download_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output", action=_Option, required=True, type=Path, metavar="PATH"
    )
    parser.add_argument(
        "--force", action=_Flag, help="Atomically replace an existing output file."
    )


def _run(context: CommandContext, writer: OutputWriter, command: _Command) -> None:
    profile_name = validate_profile_name(str(command.values["profile"]))
    profile = ProfileStore().load(profile_name)
    transport = _TemplateTransport(profile, context.timeout_seconds)
    handlers = {
        "list": _list,
        "search": _search,
        "show": _show,
        "create": _create,
        "download": _download,
        "update": _update,
        "replace": _replace,
        "archive": _archive,
        "delete": _delete,
        "versions": _versions,
        "version-download": _version_download,
        "restore": _restore,
        "preferred": _preferred,
        "fallback": _fallback,
    }
    handlers[command.name](context, writer, command, transport)


def _list(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    _write_page(
        writer, _expect_json(transport.request("GET", _list_path(command)), 200)
    )


def _search(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    if not command.values.get("name") and not command.values.get("description"):
        raise CliError(
            "search_term_required", "Search requires --name or --description."
        )
    _write_page(
        writer, _expect_json(transport.request("GET", _list_path(command)), 200)
    )


def _show(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    response = transport.request("GET", _template_path(command))
    payload = _expect_json(response, 200)
    payload["etag"] = _required_header(response, "etag")
    writer.success(_template_summary(payload), payload)


def _create(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    response = transport.request(
        "POST",
        "/api/v1/templates",
        csrf=True,
        multipart=_template_multipart(command, include_metadata=True),
    )
    payload = _expect_json(response, 201)
    payload["etag"] = _required_header(response, "etag")
    writer.success(f"Created template {payload.get('id', '')}.", payload)


def _download(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    _download_to(
        writer,
        command,
        transport.request("GET", f"{_template_path(command)}/content"),
    )


def _update(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    response = transport.request(
        "PATCH",
        _template_path(command),
        csrf=True,
        headers={"If-Match": _mutation_etag(command, transport)},
        json_body={
            "name": str(command.values["name"]),
            "description": str(command.values["description"]),
        },
    )
    payload = _expect_json(response, 200)
    payload["etag"] = _required_header(response, "etag")
    writer.success(f"Updated template {payload.get('id', '')}.", payload)


def _replace(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    response = transport.request(
        "PUT",
        f"{_template_path(command)}/content",
        csrf=True,
        headers={"If-Match": _mutation_etag(command, transport)},
        multipart=_template_multipart(command, include_metadata=False),
    )
    payload = _expect_json(response, 201)
    payload["etag"] = _required_header(response, "etag")
    writer.success(f"Published template version {payload.get('number', '')}.", payload)


def _archive(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    _confirm(context, command, "Archive this template? [y/N] ")
    response = transport.request(
        "POST",
        f"{_template_path(command)}/archive",
        csrf=True,
        headers={"If-Match": _mutation_etag(command, transport)},
    )
    payload = _expect_json(response, 200)
    payload["etag"] = _required_header(response, "etag")
    writer.success(f"Archived template {payload.get('id', '')}.", payload)


def _delete(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    _confirm(context, command, "Permanently delete this archived template? [y/N] ")
    response = transport.request(
        "DELETE",
        _template_path(command),
        csrf=True,
        headers={"If-Match": _mutation_etag(command, transport)},
    )
    _expect_status(response, 204)
    template_id = str(command.values["template_id"])
    writer.success(
        f"Deleted template {template_id}.",
        {"id": template_id, "status": "deleted"},
    )


def _versions(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    payload = _expect_list(
        transport.request("GET", f"{_template_path(command)}/versions"), 200
    )
    human = "\n".join(_version_summary(item) for item in payload) or "No versions."
    writer.success(human, {"items": payload, "total": len(payload)})


def _version_download(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    path = f"{_template_path(command)}/versions/{command.values['version_id']}/content"
    _download_to(writer, command, transport.request("GET", path))


def _restore(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    path = f"{_template_path(command)}/versions/{command.values['version_id']}/restore"
    response = transport.request(
        "POST",
        path,
        csrf=True,
        headers={"If-Match": _mutation_etag(command, transport)},
    )
    payload = _expect_json(response, 201)
    payload["etag"] = _required_header(response, "etag")
    writer.success(f"Restored template version {payload.get('number', '')}.", payload)


def _preferred(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    template_id = command.values.get("template_id")
    if command.values.get("clear"):
        response = transport.request("DELETE", "/api/v1/template-preference", csrf=True)
        _expect_status(response, 204)
        writer.success("Cleared preferred template.", {"preferred_template_id": None})
        return
    response = transport.request(
        "PUT", f"/api/v1/templates/{template_id}/preferred", csrf=True
    )
    _expect_status(response, 204)
    writer.success(
        f"Set preferred template to {template_id}.",
        {"preferred_template_id": str(template_id)},
    )


def _fallback(
    context: CommandContext,
    writer: OutputWriter,
    command: _Command,
    transport: _TemplateTransport,
) -> None:
    del context
    template_id = command.values["template_id"]
    response = transport.request(
        "PUT", f"/api/v1/templates/{template_id}/system-fallback", csrf=True
    )
    _expect_status(response, 204)
    writer.success(
        f"Set system fallback template to {template_id}.",
        {"system_fallback_template_id": str(template_id)},
    )


class _TemplateTransport:
    """Bounded remote boundary for the template family."""

    def __init__(self, profile: ConnectionProfile, timeout: float | None) -> None:
        self._profile = profile
        self._timeout = timeout

    def request(  # noqa: PLR0913 - request components remain explicit
        self,
        method: str,
        path: str,
        *,
        csrf: bool = False,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, str] | None = None,
        multipart: tuple[bytes, str] | None = None,
    ) -> _Response:
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        request_headers["Cookie"] = self._profile.session_state or ""
        if csrf:
            request_headers["X-CSRF-Token"] = self._profile.csrf_state or ""
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        elif multipart is not None:
            data, boundary = multipart
            request_headers["Content-Type"] = (
                f"multipart/form-data; boundary={boundary}"
            )
        request = Request(  # noqa: S310 - ProfileStore validates the service URL
            f"{self._profile.service_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            handlers: list[Any] = [
                HTTPSHandler(context=ssl.create_default_context()),
                _NoRedirect(),
            ]
            if self._profile.service_url.startswith("http://"):
                handlers.append(ProxyHandler({}))
            response = build_opener(*handlers).open(request, timeout=self._timeout)
            try:
                return _decode_response(
                    response.status,
                    response.headers,
                    response.read(_DOCUMENT_LIMIT + 1),
                )
            finally:
                response.close()
        except HTTPError as error:
            try:
                return _decode_response(
                    error.code, error.headers, error.read(_JSON_RESPONSE_LIMIT + 1)
                )
            finally:
                error.close()
        except (TimeoutError, URLError, OSError) as error:
            raise CliError(
                "network_error", "The service could not be reached."
            ) from error


def _decode_response(status: int, headers: Any, content: bytes) -> _Response:
    if len(content) > _DOCUMENT_LIMIT:
        raise CliError("response_too_large", "The service response is too large.")
    normalized = {str(name).lower(): str(value) for name, value in headers.items()}
    payload: Any = None
    media_type = normalized.get("content-type", "").split(";", 1)[0].lower()
    if content and media_type == "application/json":
        if len(content) > _JSON_RESPONSE_LIMIT:
            raise CliError("response_too_large", "The service response is too large.")
        try:
            payload = json.loads(content.decode("utf-8"))
        except UnicodeError, ValueError:
            payload = None
    return _Response(status, normalized, payload, content)


def _list_path(command: _Command) -> str:
    parameters: list[tuple[str, str]] = []
    for key in ("name", "description", "owner_id", "status", "offset", "limit"):
        value = command.values.get(key)
        if value is not None:
            parameters.append((key, str(value)))
    return f"/api/v1/templates?{urllib.parse.urlencode(parameters)}"


def _template_path(command: _Command) -> str:
    return f"/api/v1/templates/{command.values['template_id']}"


def _mutation_etag(command: _Command, transport: _TemplateTransport) -> str:
    supplied = command.values.get("etag")
    if supplied is not None:
        return _validate_etag(str(supplied), str(command.values["template_id"]))
    response = transport.request("GET", _template_path(command))
    _expect_json(response, 200)
    return _validate_etag(
        _required_header(response, "etag"), str(command.values["template_id"])
    )


def _validate_etag(value: str, template_id: str) -> str:
    prefix = f'"template-{template_id}-'
    if not value.startswith(prefix) or not value.endswith('"'):
        raise CliError("invalid_etag", "The template ETag is invalid.")
    try:
        revision = int(value[len(prefix) : -1])
    except ValueError as error:
        raise CliError("invalid_etag", "The template ETag is invalid.") from error
    if revision <= 0:
        raise CliError("invalid_etag", "The template ETag is invalid.")
    return value


def _template_multipart(
    command: _Command, *, include_metadata: bool
) -> tuple[bytes, str]:
    content = _read_upload(command.values["file"])
    fields: list[tuple[str, str]] = []
    if include_metadata:
        fields.extend(
            (
                ("name", str(command.values["name"])),
                ("description", str(command.values.get("description", ""))),
            )
        )
    fields.extend(("expected_fonts", str(font)) for font in command.values["font"])
    boundary = f"markweave-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    chunks.extend(
        (
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="content"; filename="template.docx"\r\n',
            f"Content-Type: {_DOCX_MEDIA_TYPE}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return b"".join(chunks), boundary


def _read_upload(value: Any) -> bytes:
    path = Path(value)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise CliError("invalid_upload", "The upload must be a regular file.")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise CliError("invalid_upload", "The upload must be a regular file.")
            content = stream.read(_DOCUMENT_LIMIT + 1)
    except CliError:
        raise
    except OSError as error:
        raise CliError("upload_unreadable", "The upload could not be read.") from error
    if not content:
        raise CliError("invalid_upload", "The upload file is empty.")
    if len(content) > _DOCUMENT_LIMIT:
        raise CliError("upload_too_large", "The upload is too large.")
    return content


def _download_to(writer: OutputWriter, command: _Command, response: _Response) -> None:
    _expect_status(response, 200)
    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if media_type != _DOCX_MEDIA_TYPE:
        raise CliError(
            "invalid_download", "The service returned an invalid template document."
        )
    etag = _required_header(response, "etag")
    prefix = '"sha256-'
    if not etag.startswith(prefix) or not etag.endswith('"'):
        raise CliError(
            "invalid_download", "The service returned an invalid template document."
        )
    expected = etag[len(prefix) : -1]
    actual = hashlib.sha256(response.content).hexdigest()
    if len(expected) != _SHA256_HEX_LENGTH or expected != actual:
        raise CliError(
            "template_integrity", "The downloaded template failed integrity checks."
        )
    destination = Path(command.values["output"])
    _atomic_write(
        destination, response.content, force=bool(command.values.get("force"))
    )
    payload = {
        "output": str(destination),
        "sha256": actual,
        "size": len(response.content),
    }
    writer.success(f"Downloaded template to {destination}.", payload)


def _atomic_write(path: Path, content: bytes, *, force: bool) -> None:
    directory = path.parent
    directory_descriptor = -1
    temporary: str | None = None
    try:
        directory_descriptor = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        temporary = f".{path.name}.{uuid4().hex}"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(
                temporary,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
        else:
            try:
                os.link(
                    temporary,
                    path.name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise CliError(
                    "output_exists",
                    "The output path already exists; use --force to replace it.",
                ) from error
            os.unlink(temporary, dir_fd=directory_descriptor)
        temporary = None
        os.fsync(directory_descriptor)
    except OSError as error:
        raise CliError(
            "download_write_failed", "The download could not be saved."
        ) from error
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)


def _confirm(context: CommandContext, command: _Command, prompt: str) -> None:
    if command.values.get("force"):
        return
    if context.non_interactive:
        raise CliError(
            "confirmation_required",
            "This destructive operation requires --force in non-interactive mode.",
        )
    try:
        answer = input(prompt)
    except (EOFError, OSError) as error:
        raise CliError(
            "confirmation_required", "Interactive confirmation is required."
        ) from error
    if answer.strip().casefold() not in {"y", "yes"}:
        raise CliError("cancelled", "Operation cancelled.")


def _expect_json(response: _Response, expected: int) -> dict[str, Any]:
    _expect_status(response, expected)
    if not isinstance(response.payload, dict):
        raise CliError("invalid_response", "The service returned an invalid response.")
    return dict(response.payload)


def _expect_list(response: _Response, expected: int) -> list[dict[str, Any]]:
    _expect_status(response, expected)
    if not isinstance(response.payload, list) or not all(
        isinstance(item, dict) for item in response.payload
    ):
        raise CliError("invalid_response", "The service returned an invalid response.")
    return [dict(item) for item in response.payload]


def _expect_status(response: _Response, expected: int) -> None:
    if response.status == expected:
        return
    if isinstance(response.payload, dict):
        error = response.payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if isinstance(code, str) and isinstance(message, str):
                raise CliError(code.lower(), message)
    raise CliError("request_failed", "The service rejected the request.")


def _required_header(response: _Response, name: str) -> str:
    value = response.headers.get(name.lower())
    if value is None:
        raise CliError("invalid_response", "The service returned an invalid response.")
    return value


def _write_page(writer: OutputWriter, payload: dict[str, Any]) -> None:
    items = payload.get("items")
    if not isinstance(items, list):
        raise CliError("invalid_response", "The service returned an invalid response.")
    human = "\n".join(
        _template_summary(item) for item in items if isinstance(item, dict)
    )
    writer.success(human or "No templates.", payload)


def _template_summary(payload: Mapping[str, Any]) -> str:
    return (
        f"{payload.get('id', '')}  {payload.get('status', '')}  "
        f"{payload.get('name', '')}  owner={payload.get('owner_username', '')}"
    )


def _version_summary(payload: Mapping[str, Any]) -> str:
    return (
        f"{payload.get('id', '')}  v{payload.get('number', '')}  "
        f"sha256={payload.get('sha256', '')}"
    )


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a UUID") from error


def _non_negative(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _page_size(value: str) -> int:
    parsed = _non_negative(value)
    if not 1 <= parsed <= _MAX_PAGE_SIZE:
        raise argparse.ArgumentTypeError("must be between 1 and 100")
    return parsed
