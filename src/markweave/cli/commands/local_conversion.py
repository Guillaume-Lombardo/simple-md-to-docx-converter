"""Explicit serverless local mode of ``markweave convert`` (D1).

``markweave convert SOURCE --local`` converts the operator's own file through the
service's validation and document-engine adapters without the HTTP API, an
account, persistence, or malware scanning. Without ``--local`` the command keeps
its unchanged HTTP-only submission behavior.
"""

from __future__ import annotations

import argparse
import errno
import os
import signal
import stat
import threading
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markweave.cli.errors import CliError
from markweave.cli.local_outputs import check_destination, publish_outputs
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext, ExitCode

if TYPE_CHECKING:
    from markweave.conversion.local import LocalConversionResult

_LOCAL_DESTS = (
    "local_mode",
    "local_to",
    "local_template",
    "local_output_dir",
    "local_require_pinned",
    "local_overwrite",
)
_FORMATS = ("docx", "pdf")
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_CANCELLATION_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True)
class LocalInvocation:
    """Parser-local state passed through the stable root-handler seam."""

    namespace: argparse.Namespace


class _LocalOption(argparse.Action):
    """Record one local option and route the invocation to the local handler."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, True if self.nargs == 0 else values)
        namespace.command_handler = _convert_locally
        namespace.command_name = LocalInvocation(namespace)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the explicit local mode to the already registered ``convert`` command."""
    convert = subparsers.choices["convert"]
    remote_options: list[tuple[str, str]] = []
    for action in convert._actions:
        if action.option_strings and action.dest != "help":
            # Remote handlers read their own request values, never these attributes;
            # suppressing defaults lets local mode detect explicit remote options.
            action.default = argparse.SUPPRESS
            remote_options.append((action.dest, action.option_strings[-1]))
    convert.set_defaults(local_remote_options=tuple(remote_options))
    _keep_remote_abbreviations(convert)
    group = convert.add_argument_group(
        "local conversion (no server)",
        "Convert the operator's own file on this host with the same validation, "
        "limits, and document engines as the service. Requires the MARKWEAVE_* "
        "conversion settings; see docs/conversion-cli.md.",
    )
    group.add_argument(
        "--local",
        dest="local_mode",
        nargs=0,
        action=_LocalOption,
        help="Convert locally without the HTTP API, an account, or ClamAV.",
    )
    group.add_argument(
        "--to",
        dest="local_to",
        metavar="FORMATS",
        action=_LocalOption,
        help="Comma-separated local outputs: docx, pdf (default: docx).",
    )
    group.add_argument(
        "--template",
        dest="local_template",
        metavar="DOCX",
        action=_LocalOption,
        help="Local Word reference template (default: Pandoc's).",
    )
    group.add_argument(
        "-o",
        "--output-dir",
        dest="local_output_dir",
        metavar="DIRECTORY",
        action=_LocalOption,
        help="Existing directory for local outputs (default: current directory).",
    )
    group.add_argument(
        "--require-pinned",
        dest="local_require_pinned",
        nargs=0,
        action=_LocalOption,
        help="Fail instead of warning when an engine differs from its approved pin.",
    )
    group.add_argument(
        "--overwrite",
        dest="local_overwrite",
        nargs=0,
        action=_LocalOption,
        help="Atomically replace existing regular local output files.",
    )


def _keep_remote_abbreviations(convert: argparse.ArgumentParser) -> None:
    """Resolve prefixes that only local options made ambiguous to the remote option.

    Before local mode, ``--out`` meant ``--output`` and ``--r`` meant ``--retries``.
    Local options still require an unambiguous spelling of their own.
    """
    resolve = convert._get_option_tuples

    def remote_first(option_string: str) -> list[Any]:
        matches = resolve(option_string)
        if len(matches) > 1:
            remote = [match for match in matches if match[0].dest not in _LOCAL_DESTS]
            if len(remote) == 1:
                return remote
        return matches

    setattr(convert, "_get_option_tuples", remote_first)  # noqa: B010


