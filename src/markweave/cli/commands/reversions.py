"""HTTP-only reverse-conversion job commands."""

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
_SUCCESS = 200
_ACCEPTED = 202
_DEFAULT_POLL_INTERVAL = 1.0
_MAX_POLL_INTERVAL = 60.0
_MAX_RETRIES = 5
_MAX_IDEMPOTENCY_KEY_LENGTH = 255
_ASCII_PRINTABLE_MIN = 33
_ASCII_DELETE = 127
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "expired"})
_STATES = _TERMINAL_STATES | {"queued", "running"}
_RESULT_MODES = frozenset(
    {"markdown", "markdown_with_assets", "markdown_with_unavailable_assets"}
)


@dataclass
class _Request:
    """Parser-local reverse command values."""

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
    request = namespace.command_name
    if isinstance(request, _Request):
        namespace.command_name = _Request(
            request.command, {**request.values, key: value}
        )


def register(parser: argparse.ArgumentParser) -> None:
    """Register reverse operations below the stable jobs command family."""

    commands = parser.add_subparsers(dest="reverse_command", metavar="COMMAND")

    capabilities = commands.add_parser(
        "capabilities", help="Show authoritative reverse-conversion capabilities."
    )
    _configure(capabilities, "jobs reverse capabilities", _capabilities)
    _profile_argument(capabilities)

    submit = commands.add_parser("submit", help="Submit a reverse conversion.")
    _configure(
        submit,
        "jobs reverse submit",
        _submit,
        defaults={"retries": "0"},
    )
    submit.add_argument("source", action=_RequestOption, help="Supported input file.")
    submit.add_argument("--idempotency-key", action=_RequestOption)
    submit.add_argument(
        "--retries",
        default="0",
        action=_RequestOption,
        metavar="COUNT",
        help="Retry ambiguous network failures with the same idempotency key.",
    )
    _profile_argument(submit)

    listing = commands.add_parser("list", help="List reverse-conversion jobs.")
    _configure(
        listing,
        "jobs reverse list",
        _list_jobs,
        defaults={"offset": "0", "limit": "50"},
    )
    listing.add_argument("--offset", default="0", action=_RequestOption)
    listing.add_argument("--limit", default="50", action=_RequestOption)
    _profile_argument(listing)

    show = commands.add_parser("show", help="Show one reverse-conversion job.")
    _job_command(show, "jobs reverse show", _show_job)

    wait = commands.add_parser("wait", help="Wait for one reverse-conversion job.")
    _job_command(
        wait,
        "jobs reverse wait",
        _wait_job,
        defaults={"poll_interval": str(_DEFAULT_POLL_INTERVAL)},
    )
    wait.add_argument(
        "--poll-interval",
        default=str(_DEFAULT_POLL_INTERVAL),
        action=_RequestOption,
        metavar="SECONDS",
    )

    cancel = commands.add_parser("cancel", help="Cancel one reverse-conversion job.")
    _job_command(cancel, "jobs reverse cancel", _cancel_job)

    download = commands.add_parser(
        "download", help="Download a reverse-conversion result."
    )
    _configure(
        download,
        "jobs reverse download",
        _download_result,
        defaults={"overwrite": False},
    )
    download.add_argument("job_id", action=_RequestOption, metavar="JOB_ID")
    download.add_argument("destination", action=_RequestOption, metavar="DESTINATION")
    download.add_argument(
        "--overwrite",
        action=_FlagOption,
        help="Atomically replace an existing regular destination file.",
    )
    _profile_argument(download)


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


