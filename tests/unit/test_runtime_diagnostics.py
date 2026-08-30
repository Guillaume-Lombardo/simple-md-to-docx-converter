"""Failure-focused coverage for bounded runtime diagnostics."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from markweave.config import MalwareScanningMode, StorageProfile
from markweave.runtime_diagnostics import (
    MAX_MANIFEST_BYTES,
    _Budget,
    _check_engines,
    _check_fonts,
    _check_permissions,
    _check_runtime,
    _check_scanner,
    _check_storage,
    _executable_works,
    run_runtime_diagnostics,
)

pytestmark = pytest.mark.unit


def test_budget_and_subprocess_checks_fail_closed(mocker) -> None:
    clock = mocker.patch("markweave.runtime_diagnostics.monotonic")
    clock.side_effect = (10.0, 11.0, 12.0)
    budget = _Budget(1.5)
    assert budget.remaining() == 0.5
    with pytest.raises(TimeoutError):
        budget.remaining()

    clock.side_effect = None
    clock.return_value = 20.0
    budget = _Budget(5)
    mocker.patch("markweave.runtime_diagnostics.shutil.which", return_value=None)
    assert not _executable_works("missing", budget)

    mocker.patch(
        "markweave.runtime_diagnostics.shutil.which", return_value="/safe/tool"
    )
    run = mocker.patch("markweave.runtime_diagnostics.subprocess.run")
    run.return_value.returncode = 2
    assert not _executable_works("tool", budget)
    run.side_effect = subprocess.TimeoutExpired(("tool",), 1)
    assert not _executable_works("tool", budget)


def test_engine_and_font_checks_are_bounded_and_validate_manifest_shape(
    mocker, tmp_path: Path
) -> None:
    settings = mocker.Mock()
    settings.template_pandoc_executable = "pandoc"
    settings.template_libreoffice_executable = "libreoffice"
    settings.conversion_mermaid_executable = "mmdc"
    settings.conversion_chromium_executable = "chromium"
    executable = mocker.patch(
        "markweave.runtime_diagnostics._executable_works",
        side_effect=(True, True, False),
    )
    assert not _check_engines(settings, _Budget(5))
    assert executable.call_count == 3

    manifest = tmp_path / "manifest.json"
    settings.conversion_font_manifest_path = manifest
    assert not _check_fonts(settings, _Budget(5))
    manifest.write_bytes(b"x" * (MAX_MANIFEST_BYTES + 1))
    assert not _check_fonts(settings, _Budget(5))
    manifest.unlink()
    manifest.mkdir()
    assert not _check_fonts(settings, _Budget(5))
    manifest.rmdir()
    manifest.write_text("[]", encoding="utf-8")
    assert not _check_fonts(settings, _Budget(5))
    manifest.write_text('{"artifacts":[]}', encoding="utf-8")
    executable.side_effect = None
    executable.return_value = True
    assert _check_fonts(settings, _Budget(5))


def test_scanner_check_accepts_only_bounded_pong(mocker) -> None:
    settings = mocker.Mock()
    settings.insecure_evaluation_mode = True
    assert _check_scanner(settings, _Budget(5))

    settings.insecure_evaluation_mode = False
    settings.malware_scanning_mode = MalwareScanningMode.TRUSTED_UPSTREAM
    assert _check_scanner(settings, _Budget(5))

    settings.malware_scanning_mode = MalwareScanningMode.CLAMAV
    settings.clamav_timeout_seconds = 2
    settings.clamav_host = "scanner"
    settings.clamav_port = 3310
    connection = mocker.MagicMock()
    connection.__enter__.return_value.recv.return_value = b"PONG\0"
    create = mocker.patch(
        "markweave.runtime_diagnostics.socket.create_connection",
        return_value=connection,
    )
    assert _check_scanner(settings, _Budget(5))
    create.assert_called_once()
    connection.__enter__.return_value.sendall.assert_called_once_with(b"zPING\0")

    connection.__enter__.return_value.recv.return_value = b"private failure"
    assert not _check_scanner(settings, _Budget(5))
    create.side_effect = OSError("private endpoint")
    assert not _check_scanner(settings, _Budget(5))


def test_distributed_storage_check_uses_bounded_real_adapters(mocker) -> None:
    settings = mocker.Mock()
    settings.storage_profile = StorageProfile.DISTRIBUTED
    settings.distributed_database_url.get_secret_value.return_value = (
        "postgresql://private"
    )
    settings.s3_bucket = "objects"
    settings.s3_endpoint_url = "https://objects.invalid"
    settings.s3_region = "test"
    settings.s3_access_key_id.get_secret_value.return_value = "access"
    settings.s3_secret_access_key.get_secret_value.return_value = "secret"
    engine = mocker.MagicMock()
    create = mocker.patch(
        "markweave.runtime_diagnostics.create_database_engine", return_value=engine
    )
    s3 = mocker.Mock()
    client = mocker.patch("markweave.runtime_diagnostics.boto3.client", return_value=s3)

    assert _check_storage(settings, _Budget(5))
    create.assert_called_once()
    engine.dispose.assert_called_once_with()
    s3.head_bucket.assert_called_once_with(Bucket="objects")
    assert "aws_secret_access_key" in client.call_args.kwargs
    settings.s3_secret_access_key.get_secret_value.assert_called_once_with()

    settings.distributed_database_url = None
    assert not _check_storage(settings, _Budget(5))

    settings.distributed_database_url = mocker.Mock()
    settings.distributed_database_url.get_secret_value.return_value = "postgresql://db"
    settings.s3_bucket = "objects"
    settings.s3_endpoint_url = None
    settings.s3_region = None
    settings.s3_access_key_id = None
    engine.reset_mock()
    assert _check_storage(settings, _Budget(5))
    assert "endpoint_url" not in client.call_args.kwargs
    assert "aws_access_key_id" not in client.call_args.kwargs


def test_permissions_runtime_and_report_collection_fail_closed(
    mocker, tmp_path: Path
) -> None:
    settings = mocker.Mock()
    settings.storage_profile = StorageProfile.STANDALONE
    settings.standalone_data_directory = tmp_path / "missing"
    settings.template_engine_workspace_root = None
    assert not _check_permissions(settings)
    assert not _check_storage(settings, _Budget(5))

    settings.standalone_data_directory = None
    assert not _check_permissions(settings)
    assert not _check_storage(settings, _Budget(5))

    data = tmp_path / "data"
    (data / "objects").mkdir(parents=True)
    settings.standalone_data_directory = data
    settings.template_engine_workspace_root = data
    assert _check_permissions(settings)

    mocker.patch("markweave.runtime_diagnostics.os.geteuid", return_value=0)
    assert not _check_runtime()

    settings.storage_profile = StorageProfile.DISTRIBUTED
    checks = mocker.patch(
        "markweave.runtime_diagnostics._check_engines",
        side_effect=RuntimeError("secret"),
    )
    mocker.patch("markweave.runtime_diagnostics._check_fonts", return_value=True)
    mocker.patch("markweave.runtime_diagnostics._check_scanner", return_value=True)
    mocker.patch("markweave.runtime_diagnostics._check_storage", return_value=True)
    mocker.patch("markweave.runtime_diagnostics._check_permissions", return_value=True)
    mocker.patch("markweave.runtime_diagnostics._check_runtime", return_value=True)
    report = run_runtime_diagnostics(settings, timeout_seconds=1)
    assert not report.checks[1].passed
    assert all(check.passed for check in report.checks[2:])
    checks.assert_called_once()
