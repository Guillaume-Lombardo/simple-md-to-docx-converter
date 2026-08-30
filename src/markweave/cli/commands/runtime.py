"""Supported local runtime-operation commands."""

from __future__ import annotations

import argparse

from markweave.cli.errors import CliError
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register runtime commands without importing optional server backends."""
    _leaf(subparsers, "serve", "Run the local HTTP service.", _serve)
    _leaf(subparsers, "worker", "Run a local worker.", _worker)
    _leaf(subparsers, "doctor", "Check local prerequisites.", _doctor)
    _leaf(subparsers, "migrate", "Apply database migrations.", _migrate)


def _leaf(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    handler: object,
) -> None:
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(command_name=name, command_handler=handler)


def _serve(context: CommandContext, writer: OutputWriter, command: str) -> None:
    del context, command
    try:
        from markweave.runtime import run_http_service  # noqa: PLC0415

        profile = run_http_service()
    except SystemExit as error:
        if not isinstance(error.code, int) or error.code == 0:
            raise
        raise CliError("runtime_failure", "Runtime operation failed.") from None
    except Exception as error:
        raise _runtime_error(error) from None
    writer.success(
        "HTTP service stopped.",
        {"command": "serve", "profile": profile.value, "status": "stopped"},
    )


def _worker(context: CommandContext, writer: OutputWriter, command: str) -> None:
    del context, command
    try:
        from markweave.runtime import run_external_worker  # noqa: PLC0415

        run_external_worker()
    except Exception as error:
        raise _runtime_error(error) from None
    writer.success(
        "Worker stopped.",
        {"command": "worker", "profile": "distributed", "status": "stopped"},
    )


def _doctor(context: CommandContext, writer: OutputWriter, command: str) -> None:
    del command
    try:
        from markweave.runtime_diagnostics import (  # noqa: PLC0415
            run_runtime_diagnostics,
        )

        report = run_runtime_diagnostics(timeout_seconds=context.timeout_seconds)
    except Exception as error:
        raise _runtime_error(error) from None
    failed = tuple(check.name for check in report.checks if not check.passed)
    if failed:
        names = ", ".join(failed)
        raise CliError(
            "doctor_failed",
            f"Runtime checks failed: {names}.",
        )
    writer.success(
        f"All {len(report.checks)} runtime checks passed.",
        {
            "checks": [
                {"name": check.name, "status": "passed"} for check in report.checks
            ],
            "profile": report.profile.value,
            "status": "passed",
        },
    )


def _migrate(context: CommandContext, writer: OutputWriter, command: str) -> None:
    del command
    try:
        from markweave.runtime_migrations import (  # noqa: PLC0415
            migrate_configured_profile,
        )

        result = migrate_configured_profile(timeout_seconds=context.timeout_seconds)
    except Exception as error:
        raise _runtime_error(error) from None
    action = "updated" if result.changed else "already current"
    writer.success(
        f"{result.profile.value.capitalize()} database is {action}.",
        {
            "changed": result.changed,
            "command": "migrate",
            "profile": result.profile.value,
            "revision": result.current_revision,
            "status": "completed",
        },
    )


def _runtime_error(error: Exception) -> CliError:
    """Map runtime boundaries to one redacted operational failure."""
    from markweave.config import ConfigurationError  # noqa: PLC0415
    from markweave.persistence.errors import PersistenceError  # noqa: PLC0415

    if isinstance(error, ConfigurationError):
        return CliError("invalid_configuration", "Invalid application configuration.")
    if isinstance(error, PersistenceError):
        return CliError("persistence_failure", "Persistence operation failed.")
    if isinstance(error, TimeoutError):
        return CliError("operation_timeout", "The operation timed out.")
    return CliError("runtime_failure", "Runtime operation failed.")
