"""HTTP-only conversion submission and job lifecycle commands."""

from __future__ import annotations

import argparse
import errno
import math
import os
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from markweave.cli.commands.conversion_http import (
    ConversionHttpClient,
    ConversionHttpResponse,
)
from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.profiles import ProfileStore, validate_profile_name
from markweave.cli.types import CommandContext

_DEFAULT_PROFILE = "default"
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})
_SUCCESS = 200
_ACCEPTED = 202
_DEFAULT_POLL_INTERVAL = 1.0
_MAX_POLL_INTERVAL = 60.0
_MAX_RETRIES = 5
_MAX_IDEMPOTENCY_KEY_LENGTH = 255
_MAX_PROGRESS = 100
_ASCII_PRINTABLE_MIN = 33
_ASCII_DELETE = 127


@dataclass
class _Request:
    """Parser-local values passed through the stable root-handler seam."""

    command: str
    values: dict[str, Any] = field(default_factory=dict)


class _RequestOption(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        _set_request_value(namespace, self.dest, values)


class _FlagOption(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs) -> None:
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
        _set_request_value(namespace, self.dest, True)


def _set_request_value(namespace: argparse.Namespace, key: str, value: Any) -> None:
    """Copy parser defaults before recording one invocation-local value."""
    request = namespace.command_name
    if isinstance(request, _Request):
        namespace.command_name = _Request(
            request.command, {**request.values, key: value}
        )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register conversion submission and complete owner job lifecycle commands."""
    options = subparsers.add_parser(
        "conversion-options", help="Show authoritative conversion options."
    )
    _configure(options, "conversion-options", _conversion_options)
    _profile_argument(options)

    convert = subparsers.add_parser("convert", help="Submit a conversion.")
    convert.set_defaults(
        command_name=_Request(
            "convert",
            {"profile": _DEFAULT_PROFILE, "output": "docx", "retries": "0"},
        ),
        command_handler=_convert,
    )
    convert.add_argument("source", action=_RequestOption, help="Markdown or ZIP input.")
    convert.add_argument(
        "--output",
        choices=("docx", "pdf", "both"),
        default="docx",
        action=_RequestOption,
        help="Requested result format (default: docx).",
    )
    convert.add_argument("--template-id", action=_RequestOption)
    convert.add_argument("--template-version-id", action=_RequestOption)
    convert.add_argument("--idempotency-key", action=_RequestOption)
    convert.add_argument(
        "--retries",
        default="0",
        action=_RequestOption,
        metavar="COUNT",
        help="Retry ambiguous network failures with the same idempotency key.",
    )
    _profile_argument(convert)

    jobs = subparsers.add_parser("jobs", help="Inspect and manage conversion jobs.")
    job_commands = jobs.add_subparsers(dest="jobs_command", metavar="COMMAND")
    listing = job_commands.add_parser("list", help="List conversion jobs.")
    _configure(
        listing,
        "jobs list",
        _list_jobs,
        defaults={"offset": "0", "limit": "50"},
    )
    listing.add_argument("--offset", default="0", action=_RequestOption)
    listing.add_argument("--limit", default="50", action=_RequestOption)
    _profile_argument(listing)

    show = job_commands.add_parser("show", help="Show one conversion job.")
    _job_command(show, "jobs show", _show_job)

    wait = job_commands.add_parser("wait", help="Wait for one conversion job.")
    _job_command(
        wait,
        "jobs wait",
        _wait_job,
        defaults={"poll_interval": str(_DEFAULT_POLL_INTERVAL)},
    )
    wait.add_argument(
        "--poll-interval",
        default=str(_DEFAULT_POLL_INTERVAL),
        action=_RequestOption,
        metavar="SECONDS",
    )

    cancel = job_commands.add_parser("cancel", help="Cancel one conversion job.")
    _job_command(cancel, "jobs cancel", _cancel_job)

    download = job_commands.add_parser("download", help="Download a conversion result.")
    _download_command(download, "jobs download", _download_result)

    manifest = job_commands.add_parser(
        "manifest", help="Download a conversion manifest."
    )
    _download_command(manifest, "jobs manifest", _download_manifest)


def _configure(
    parser: argparse.ArgumentParser,
    command: str,
    handler,
    *,
    defaults: dict[str, Any] | None = None,
) -> None:
    values = {"profile": _DEFAULT_PROFILE, **(defaults or {})}
    parser.set_defaults(command_name=_Request(command, values), command_handler=handler)


def _profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        default=_DEFAULT_PROFILE,
        action=_RequestOption,
        metavar="NAME",
        help="Named local connection profile (default: default).",
    )


def _job_command(
    parser: argparse.ArgumentParser,
    command: str,
    handler,
    *,
    defaults: dict[str, Any] | None = None,
) -> None:
    _configure(parser, command, handler, defaults=defaults)
    parser.add_argument("job_id", action=_RequestOption, metavar="JOB_ID")
    _profile_argument(parser)


def _download_command(parser: argparse.ArgumentParser, command: str, handler) -> None:
    _configure(parser, command, handler, defaults={"overwrite": False})
    parser.add_argument("job_id", action=_RequestOption, metavar="JOB_ID")
    parser.add_argument("destination", action=_RequestOption, metavar="DESTINATION")
    parser.add_argument(
        "--overwrite",
        action=_FlagOption,
        help="Atomically replace an existing regular destination file.",
    )
    _profile_argument(parser)


def _convert(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    source_path = Path(_string(request, "source"))
    source_kind, source = _read_source(source_path)
    output = _string(request, "output")
    template_id = _optional_uuid(request, "template_id")
    template_version_id = _optional_uuid(request, "template_version_id")
    if (template_id is None) != (template_version_id is None):
        raise CliError(
            "template_pair_required",
            "Template and template-version identifiers must be provided together.",
        )
    idempotency_key = _idempotency_key(request.values.get("idempotency_key"))
    retries = _integer(request, "retries", minimum=0, maximum=_MAX_RETRIES)
    if retries and idempotency_key is None:
        raise CliError(
            "idempotency_key_required",
            "Retries require an explicit idempotency key.",
        )
    client = _client(context, request)
    response: ConversionHttpResponse | None = None
    for attempt in range(retries + 1):
        try:
            response = client.submit(
                source,
                source_kind=source_kind,
                output=output,
                template_id=template_id,
                template_version_id=template_version_id,
                idempotency_key=idempotency_key,
            )
            break
        except CliError as error:
            if error.code != "network_error" or attempt == retries:
                raise
    if response is None:
        raise _invalid_response()
    payload = _payload(
        response, expected_status=_ACCEPTED, fallback="submission_failed"
    )
    job = _job(payload)
    poll_after = _retry_after(response)
    result = {**job, "poll_after_seconds": poll_after}
    if idempotency_key is not None:
        result["idempotency_key"] = idempotency_key
    writer.success(
        f"Submitted job {job['id']}; poll after {poll_after:g} seconds.", result
    )


def _conversion_options(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    response = _client(context, request).options()
    payload = _payload(
        response, expected_status=_SUCCESS, fallback="conversion_options_read_failed"
    )
    options = _validated_conversion_options(payload)
    selection = options["selection_source"].replace("_", " ")
    writer.success(
        f"Upload limit: {options['conversion_upload_max_bytes']} bytes; "
        f"template selection: {selection}.",
        {"conversion_options": options},
    )


def _list_jobs(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    offset = _integer(request, "offset", minimum=0)
    limit = _integer(request, "limit", minimum=1, maximum=100)
    response = _client(context, request).list_jobs(offset=offset, limit=limit)
    payload = _payload(response, expected_status=_SUCCESS, fallback="jobs_list_failed")
    items = payload.get("items")
    total = payload.get("total")
    if not isinstance(items, list) or not isinstance(total, int):
        raise _invalid_response()
    normalized = [_job(item) for item in items]
    result = {**payload, "items": normalized}
    writer.success(f"Listed {len(normalized)} of {total} jobs.", result)


def _show_job(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    job_id = _uuid(request, "job_id")
    response = _client(context, request).get_job(job_id)
    job = _job(_payload(response, expected_status=_SUCCESS, fallback="job_show_failed"))
    writer.success(_job_summary(job), job)


def _wait_job(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    if context.timeout_seconds is None:
        raise CliError(
            "wait_timeout_required",
            "A bounded --timeout is required when waiting for a job.",
        )
    job_id = _uuid(request, "job_id")
    interval = _number(
        request, "poll_interval", minimum=0.01, maximum=_MAX_POLL_INTERVAL
    )
    deadline = time.monotonic() + context.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError("wait_timeout", "The job did not finish before the timeout.")
        response = _client(context, request, timeout=remaining).get_job(job_id)
        job = _job(
            _payload(response, expected_status=_SUCCESS, fallback="job_wait_failed")
        )
        if job["state"] in _TERMINAL_STATES:
            _raise_terminal_failure(job)
            writer.success(_job_summary(job), job)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError("wait_timeout", "The job did not finish before the timeout.")
        time.sleep(min(interval, remaining))


def _cancel_job(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    job_id = _uuid(request, "job_id")
    response = _client(context, request).cancel_job(job_id)
    job = _job(
        _payload(response, expected_status=_SUCCESS, fallback="job_cancel_failed")
    )
    writer.success(_job_summary(job), job)


def _download_result(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    _download(context, writer, request, manifest=False)


def _download_manifest(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    _download(context, writer, request, manifest=True)


def _download(
    context: CommandContext,
    writer: OutputWriter,
    request: _Request,
    *,
    manifest: bool,
) -> None:
    job_id = _uuid(request, "job_id")
    destination = Path(_string(request, "destination"))
    overwrite = request.values.get("overwrite") is True
    client = _client(context, request)
    response = (
        client.download_manifest(job_id, destination, overwrite=overwrite)
        if manifest
        else client.download_result(job_id, destination, overwrite=overwrite)
    )
    if response.status != _SUCCESS:
        raise _api_error(
            response,
            fallback="manifest_download_failed"
            if manifest
            else "result_download_failed",
        )
    if response.bytes_written is None:
        raise _invalid_response()
    kind = "manifest" if manifest else "result"
    payload: dict[str, Any] = {
        "bytes": response.bytes_written,
        "job_id": job_id,
        "status": "downloaded",
        "type": kind,
    }
    if response.correlation_id is not None:
        payload["correlation_id"] = response.correlation_id
    writer.success(f"Downloaded the job {kind}.", payload)


def _client(
    context: CommandContext, request: _Request, *, timeout: float | None = None
) -> ConversionHttpClient:
    profile_name = validate_profile_name(_string(request, "profile"))
    profile = ProfileStore().load(profile_name)
    return ConversionHttpClient(
        profile,
        timeout=context.timeout_seconds if timeout is None else timeout,
    )


def _read_source(path: Path) -> tuple[str, bytes]:
    source_kind = path.suffix.lower().removeprefix(".")
    if source_kind not in {"md", "zip"}:
        raise CliError(
            "source_type_invalid", "The conversion source must be Markdown or ZIP."
        )
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CliError("source_unsafe", "The conversion source is unsafe.")
            if metadata.st_size == 0:
                raise CliError("source_empty", "The conversion source is empty.")
            content = stream.read()
    except CliError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENODEV, errno.ENXIO}:
            raise CliError(
                "source_unsafe", "The conversion source is unsafe."
            ) from error
        raise CliError(
            "source_unavailable", "The conversion source is unavailable."
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not content:
        raise CliError("source_empty", "The conversion source is empty.")
    return source_kind, content


def _payload(
    response: ConversionHttpResponse, *, expected_status: int, fallback: str
) -> dict[str, Any]:
    if response.status != expected_status:
        raise _api_error(response, fallback=fallback)
    if response.payload is None:
        raise _invalid_response()
    return response.payload


def _api_error(response: ConversionHttpResponse, *, fallback: str) -> CliError:
    error = response.payload.get("error") if response.payload is not None else None
    code = fallback
    message = "The service rejected the request."
    if isinstance(error, dict):
        candidate_code = error.get("code")
        candidate_message = error.get("message")
        if isinstance(candidate_code, str) and isinstance(candidate_message, str):
            code = candidate_code.lower()
            message = candidate_message
    if response.correlation_id is not None:
        message = f"{message} Correlation ID: {response.correlation_id}."
    return CliError(code, message)


def _invalid_response() -> CliError:
    return CliError("response_invalid", "The service returned an invalid response.")


def _job(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_response()
    job_id = value.get("id")
    state = value.get("state")
    progress = value.get("progress")
    correlation_id = value.get("correlation_id")
    try:
        canonical_id = str(UUID(job_id)) if isinstance(job_id, str) else None
        canonical_correlation = (
            str(UUID(correlation_id)) if isinstance(correlation_id, str) else None
        )
    except ValueError as error:
        raise _invalid_response() from error
    if (
        canonical_id is None
        or canonical_correlation is None
        or not isinstance(state, str)
        or not isinstance(progress, int)
        or isinstance(progress, bool)
        or not 0 <= progress <= _MAX_PROGRESS
    ):
        raise _invalid_response()
    return {**value, "id": canonical_id, "correlation_id": canonical_correlation}


def _validated_conversion_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_response()
    upload_limit = value.get("conversion_upload_max_bytes")
    source = value.get("selection_source")
    template = value.get("resolved_template")
    version_id = value.get("template_version_id")
    if (
        not isinstance(upload_limit, int)
        or isinstance(upload_limit, bool)
        or upload_limit <= 0
        or source not in {"pandoc_default", "preferred", "system_fallback"}
    ):
        raise _invalid_response()
    if source == "pandoc_default":
        if template is not None or version_id is not None:
            raise _invalid_response()
    else:
        if not isinstance(template, dict) or not isinstance(version_id, str):
            raise _invalid_response()
        canonical_version = _canonical_response_uuid(version_id)
        template = _validated_template_identity(template)
        current_version = template["current_version_id"]
        if canonical_version != current_version:
            raise _invalid_response()
        version_id = canonical_version
    return {
        "conversion_upload_max_bytes": upload_limit,
        "resolved_template": template,
        "template_version_id": version_id,
        "selection_source": source,
    }


def _validated_template_identity(value: dict[str, Any]) -> dict[str, Any]:
    identity = dict(value)
    for key in ("id", "owner_id", "current_version_id"):
        identity[key] = _canonical_response_uuid(value.get(key))
    revision = value.get("revision")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
        or value.get("status") not in {"active", "archived"}
        or any(
            not isinstance(value.get(key), str)
            for key in ("name", "description", "owner_username")
        )
    ):
        raise _invalid_response()
    identity["revision"] = revision
    return identity


def _canonical_response_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_response()
    try:
        canonical = str(UUID(value))
    except ValueError as error:
        raise _invalid_response() from error
    if canonical != value:
        raise _invalid_response()
    return canonical


def _job_summary(job: dict[str, Any]) -> str:
    return f"Job {job['id']} is {job['state']} ({job['progress']}%)."


def _raise_terminal_failure(job: dict[str, Any]) -> None:
    state = job["state"]
    if state == "succeeded":
        return
    message = job.get("error_message")
    code = job.get("error_code")
    correlation = job["correlation_id"]
    if state == "failed" and isinstance(message, str) and isinstance(code, str):
        raise CliError(code.lower(), f"{message} Correlation ID: {correlation}.")
    if state == "failed":
        raise CliError("job_failed", f"The job failed. Correlation ID: {correlation}.")
    messages = {
        "cancelled": "The job was cancelled.",
        "expired": "The job expired before it could be downloaded.",
    }
    raise CliError(f"job_{state}", f"{messages[state]} Correlation ID: {correlation}.")


def _retry_after(response: ConversionHttpResponse) -> float:
    raw = response.headers.get("retry-after")
    try:
        value = float(raw) if raw is not None else math.nan
    except ValueError as error:
        raise _invalid_response() from error
    if not math.isfinite(value) or value <= 0 or value > _MAX_POLL_INTERVAL:
        raise _invalid_response()
    return value


def _string(request: _Request, key: str) -> str:
    value = request.values.get(key)
    if not isinstance(value, str) or not value:
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _uuid(request: _Request, key: str) -> str:
    try:
        return str(UUID(_string(request, key)))
    except ValueError as error:
        raise CliError(
            "identifier_invalid", "The job identifier is invalid."
        ) from error


def _optional_uuid(request: _Request, key: str) -> str | None:
    value = request.values.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CliError("identifier_invalid", "The template identifier is invalid.")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise CliError(
            "identifier_invalid", "The template identifier is invalid."
        ) from error


def _integer(
    request: _Request, key: str, *, minimum: int, maximum: int | None = None
) -> int:
    try:
        value = int(_string(request, key))
    except ValueError as error:
        raise CliError(
            "invalid_request", "The command arguments are invalid."
        ) from error
    if value < minimum or (maximum is not None and value > maximum):
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _number(request: _Request, key: str, *, minimum: float, maximum: float) -> float:
    try:
        value = float(_string(request, key))
    except ValueError as error:
        raise CliError(
            "invalid_request", "The command arguments are invalid."
        ) from error
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise CliError("invalid_request", "The command arguments are invalid.")
    return value


def _idempotency_key(value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_IDEMPOTENCY_KEY_LENGTH
        or any(
            ord(character) < _ASCII_PRINTABLE_MIN or ord(character) == _ASCII_DELETE
            for character in value
        )
    ):
        raise CliError("idempotency_key_invalid", "The idempotency key is invalid.")
    return value
