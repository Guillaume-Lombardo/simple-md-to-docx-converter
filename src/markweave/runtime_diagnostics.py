"""Bounded, non-mutating, and redacted runtime diagnostics."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import boto3
from botocore.config import Config
from sqlalchemy import text

from markweave.config import MalwareScanningMode, Settings, StorageProfile
from markweave.persistence.sql import create_database_engine

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SCANNER_RESPONSE_BYTES = 64
DEFAULT_DIAGNOSTIC_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """One fixed-name diagnostic outcome without environment values."""

    name: str
    passed: bool


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Complete bounded report for one validated storage profile."""

    profile: StorageProfile
    checks: tuple[DiagnosticCheck, ...]


class _Budget:
    def __init__(self, seconds: float) -> None:
        self._deadline = monotonic() + seconds

    def remaining(self) -> float:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining


def run_runtime_diagnostics(
    settings: Settings | None = None, *, timeout_seconds: float | None = None
) -> DiagnosticReport:
    """Run every required check once within one overall time budget."""
    resolved = settings or Settings.load()
    budget = _Budget(timeout_seconds or DEFAULT_DIAGNOSTIC_TIMEOUT_SECONDS)
    checks: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("configuration", lambda: True),
        ("engines", lambda: _check_engines(resolved, budget)),
        ("fonts", lambda: _check_fonts(resolved, budget)),
        ("scanner", lambda: _check_scanner(resolved, budget)),
        ("storage", lambda: _check_storage(resolved, budget)),
        ("permissions", lambda: _check_permissions(resolved)),
        ("runtime", _check_runtime),
    )
    outcomes: list[DiagnosticCheck] = []
    for name, check in checks:
        try:
            passed = check()
        except Exception:
            passed = False
        outcomes.append(DiagnosticCheck(name=name, passed=passed))
    return DiagnosticReport(profile=resolved.storage_profile, checks=tuple(outcomes))


def _check_engines(settings: Settings, budget: _Budget) -> bool:
    executables = (
        settings.template_pandoc_executable,
        settings.template_libreoffice_executable,
        settings.conversion_mermaid_executable,
        settings.conversion_chromium_executable,
    )
    return all(_executable_works(value, budget) for value in executables)


def _check_fonts(settings: Settings, budget: _Budget) -> bool:
    manifest = settings.conversion_font_manifest_path
    try:
        if not manifest.is_file():
            return False
        with manifest.open("rb") as stream:
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(content) > MAX_MANIFEST_BYTES:
            return False
        payload = json.loads(content)
    except OSError, ValueError, TypeError:
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        return False
    return _executable_works("fc-list", budget)


def _check_scanner(settings: Settings, budget: _Budget) -> bool:
    if settings.insecure_evaluation_mode:
        return True
    if settings.malware_scanning_mode is MalwareScanningMode.TRUSTED_UPSTREAM:
        return True
    timeout = min(settings.clamav_timeout_seconds, budget.remaining())
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port), timeout=timeout
        ) as connection:
            connection.settimeout(min(timeout, budget.remaining()))
            connection.sendall(b"zPING\0")
            response = connection.recv(MAX_SCANNER_RESPONSE_BYTES + 1)
    except OSError, TimeoutError:
        return False
    return response in {b"PONG\0", b"PONG\n"}


def _check_storage(settings: Settings, budget: _Budget) -> bool:
    if settings.storage_profile is StorageProfile.STANDALONE:
        directory = settings.standalone_data_directory
        if directory is None:
            return False
        database = directory / "metadata.sqlite3"
        objects = directory / "objects"
        if not database.is_file() or not objects.is_dir():
            return False
        connection = sqlite3.connect(
            f"file:{database}?mode=ro", uri=True, timeout=budget.remaining()
        )
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return True

    secret = settings.distributed_database_url
    bucket = settings.s3_bucket
    if secret is None or bucket is None:
        return False
    engine = create_database_engine(
        secret.get_secret_value(), timeout_seconds=budget.remaining()
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()

    options: dict[str, object] = {
        "config": Config(
            connect_timeout=budget.remaining(),
            read_timeout=budget.remaining(),
            retries={"max_attempts": 0},
        )
    }
    if settings.s3_endpoint_url is not None:
        options["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_region is not None:
        options["region_name"] = settings.s3_region
    if settings.s3_access_key_id is not None:
        options["aws_access_key_id"] = settings.s3_access_key_id.get_secret_value()
        options["aws_secret_access_key"] = (
            settings.s3_secret_access_key.get_secret_value()
            if settings.s3_secret_access_key is not None
            else ""
        )
    boto3.client("s3", **options).head_bucket(Bucket=bucket)
    return True


def _check_permissions(settings: Settings) -> bool:
    paths: tuple[Path, ...]
    if settings.storage_profile is StorageProfile.STANDALONE:
        directory = settings.standalone_data_directory
        if directory is None:
            return False
        paths = (directory, directory / "objects")
    else:
        paths = ()
    workspace = settings.template_engine_workspace_root
    if workspace is not None:
        paths += (workspace,)
    return all(
        path.is_dir() and os.access(path, os.R_OK | os.W_OK | os.X_OK) for path in paths
    )


def _check_runtime() -> bool:
    temporary = Path(
        os.environ.get("TMPDIR", "/tmp")  # noqa: S108 - container runtime contract
    )
    return os.geteuid() != 0 and temporary.is_dir() and os.access(temporary, os.W_OK)


def _executable_works(value: str, budget: _Budget) -> bool:
    resolved = shutil.which(value)
    if resolved is None:
        return False
    try:
        completed = subprocess.run(  # noqa: S603 - resolved executable, fixed argument
            (resolved, "--version"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=budget.remaining(),
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0