def _convert_locally(
    context: CommandContext, writer: OutputWriter, invocation: LocalInvocation
) -> None:
    namespace = invocation.namespace
    if getattr(namespace, "local_mode", False) is not True:
        raise CliError(
            "local_mode_required",
            "Local conversion options require --local.",
            exit_code=ExitCode.USAGE,
        )
    remote = [
        option
        for dest, option in getattr(namespace, "local_remote_options", ())
        if dest in namespace
    ]
    if remote:
        raise CliError(
            "local_option_unsupported",
            f"{remote[0]} is not supported with --local.",
            exit_code=ExitCode.USAGE,
        )
    formats = _formats(getattr(namespace, "local_to", None))
    source_path = Path(_required_string(namespace, "source"))
    stem, source_kind = _source_identity(source_path)
    template_value = getattr(namespace, "local_template", None)
    template_path = Path(template_value) if template_value is not None else None
    if template_path is not None and template_path.suffix.casefold() != ".docx":
        raise CliError("template_type_invalid", "The local template must be a DOCX.")
    output_directory = Path(getattr(namespace, "local_output_dir", None) or ".")
    overwrite = getattr(namespace, "local_overwrite", False) is True
    require_pinned = getattr(namespace, "local_require_pinned", False) is True

    from markweave.conversion.errors import ConversionError  # noqa: PLC0415
    from markweave.conversion.local import (  # noqa: PLC0415
        LocalConversionError,
        LocalConversionRequest,
        LocalConverter,
        LocalOutput,
    )
    from markweave.conversion.local_settings import (  # noqa: PLC0415
        LocalConfigurationError,
        LocalConversionSettings,
    )

    try:
        settings = LocalConversionSettings.load()
    except LocalConfigurationError as error:
        raise CliError(
            "local_configuration_invalid",
            "Local conversion configuration is missing or invalid: "
            f"{', '.join(error.variables)}.",
        ) from None
    source = _read_file(source_path, settings.conversion_upload_max_bytes, "source")
    template = (
        _read_file(template_path, settings.template_max_archive_bytes, "template")
        if template_path is not None
        else None
    )
    outputs = frozenset(LocalOutput(value) for value in formats)
    names = [f"{stem}.{value}" for value in formats]
    manifest_name = f"{stem}.traceability.json"
    directory = _open_directory(output_directory)
    try:
        for name in (*names, manifest_name):
            check_destination(directory, name, overwrite=overwrite)
        deadline = (
            time.monotonic() + context.timeout_seconds
            if context.timeout_seconds is not None
            else None
        )
        with _Cancellation() as cancelled:
            try:
                result = LocalConverter(settings).convert(
                    LocalConversionRequest(
                        source=source,
                        source_kind=source_kind,
                        outputs=outputs,
                        template=template,
                        require_pinned=require_pinned,
                    ),
                    deadline_monotonic=deadline,
                    cancellation_requested=cancelled.is_set,
                )
            except (LocalConversionError, ConversionError) as error:
                raise _conversion_error(error, cancelled, deadline) from None

            def checkpoint() -> None:
                if cancelled.is_set():
                    raise _cancelled_error()
                if deadline is not None and time.monotonic() >= deadline:
                    raise _timeout_error()

            checkpoint()
            files = [
                (name, result.outputs[LocalOutput(value)])
                for name, value in zip(names, formats, strict=True)
            ]
            publish_outputs(
                directory,
                [*files, (manifest_name, result.manifest)],
                overwrite=overwrite,
                checkpoint=checkpoint,
            )
    finally:
        os.close(directory)
    for warning in result.warnings:
        writer.warning(warning)
    writer.success(
        f"Converted locally to {', '.join(names)}; traceability: {manifest_name}.",
        _payload(result, names, formats, manifest_name),
    )


def _payload(
    result: LocalConversionResult,
    names: list[str],
    formats: tuple[str, ...],
    manifest_name: str,
) -> dict[str, Any]:
    from markweave.conversion.local import LocalOutput  # noqa: PLC0415

    outputs: list[dict[str, Any]] = []
    for name, value in zip(names, formats, strict=True):
        content = result.outputs[LocalOutput(value)]
        outputs.append({"format": value, "file": name, "bytes": len(content)})
    return {
        "mode": "local",
        "status": "succeeded",
        "outputs": outputs,
        "manifest_file": manifest_name,
        "engines": [engine.as_manifest() for engine in result.engines],
        "engines_pinned": result.pinned,
        "warnings": list(result.warnings),
    }


