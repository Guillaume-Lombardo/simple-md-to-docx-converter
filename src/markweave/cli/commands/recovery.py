"""Guarded production backup and restore commands."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markweave.cli.errors import CliError, unavailable
from markweave.cli.output import OutputWriter
from markweave.cli.types import CommandContext

if TYPE_CHECKING:
    from markweave.config import StorageProfile
    from markweave.recovery_adapters import S3Configuration

_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_STORAGE_PROFILES = ("standalone", "distributed")
_RECOVERY_SERVER_MODULES = ("pydantic", "pydantic_settings")
CONTROL_CHARACTER_LIMIT = 32


class _Invocation:
    """Argument values bound to the handler without changing T31's root contract."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.values: dict[str, Any] = {
            "data_directory": None,
            "database_url_environment": None,
            "s3_bucket": None,
            "s3_endpoint_url": None,
            "s3_region": None,
            "s3_access_key_environment": None,
            "s3_secret_key_environment": None,
            "consistency_proof": None,
            "offline_proof": None,
            "confirmed": False,
            "report_directory": None,
            "evidence_id": None,
            "profile": None,
            "destination": None,
            "source": None,
        }

    def __call__(
        self, context: CommandContext, writer: OutputWriter, _command: str
    ) -> None:
        _require_recovery_backend()
        try:
            if self.operation == "backup":
                _backup(context, writer, self.values)
            else:
                _restore(context, writer, self.values)
        except ModuleNotFoundError:
            raise
        except Exception as error:
            from markweave.recovery_manifest import RecoveryError  # noqa: PLC0415

            if not isinstance(error, RecoveryError):
                raise
            raise CliError("recovery_failed", str(error)) from None


def _require_recovery_backend() -> None:
    """Fail precisely before importing recovery modules from a base-only install."""
    if any(find_spec(module) is None for module in _RECOVERY_SERVER_MODULES):
        raise CliError(
            "optional_dependency_missing",
            "Recovery commands require server dependencies; "
            "install 'markweave[server]'.",
        )


