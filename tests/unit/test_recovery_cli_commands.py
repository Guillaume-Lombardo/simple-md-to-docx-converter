"""Deterministic CLI boundary coverage for recovery operations."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from markweave.cli.commands.recovery import (
    _absolute,
    _distributed_configuration,
    _environment_name,
    _secret_environment,
    load_and_verify_manifest,
)
from markweave.cli.main import main
from markweave.cli.types import ExitCode
from markweave.config import StorageProfile
from markweave.recovery_adapters import S3Configuration
from markweave.recovery_manifest import (
    RecoveryError,
    RecoveryIdentity,
    RecoveryManifest,
    RecoveryMember,
    build_manifest,
)
from markweave.recovery_service import RestoreResult

pytestmark = pytest.mark.unit


def test_manifest_verification_dependency_loads_only_for_restore(
    tmp_path: Path, mocker
) -> None:
    verify = mocker.patch(
        "markweave.recovery_manifest.load_and_verify_manifest",
        return_value=mocker.sentinel.manifest,
    )
    source = tmp_path / "recovery-set"

    assert load_and_verify_manifest(source) is mocker.sentinel.manifest
    verify.assert_called_once_with(source)


def test_backup_requires_explicit_coherent_profile_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            (
                "--non-interactive",
                "backup",
                "--profile",
                "standalone",
                "--destination",
                str(tmp_path.resolve()),
            )
        )
        is ExitCode.FAILURE
    )
    assert capsys.readouterr() == (
        "",
        "error: Standalone recovery configuration is mixed or incomplete\n",
    )


def test_distributed_backup_reads_secrets_only_from_named_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = uuid4().hex
    database_marker = f"postgresql+psycopg://user:{marker}@example/db"
    access_marker = f"access-{marker}"
    credential_marker = f"credential-{marker}"
    monkeypatch.setenv("RECOVERY_DATABASE", database_marker)
    monkeypatch.setenv("RECOVERY_ACCESS", access_marker)
    monkeypatch.setenv("RECOVERY_SECRET", credential_marker)
    manifest = build_manifest(
        StorageProfile.DISTRIBUTED,
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        identity=RecoveryIdentity("d" * 64, "o" * 64, "proof", "s" * 64),
        members=(RecoveryMember("database/metadata.json", 0, "a" * 64),),
    )
    backup = mocker.patch(
        "markweave.recovery_service.RecoveryService.backup",
        return_value=manifest,
    )
    assert (
        main(
            (
                "--json",
                "--non-interactive",
                "backup",
                "--profile",
                "distributed",
                "--destination",
                str(tmp_path.resolve()),
                "--database-url-environment",
                "RECOVERY_DATABASE",
                "--s3-bucket",
                "source-bucket",
                "--s3-access-key-environment",
                "RECOVERY_ACCESS",
                "--s3-secret-key-environment",
                "RECOVERY_SECRET",
                "--consistency-proof",
                "quiescence-42",
            )
        )
        is ExitCode.SUCCESS
    )
    request = backup.call_args.args[0]
    assert request.database_url == database_marker
    assert request.s3 == S3Configuration(
        "source-bucket", None, None, access_marker, credential_marker
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert all(
        secret not in output.out
        for secret in (database_marker, access_marker, credential_marker)
    )
    assert all(
        secret not in repr(request)
        for secret in (database_marker, access_marker, credential_marker)
    )


def test_non_interactive_restore_fails_before_mutation_without_yes(
    tmp_path: Path,
    mocker,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "set"
    source.mkdir()
    manifest = mocker.patch("markweave.cli.commands.recovery.load_and_verify_manifest")
    manifest.return_value.backup_id = "b" * 64
    restore = mocker.patch("markweave.recovery_service.RecoveryService.restore")

    assert (
        main(
            (
                "--non-interactive",
                "restore",
                "--profile",
                "standalone",
                "--source",
                str(source.resolve()),
                "--data-directory",
                str((tmp_path / "restored").resolve()),
                "--offline-proof",
                "change-window-42",
            )
        )
        is ExitCode.FAILURE
    )
    assert capsys.readouterr().err == "error: Non-interactive restore requires --yes\n"
    restore.assert_not_called()


def test_s3_configuration_repr_redacts_credentials() -> None:
    configuration = S3Configuration(
        "bucket", "https://objects.example", "region", "access-secret", "key-secret"
    )
    rendered = repr(configuration)
    assert "access-secret" not in rendered
    assert "key-secret" not in rendered
    assert rendered.count("<redacted>") == 2


def _manifest() -> RecoveryManifest:
    return RecoveryManifest(
        "markweave-recovery-set-v1",
        "b" * 64,
        "standalone",
        datetime.now(UTC).isoformat(),
        "d" * 64,
        "o" * 64,
        "proof",
        f"{'1' * 64}.{'2' * 64}",
        (RecoveryMember("database/metadata.sqlite3", 0, "a" * 64),),
    )


def test_confirmed_restore_success_and_quarterly_report(
    tmp_path: Path, mocker, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest()
    mocker.patch(
        "markweave.cli.commands.recovery.load_and_verify_manifest",
        return_value=manifest,
    )
    restored = RestoreResult(
        manifest.backup_id,
        "readiness-evidence",
        datetime.now(UTC),
    )
    restore = mocker.patch(
        "markweave.recovery_service.RecoveryService.restore",
        return_value=restored,
    )
    base = (
        "--json",
        "--non-interactive",
        "restore",
        "--profile",
        "standalone",
        "--source",
        str((tmp_path / "set").resolve()),
        "--data-directory",
        str((tmp_path / "target").resolve()),
        "--offline-proof",
        "window",
        "--yes",
    )
    assert main(base) is ExitCode.SUCCESS
    assert '"status":"verified"' in capsys.readouterr().out
    assert restore.call_count == 1

    report_directory = (tmp_path / "reports").resolve()
    assert (
        main(
            (
                *base,
                "--report-directory",
                str(report_directory),
                "--evidence-id",
                "quarterly-ready",
            )
        )
        is ExitCode.SUCCESS
    )
    assert '"targets_met":true' in capsys.readouterr().out
    assert len(tuple(report_directory.iterdir())) == 1


def test_restore_confirmation_and_report_pair_fail_closed(
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mocker.patch(
        "markweave.cli.commands.recovery.load_and_verify_manifest",
        return_value=_manifest(),
    )
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("wrong\n"))
    arguments = (
        "restore",
        "--profile",
        "standalone",
        "--source",
        str((tmp_path / "set").resolve()),
        "--data-directory",
        str((tmp_path / "target").resolve()),
        "--offline-proof",
        "window",
    )
    assert main(arguments) is ExitCode.FAILURE
    assert "did not match" in capsys.readouterr().err
    assert (
        main(
            (
                "--non-interactive",
                *arguments,
                "--yes",
                "--report-directory",
                str((tmp_path / "reports").resolve()),
            )
        )
        is ExitCode.FAILURE
    )
    assert "required together" in capsys.readouterr().err


def test_interactive_restore_accepts_exact_manifest_identity(
    tmp_path: Path,
    mocker,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = _manifest()
    mocker.patch(
        "markweave.cli.commands.recovery.load_and_verify_manifest",
        return_value=manifest,
    )
    mocker.patch(
        "markweave.recovery_service.RecoveryService.restore",
        return_value=RestoreResult(manifest.backup_id, "readiness", datetime.now(UTC)),
    )
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO(f"{manifest.backup_id}\n")
    )
    assert (
        main(
            (
                "restore",
                "--profile",
                "standalone",
                "--source",
                str((tmp_path / "set").resolve()),
                "--data-directory",
                str((tmp_path / "target").resolve()),
                "--offline-proof",
                "window",
            )
        )
        is ExitCode.SUCCESS
    )
    assert "restored and verified" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    (
        (
            "backup",
            "--profile",
            "distributed",
            "--destination",
            "/recovery/sets",
            "--s3-bucket",
            "bucket",
            "--consistency-proof",
            "proof",
        ),
        (
            "backup",
            "--profile",
            "standalone",
            "--destination",
            "/recovery/sets",
            "--data-directory",
            "/data",
            "--s3-bucket",
            "mixed",
        ),
    ),
)
def test_cli_rejects_incomplete_and_mixed_provider_options(
    arguments: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) is ExitCode.FAILURE
    assert capsys.readouterr().err.startswith("error:")


def _distributed_values(**overrides):
    values = {
        "database_url_environment": "RECOVERY_DATABASE",
        "s3_bucket": "bucket",
        "s3_endpoint_url": None,
        "s3_region": None,
        "s3_access_key_environment": None,
        "s3_secret_key_environment": None,
    }
    values.update(overrides)
    return values


def test_cli_secret_and_path_guards_cover_invalid_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RecoveryError, match="unavailable"):
        _secret_environment("MISSING_RECOVERY_VALUE")
    monkeypatch.setenv("RECOVERY_DATABASE", "sqlite:///unsafe")
    with pytest.raises(RecoveryError, match="PostgreSQL"):
        _distributed_configuration(StorageProfile.DISTRIBUTED, _distributed_values())
    monkeypatch.setenv("RECOVERY_DATABASE", "postgresql://safe")
    with pytest.raises(RecoveryError, match="supplied together"):
        _distributed_configuration(
            StorageProfile.DISTRIBUTED,
            _distributed_values(s3_access_key_environment="RECOVERY_ACCESS"),
        )
    monkeypatch.setenv("CONTROLLED_RECOVERY_VALUE", "unsafe\nvalue")
    with pytest.raises(RecoveryError, match="unavailable"):
        _secret_environment("CONTROLLED_RECOVERY_VALUE")
    with pytest.raises(RecoveryError, match="absolute"):
        _absolute(Path("relative"), "Recovery source")
    with pytest.raises(argparse.ArgumentTypeError, match="environment variable name"):
        _environment_name("not-valid!")


def test_backup_rejects_partially_supplied_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("backup", "--profile", "standalone")) is ExitCode.FAILURE
    assert "profile and destination" in capsys.readouterr().err