def _formats(value: Any) -> tuple[str, ...]:
    if value is None:
        return ("docx",)
    if not isinstance(value, str):
        raise _usage("The --to value is invalid.")
    selected: list[str] = []
    for item in (part.strip().casefold() for part in value.split(",")):
        if item not in _FORMATS:
            raise _usage("--to accepts a comma-separated list of docx and pdf.")
        if item not in selected:
            selected.append(item)
    return tuple(value for value in _FORMATS if value in selected)


def _source_identity(path: Path) -> tuple[str, Any]:
    from markweave.jobs.models import SourceKind  # noqa: PLC0415

    suffix = path.suffix.casefold()
    if suffix not in {".md", ".zip"}:
        raise CliError(
            "source_type_invalid", "The conversion source must be Markdown or ZIP."
        )
    stem = path.stem
    if (
        not stem
        or stem.startswith(".")
        or any(unicodedata.category(character).startswith("C") for character in stem)
    ):
        raise CliError("source_name_invalid", "The conversion source name is invalid.")
    return stem, SourceKind.MARKDOWN if suffix == ".md" else SourceKind.ARCHIVE


def _read_file(path: Path, limit: int, kind: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, _READ_FLAGS)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise CliError(f"{kind}_unsafe", f"The local {kind} is unsafe.")
            content = stream.read(limit + 1)
    except CliError:
        raise
    except OSError as error:
        if error.errno in {errno.EISDIR, errno.ELOOP, errno.ENODEV, errno.ENXIO}:
            raise CliError(f"{kind}_unsafe", f"The local {kind} is unsafe.") from None
        raise CliError(
            f"{kind}_unavailable", f"The local {kind} is unavailable."
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not content:
        raise CliError(f"{kind}_empty", f"The local {kind} is empty.")
    if len(content) > limit:
        raise CliError(
            f"{kind}_too_large", f"The local {kind} exceeds the configured limit."
        )
    return content


def _open_directory(path: Path) -> int:
    try:
        return os.open(
            path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except OSError:
        raise CliError(
            "output_directory_invalid",
            "The output directory must be an existing real directory.",
        ) from None


class _Cancellation:
    """Turn SIGINT/SIGTERM into cooperative engine cancellation.

    A class-based context manager keeps frozen ``CliError`` exceptions untouched.
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self._previous: dict[int, Any] = {}

    def __enter__(self) -> threading.Event:
        if threading.current_thread() is threading.main_thread():
            for signum in _CANCELLATION_SIGNALS:
                self._previous[signum] = signal.signal(signum, self._request)
        return self.event

    def __exit__(self, *exception: object) -> None:
        while self._previous:
            signum, handler = self._previous.popitem()
            signal.signal(signum, handler)

    def _request(self, signum: int, frame: object) -> None:
        del signum, frame
        self.event.set()


def _conversion_error(
    error: Any, cancelled: threading.Event, deadline: float | None
) -> CliError:
    if cancelled.is_set():
        return _cancelled_error()
    if deadline is not None and time.monotonic() >= deadline:
        return _timeout_error()
    code = getattr(error, "code", "conversion_failed")
    message = str(error) or "The local conversion failed."
    return CliError(str(getattr(code, "value", code)), message)


def _timeout_error() -> CliError:
    return CliError("conversion_timeout", "The local conversion timed out.")


def _cancelled_error() -> CliError:
    return CliError("conversion_cancelled", "The local conversion was cancelled.")


def _required_string(namespace: argparse.Namespace, key: str) -> str:
    value = getattr(namespace, key, None)
    if not isinstance(value, str) or not value:
        raise _usage("The command arguments are invalid.")
    return value


def _usage(message: str) -> CliError:
    return CliError("invalid_request", message, exit_code=ExitCode.USAGE)