class _BoundStore(argparse.Action):
    def __init__(self, *args: Any, invocation: _Invocation, **kwargs: Any) -> None:
        self._invocation = invocation
        super().__init__(*args, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, option_string
        setattr(namespace, self.dest, values)
        self._invocation.values[self.dest] = values


class _BoundTrue(_BoundStore):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        del parser, values, option_string
        setattr(namespace, self.dest, True)
        self._invocation.values[self.dest] = True


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the stable local recovery-operation commands."""

    backup = _Invocation("backup")
    backup_parser = subparsers.add_parser("backup", help="Create a local backup.")
    backup_parser.set_defaults(command_name="backup", command_handler=backup)
    _profile_arguments(backup_parser, backup, source_name="destination", required=False)
    backup_parser.add_argument(
        "--consistency-proof",
        action=_BoundStore,
        invocation=backup,
        help="Distributed quiescence or provider consistency proof identity.",
    )

    restore = _Invocation("restore")
    restore_parser = subparsers.add_parser("restore", help="Restore a local backup.")
    restore_parser.set_defaults(command_name="restore", command_handler=restore)
    _profile_arguments(restore_parser, restore, source_name="source", required=True)
    restore_parser.add_argument(
        "--offline-proof",
        required=True,
        action=_BoundStore,
        invocation=restore,
        help="Identity of the verified offline or isolated target change window.",
    )
    restore_parser.add_argument(
        "--yes",
        dest="confirmed",
        action=_BoundTrue,
        invocation=restore,
        help="Confirm the isolated restore mutation.",
    )
    restore_parser.add_argument(
        "--report-directory",
        type=Path,
        action=_BoundStore,
        invocation=restore,
        help="Retain an immutable quarterly exercise report.",
    )
    restore_parser.add_argument(
        "--evidence-id",
        action=_BoundStore,
        invocation=restore,
        help="Readiness evidence identity for an exercise report.",
    )


def _profile_arguments(
    parser: argparse.ArgumentParser,
    invocation: _Invocation,
    *,
    source_name: str,
    required: bool,
) -> None:
    parser.add_argument(
        "--profile",
        required=required,
        choices=_STORAGE_PROFILES,
        action=_BoundStore,
        invocation=invocation,
    )
    parser.add_argument(
        f"--{source_name}",
        required=required,
        type=Path,
        action=_BoundStore,
        invocation=invocation,
    )
    parser.add_argument(
        "--data-directory",
        type=Path,
        action=_BoundStore,
        invocation=invocation,
        help="Standalone source or isolated restore data directory.",
    )
    parser.add_argument(
        "--database-url-environment",
        type=_environment_name,
        action=_BoundStore,
        invocation=invocation,
        help="Environment variable containing the PostgreSQL URL.",
    )
    parser.add_argument("--s3-bucket", action=_BoundStore, invocation=invocation)
    parser.add_argument("--s3-endpoint-url", action=_BoundStore, invocation=invocation)
    parser.add_argument("--s3-region", action=_BoundStore, invocation=invocation)
    parser.add_argument(
        "--s3-access-key-environment",
        type=_environment_name,
        action=_BoundStore,
        invocation=invocation,
    )
    parser.add_argument(
        "--s3-secret-key-environment",
        type=_environment_name,
        action=_BoundStore,
        invocation=invocation,
    )


def _backup(
    context: CommandContext, writer: OutputWriter, values: dict[str, Any]
) -> None:
    from markweave.recovery import RECOVERY_TARGETS  # noqa: PLC0415
    from markweave.recovery_service import (  # noqa: PLC0415 - optional CLI backend
        BackupRequest,
        RecoveryService,
    )

    if values["profile"] is None and values["destination"] is None:
        raise unavailable("backup")
    if values["profile"] is None or values["destination"] is None:
        raise _recovery_error("Backup profile and destination are required")
    profile = _storage_profile(values["profile"])
    timeout = context.timeout_seconds or RECOVERY_TARGETS[profile].rto_seconds
    database_url, s3 = _distributed_configuration(profile, values)
    manifest = RecoveryService().backup(
        BackupRequest(
            profile=profile,
            destination=_absolute(values["destination"], "Backup destination"),
            timeout_seconds=timeout,
            data_directory=_optional_absolute(
                values["data_directory"], "Standalone data directory"
            ),
            database_url=database_url,
            s3=s3,
            consistency_proof=values["consistency_proof"],
        )
    )
    writer.success(
        f"Backup {manifest.backup_id} created.",
        {
            "backup_id": manifest.backup_id,
            "created_at": manifest.created_at,
            "profile": manifest.profile,
            "status": "created",
        },
    )


def _restore(
    context: CommandContext, writer: OutputWriter, values: dict[str, Any]
) -> None:
    from markweave.recovery import (  # noqa: PLC0415
        RECOVERY_TARGETS,
        FilesystemRestoreReportStore,
        RestoreExerciseRunner,
    )
    from markweave.recovery_service import (  # noqa: PLC0415 - optional CLI backend
        RecoveryService,
        RestoreRequest,
    )

    profile = _storage_profile(values["profile"])
    source = _absolute(values["source"], "Recovery source")
    manifest = load_and_verify_manifest(source)
    if not values["confirmed"]:
        if context.non_interactive:
            raise _recovery_error("Non-interactive restore requires --yes")
        sys.stderr.write(f"Type backup identity {manifest.backup_id} to continue: ")
        sys.stderr.flush()
        if sys.stdin.readline().strip() != manifest.backup_id:
            raise _recovery_error(
                "Restore confirmation did not match the backup identity"
            )
    report_directory = values["report_directory"]
    evidence_id = values["evidence_id"]
    if (report_directory is None) != (evidence_id is None):
        raise _recovery_error(
            "Exercise report directory and evidence identity are required together"
        )
    timeout = context.timeout_seconds or RECOVERY_TARGETS[profile].rto_seconds
    database_url, s3 = _distributed_configuration(profile, values)
    request = RestoreRequest(
        profile=profile,
        source=source,
        timeout_seconds=timeout,
        offline_proof=values["offline_proof"],
        data_directory=_optional_absolute(
            values["data_directory"], "Standalone restore destination"
        ),
        database_url=database_url,
        s3=s3,
    )
    service = RecoveryService()
    if report_directory is None:
        result = service.restore(request)
        targets_met: bool | None = None
    else:
        report_path = _absolute(report_directory, "Exercise report directory")
        captured: list[Any] = []

        def perform(rto_seconds: float) -> bool:
            captured.append(
                service.restore(
                    replace(request, timeout_seconds=min(timeout, rto_seconds))
                )
            )
            return True

        report = RestoreExerciseRunner(FilesystemRestoreReportStore(report_path)).run(
            profile,
            backup_id=manifest.backup_id,
            evidence_id=evidence_id,
            backup_created_at=manifest_created_at(manifest.created_at),
            restore=perform,
        )
        if not captured:
            raise _recovery_error("Restore exercise did not produce readiness evidence")
        result = captured[0]
        targets_met = report.targets_met
        if not report.targets_met:
            raise _recovery_error("Restore exercise did not meet its recovery targets")
    writer.success(
        f"Backup {result.backup_id} restored and verified.",
        {
            "backup_id": result.backup_id,
            "evidence_id": result.evidence_id,
            "profile": profile.value,
            "status": "verified",
            **({"targets_met": targets_met} if targets_met is not None else {}),
        },
    )


def _distributed_configuration(
    profile: StorageProfile, values: dict[str, Any]
) -> tuple[str | None, S3Configuration | None]:
    from markweave.config import StorageProfile  # noqa: PLC0415
    from markweave.recovery_adapters import (  # noqa: PLC0415 - optional CLI backend
        S3Configuration,
    )

    distributed_values = (
        values["database_url_environment"],
        values["s3_bucket"],
        values["s3_endpoint_url"],
        values["s3_region"],
        values["s3_access_key_environment"],
        values["s3_secret_key_environment"],
    )
    if profile is StorageProfile.STANDALONE:
        if any(value is not None for value in distributed_values):
            raise _recovery_error("Standalone recovery cannot use distributed settings")
        return None, None
    database_environment = values["database_url_environment"]
    bucket = values["s3_bucket"]
    if database_environment is None or bucket is None:
        raise _recovery_error("Distributed recovery settings are incomplete")
    database_url = _secret_environment(database_environment)
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise _recovery_error("Distributed recovery requires PostgreSQL")
    access_environment = values["s3_access_key_environment"]
    secret_environment = values["s3_secret_key_environment"]
    if (access_environment is None) != (secret_environment is None):
        raise _recovery_error(
            "S3 credential environment names must be supplied together"
        )
    access_key = _secret_environment(access_environment) if access_environment else None
    secret_key = _secret_environment(secret_environment) if secret_environment else None
    return database_url, S3Configuration(
        bucket=bucket,
        endpoint_url=values["s3_endpoint_url"],
        region=values["s3_region"],
        access_key_id=access_key,
        secret_access_key=secret_key,
    )


def _secret_environment(name: str) -> str:
    value = os.environ.get(name)
    if (
        value is None
        or not value
        or any(ord(character) < CONTROL_CHARACTER_LIMIT for character in value)
    ):
        raise _recovery_error("A required recovery environment value is unavailable")
    return value


def _environment_name(value: str) -> str:
    if _ENVIRONMENT_NAME.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("environment variable name is invalid")
    return value


def _absolute(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise _recovery_error(f"{label} must be absolute")
    return path


def _optional_absolute(path: Path | None, label: str) -> Path | None:
    return None if path is None else _absolute(path, label)


def manifest_created_at(value: str):
    """Parse the already authenticated manifest timestamp."""

    return datetime.fromisoformat(value)


def load_and_verify_manifest(source: Path) -> Any:
    """Verify a recovery set only after the restore command is selected."""
    from markweave.recovery_manifest import (  # noqa: PLC0415
        load_and_verify_manifest as verify,
    )

    return verify(source)


def _storage_profile(value: object) -> StorageProfile:
    """Resolve the server-side profile only after a recovery command is selected."""
    from markweave.config import StorageProfile  # noqa: PLC0415

    return StorageProfile(value)


def _recovery_error(message: str) -> Exception:
    """Construct the recovery-domain error without importing it for remote commands."""
    from markweave.recovery_manifest import RecoveryError  # noqa: PLC0415

    return RecoveryError(message)
