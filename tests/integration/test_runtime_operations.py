"""Real filesystem, process, and SQLite coverage for runtime operations."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from markweave.config import Settings
from markweave.runtime_diagnostics import run_runtime_diagnostics
from markweave.runtime_migrations import migrate_configured_profile
from tests.settings import template_settings

pytestmark = pytest.mark.integration


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "initial_admin_username": "admin",
        "initial_admin_password": "test-password",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3600,
        "storage_profile": "standalone",
        "standalone_data_directory": tmp_path / "data",
        "malware_scanning_mode": "trusted-upstream",
        **template_settings(),
    }
    values.update(overrides)
    return Settings(**values)


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return str(path)


def test_doctor_crosses_real_read_only_filesystem_process_and_sqlite_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    engine = _executable(tools / "engine")
    _executable(tools / "fc-list")
    monkeypatch.setenv("PATH", f"{tools}")
    manifest = tmp_path / "fonts.json"
    manifest.write_text(json.dumps({"artifacts": []}), encoding="utf-8")
    settings = _settings(
        tmp_path,
        conversion_font_manifest_path=manifest,
        conversion_mermaid_executable=engine,
        conversion_chromium_executable=engine,
        template_pandoc_executable=engine,
        template_libreoffice_executable=engine,
    )
    migration = migrate_configured_profile(settings)
    data_directory = settings.standalone_data_directory
    assert data_directory is not None
    objects = data_directory / "objects"
    objects.mkdir(mode=0o700)

    report = run_runtime_diagnostics(settings, timeout_seconds=5)

    assert migration.changed
    assert all(check.passed for check in report.checks)
    assert {check.name for check in report.checks} == {
        "configuration",
        "engines",
        "fonts",
        "scanner",
        "storage",
        "permissions",
        "runtime",
    }


def test_standalone_migration_is_concurrency_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(
            executor.map(lambda _: migrate_configured_profile(settings), range(4))
        )

    repeated = migrate_configured_profile(settings)

    assert all(
        result.current_revision == results[0].current_revision for result in results
    )
    assert sum(result.changed for result in results) == 1
    assert not repeated.changed