def _capabilities(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    response = _client(context, request).reversion_capabilities()
    payload = _payload(
        response, expected_status=_SUCCESS, fallback="reversion_capabilities_failed"
    )
    capabilities, _extensions = _validated_capabilities(payload)
    writer.success(
        "Reverse conversion supports "
        f"{len(capabilities['format_families'])} format families with a "
        f"{capabilities['maximum_upload_bytes']}-byte upload limit.",
        {"reversion_capabilities": capabilities},
    )


def _submit(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    client = _client(context, request)
    capabilities_payload = _payload(
        client.reversion_capabilities(),
        expected_status=_SUCCESS,
        fallback="reversion_capabilities_failed",
    )
    capabilities, extensions = _validated_capabilities(capabilities_payload)
    source_path = Path(_string(request, "source"))
    source = _read_source(
        source_path,
        maximum_bytes=capabilities["maximum_upload_bytes"],
        extensions=extensions,
    )
    idempotency_key = _idempotency_key(request.values.get("idempotency_key"))
    retries = _integer(request, "retries", minimum=0, maximum=_MAX_RETRIES)
    if retries and idempotency_key is None:
        raise CliError(
            "idempotency_key_required",
            "Retries require an explicit idempotency key.",
        )
    for attempt in range(retries + 1):
        try:
            response = client.submit_reversion(
                source,
                filename=source_path.name,
                idempotency_key=idempotency_key,
            )
            break
        except CliError as error:
            if error.code != "network_error" or attempt == retries:
                raise
    job = _job(
        _payload(
            response, expected_status=_ACCEPTED, fallback="reversion_submit_failed"
        )
    )
    poll_after = _retry_after(response)
    result = {**job, "poll_after_seconds": poll_after}
    if idempotency_key is not None:
        result["idempotency_key"] = idempotency_key
    writer.success(
        f"Submitted reverse job {job['id']}; poll after {poll_after:g} seconds.",
        result,
    )


def _list_jobs(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    offset = _integer(request, "offset", minimum=0)
    limit = _integer(request, "limit", minimum=1, maximum=100)
    response = _client(context, request).list_reversions(offset=offset, limit=limit)
    payload = _payload(
        response, expected_status=_SUCCESS, fallback="reversions_list_failed"
    )
    items = payload.get("items")
    total = payload.get("total")
    if not isinstance(items, list) or type(total) is not int or total < 0:
        raise _invalid_response()
    normalized = [_job(item) for item in items]
    writer.success(
        f"Listed {len(normalized)} of {total} reverse jobs.",
        {**payload, "items": normalized},
    )


def _show_job(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    job_id = _uuid(request, "job_id")
    response = _client(context, request).get_reversion(job_id)
    job = _job(
        _payload(response, expected_status=_SUCCESS, fallback="reversion_show_failed")
    )
    writer.success(_job_summary(job), job)


def _wait_job(context: CommandContext, writer: OutputWriter, request: _Request) -> None:
    if context.timeout_seconds is None:
        raise CliError(
            "wait_timeout_required",
            "A bounded --timeout is required when waiting for a reverse job.",
        )
    job_id = _uuid(request, "job_id")
    interval = _number(
        request, "poll_interval", minimum=0.01, maximum=_MAX_POLL_INTERVAL
    )
    deadline = time.monotonic() + context.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError(
                "wait_timeout", "The reverse job did not finish before the timeout."
            )
        response = _client(context, request, timeout=remaining).get_reversion(job_id)
        job = _job(
            _payload(
                response, expected_status=_SUCCESS, fallback="reversion_wait_failed"
            )
        )
        if job["state"] in _TERMINAL_STATES:
            _raise_terminal_failure(job)
            writer.success(_job_summary(job), job)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliError(
                "wait_timeout", "The reverse job did not finish before the timeout."
            )
        time.sleep(min(interval, remaining))


def _cancel_job(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    job_id = _uuid(request, "job_id")
    response = _client(context, request).cancel_reversion(job_id)
    job = _job(
        _payload(response, expected_status=_SUCCESS, fallback="reversion_cancel_failed")
    )
    writer.success(_job_summary(job), job)


def _download_result(
    context: CommandContext, writer: OutputWriter, request: _Request
) -> None:
    job_id = _uuid(request, "job_id")
    destination = Path(_string(request, "destination"))
    response = _client(context, request).download_reversion_result(
        job_id,
        destination,
        overwrite=request.values.get("overwrite") is True,
    )
    if response.status != _SUCCESS:
        raise _api_error(response, fallback="reversion_result_download_failed")
    if response.bytes_written is None:
        raise _invalid_response()
    payload: dict[str, Any] = {
        "bytes": response.bytes_written,
        "job_id": job_id,
        "status": "downloaded",
        "type": "reversion_result",
    }
    if response.correlation_id is not None:
        payload["correlation_id"] = response.correlation_id
    writer.success("Downloaded the reverse-job result.", payload)


def _client(
    context: CommandContext, request: _Request, *, timeout: float | None = None
) -> ConversionHttpClient:
    profile = ProfileStore().load(validate_profile_name(_string(request, "profile")))
    return ConversionHttpClient(
        profile,
        timeout=context.timeout_seconds if timeout is None else timeout,
    )


def _read_source(
    path: Path, *, maximum_bytes: int, extensions: frozenset[str]
) -> bytes:
    if path.suffix.casefold() not in extensions:
        raise CliError(
            "source_type_invalid",
            "The source extension is not supported by the connected service.",
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
                raise CliError("source_unsafe", "The reverse source is unsafe.")
            if metadata.st_size <= 0:
                raise CliError("source_empty", "The reverse source is empty.")
            if metadata.st_size > maximum_bytes:
                raise CliError(
                    "source_too_large", "The reverse source exceeds the upload limit."
                )
            content = stream.read(maximum_bytes + 1)
    except CliError:
        raise
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENODEV, errno.ENXIO}:
            raise CliError("source_unsafe", "The reverse source is unsafe.") from error
        raise CliError(
            "source_unavailable", "The reverse source is unavailable."
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not content:
        raise CliError("source_empty", "The reverse source is empty.")
    if len(content) > maximum_bytes:
        raise CliError(
            "source_too_large", "The reverse source exceeds the upload limit."
        )
    return content


def _validated_capabilities(
    value: Any,
) -> tuple[dict[str, Any], frozenset[str]]:
    if not isinstance(value, dict):
        raise _invalid_response()
    schema_version = value.get("schema_version")
    maximum = value.get("maximum_upload_bytes")
    families = value.get("format_families")
    modes = value.get("result_package_modes")
    admission = value.get("admission")
    pdf = value.get("pdf")
    execution = value.get("execution")
    if (
        type(schema_version) is not int
        or schema_version <= 0
        or type(maximum) is not int
        or maximum <= 0
        or not isinstance(families, list)
        or not families
        or not isinstance(modes, list)
        or not modes
        or not set(modes) <= _RESULT_MODES
        or not isinstance(admission, dict)
        or not isinstance(pdf, dict)
        or not isinstance(execution, dict)
        or execution.get("local") is not True
        or execution.get("ocr") is not False
        or execution.get("hosted_fallback") is not False
    ):
        raise _invalid_response()
    extensions: list[str] = []
    for family in families:
        if not isinstance(family, dict):
            raise _invalid_response()
        family_extensions = family.get("extensions")
        if (
            not isinstance(family.get("family"), str)
            or not isinstance(family_extensions, list)
            or not family_extensions
            or any(
                not isinstance(extension, str)
                or not extension.startswith(".")
                or extension != extension.casefold()
                for extension in family_extensions
            )
        ):
            raise _invalid_response()
        extensions.extend(family_extensions)
    if len(set(extensions)) != len(extensions):
        raise _invalid_response()
    return dict(value), frozenset(extensions)


def _job(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid_response()
    try:
        job_id = _canonical_uuid(value.get("id"))
        owner_id = _canonical_uuid(value.get("owner_id"))
        correlation_id = _canonical_uuid(value.get("correlation_id"))
    except (TypeError, ValueError) as error:
        raise _invalid_response() from error
    state = value.get("state")
    step = value.get("step")
    attempt = value.get("attempt")
    cancel_requested = value.get("cancel_requested")
    if (
        state not in _STATES
        or not isinstance(step, str)
        or not step
        or type(attempt) is not int
        or attempt < 0
        or type(cancel_requested) is not bool
    ):
        raise _invalid_response()
    return {
        **value,
        "id": job_id,
        "owner_id": owner_id,
        "correlation_id": correlation_id,
    }


def _canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError
    canonical = str(UUID(value))
    if canonical != value:
        raise ValueError
    return canonical


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
    message = "The service rejected the reverse-conversion request."
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


def _job_summary(job: dict[str, Any]) -> str:
    return f"Reverse job {job['id']} is {job['state']} ({job['step']})."


def _raise_terminal_failure(job: dict[str, Any]) -> None:
    state = job["state"]
    if state == "succeeded":
        return
    correlation = job["correlation_id"]
    message = job.get("error_message")
    code = job.get("error_code")
    if state == "failed" and isinstance(message, str) and isinstance(code, str):
        raise CliError(code.lower(), f"{message} Correlation ID: {correlation}.")
    messages = {
        "failed": "The reverse job failed.",
        "cancelled": "The reverse job was cancelled.",
        "expired": "The reverse job expired before it could be downloaded.",
    }
    raise CliError(
        f"reversion_{state}", f"{messages[state]} Correlation ID: {correlation}."
    )


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
            "identifier_invalid", "The reverse-job identifier is invalid."
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
