"""Profile-neutral recovery orchestration and rollback unit coverage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from markweave.config import StorageProfile
from markweave.recovery_adapters import AdapterBackup, S3Configuration
from markweave.recovery_manifest import RecoveryError, RecoveryMember
from markweave.recovery_service import (
    BackupRequest,
    RecoveryService,
    RestoreRequest,
    _required,
)

pytestmark = pytest.mark.unit


def _distributed_backup(service: RecoveryService, tmp_path: Path, mocker):
    def database_backup(_url, staging, _deadline):
        target = staging / "database" / "metadata.json"
        target.parent.mkdir(parents=True)
        target.write_text("{}", encoding="utf-8")
        return AdapterBackup(
            "d" * 64,
            "1" * 64,
            (
                RecoveryMember(
                    "database/metadata.json",
                    2,
                    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
                ),
            ),
        )

    def object_backup(staging):
        target = (
            staging
            / "objects"
            / "uploads"
            / "00000000-0000-0000-0000-000000000000"
            / "00000000-0000-0000-0000-000000000001"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x")
        return AdapterBackup(
            "o" * 64,
            "2" * 64,
            (
                RecoveryMember(
                    "objects/uploads/00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000001",
                    1,
                    "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
                ),
            ),
        )

    mocker.patch.object(service._postgresql, "backup", side_effect=database_backup)
    s3_adapter = mocker.patch("markweave.recovery_service.S3RecoveryAdapter")
    s3_adapter.return_value.backup.side_effect = object_backup
    manifest = service.backup(
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            (tmp_path / "sets").resolve(),
            30,
            database_url="postgresql://redacted",
            s3=S3Configuration("source", None, None),
            consistency_proof="quiescent",
        )
    )
    return manifest, s3_adapter


def test_distributed_service_binds_identities_and_restores_with_cleanup(
    tmp_path: Path, mocker
) -> None:
    service = RecoveryService()
    manifest, s3_type = _distributed_backup(service, tmp_path, mocker)
    source = (tmp_path / "sets" / manifest.backup_id).resolve()
    s3 = s3_type.return_value
    s3.ensure_empty_and_restore.return_value = (
        "object-evidence",
        frozenset({"uploads/a/b"}),
    )
    pg_restore = mocker.patch.object(
        service._postgresql, "restore", return_value="db-evidence"
    )
    result = service.restore(
        RestoreRequest(
            StorageProfile.DISTRIBUTED,
            source,
            30,
            "isolated-window",
            database_url="postgresql://target",
            s3=S3Configuration("target", None, None),
        )
    )
    assert result.backup_id == manifest.backup_id
    assert result.evidence_id

    pg_restore.side_effect = RecoveryError("database failed")
    s3.ensure_empty_and_restore.return_value = (
        "object-evidence",
        frozenset({"uploads/a/b"}),
    )
    with pytest.raises(RecoveryError, match="database failed"):
        service.restore(
            RestoreRequest(
                StorageProfile.DISTRIBUTED,
                source,
                30,
                "isolated-window-2",
                database_url="postgresql://target-2",
                s3=S3Configuration("target-2", None, None),
            )
        )
    s3.remove.assert_called_with(frozenset({"uploads/a/b"}))


@pytest.mark.parametrize(
    "recovery_request",
    (
        BackupRequest(StorageProfile.STANDALONE, Path("/recovery-test/sets"), 1),
        BackupRequest(
            StorageProfile.STANDALONE,
            Path("/recovery-test/sets"),
            1,
            data_directory=Path("/recovery-test/data"),
            database_url="postgresql://mixed",
        ),
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            Path("/recovery-test/sets"),
            1,
            database_url="postgresql://db",
        ),
    ),
)
def test_backup_request_validation_rejects_mixed_or_incomplete(
    recovery_request,
) -> None:
    with pytest.raises(RecoveryError, match="mixed or incomplete"):
        RecoveryService().backup(recovery_request)


@pytest.mark.parametrize(
    "recovery_request",
    (
        RestoreRequest(
            StorageProfile.STANDALONE,
            Path("/recovery-test/source"),
            1,
            "",
            data_directory=Path("/recovery-test/target"),
        ),
        RestoreRequest(
            StorageProfile.STANDALONE,
            Path("/recovery-test/source"),
            1,
            "proof",
            data_directory=Path("/recovery-test/target"),
            database_url="postgresql://mixed",
        ),
        RestoreRequest(
            StorageProfile.DISTRIBUTED,
            Path("/recovery-test/source"),
            1,
            "proof",
            database_url="postgresql://target",
        ),
    ),
)
def test_restore_request_validation_rejects_unsafe_contracts(recovery_request) -> None:
    with pytest.raises(RecoveryError):
        RecoveryService().restore(recovery_request)


def test_backup_reuses_identical_published_identity(tmp_path: Path, mocker) -> None:
    service = RecoveryService()
    manifest, _s3_type = _distributed_backup(service, tmp_path, mocker)
    mocker.patch("markweave.recovery_service.build_manifest", return_value=manifest)
    repeated = service.backup(
        BackupRequest(
            StorageProfile.DISTRIBUTED,
            (tmp_path / "sets").resolve(),
            30,
            database_url="postgresql://redacted",
            s3=S3Configuration("source", None, None),
            consistency_proof="quiescent",
        )
    )
    assert repeated == manifest
    assert not list((tmp_path / "sets").glob(".pending-*"))


def test_backup_cleans_staging_on_interrupt(tmp_path: Path, mocker) -> None:
    service = RecoveryService()
    mocker.patch.object(service._postgresql, "backup", side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        service.backup(
            BackupRequest(
                StorageProfile.DISTRIBUTED,
                (tmp_path / "sets").resolve(),
                30,
                database_url="postgresql://redacted",
                s3=S3Configuration("source", None, None),
                consistency_proof="quiescent",
            )
        )
    assert not list((tmp_path / "sets").glob(".pending-*"))


def test_restore_rejects_profile_and_source_identity_before_mutation(
    tmp_path: Path, mocker
) -> None:
    service = RecoveryService()
    manifest, _s3_type = _distributed_backup(service, tmp_path, mocker)
    request = RestoreRequest(
        StorageProfile.DISTRIBUTED,
        (tmp_path / "sets" / manifest.backup_id).resolve(),
        30,
        "isolated",
        database_url="postgresql://target",
        s3=S3Configuration("target", None, None),
    )
    loader = mocker.patch("markweave.recovery_service.load_and_verify_manifest")
    loader.return_value = replace(manifest, profile=StorageProfile.STANDALONE.value)
    with pytest.raises(RecoveryError, match="profile"):
        service.restore(request)
    loader.return_value = replace(manifest, source_identity="invalid")
    with pytest.raises(RecoveryError, match="source identity"):
        service.restore(request)


def test_backup_root_and_required_value_fail_closed(tmp_path: Path) -> None:
    service = RecoveryService()
    with pytest.raises(RecoveryError, match="unsafe"):
        service._backup_root(Path("relative"))
    file_path = (tmp_path / "file").resolve()
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RecoveryError, match="unsafe"):
        service._backup_root(file_path)
    missing_parent = (tmp_path / "missing" / "sets").resolve()
    with pytest.raises(RecoveryError, match="parent"):
        service._backup_root(missing_parent)
    with pytest.raises(RecoveryError, match="incomplete"):
        _required(None)
    assert _required("value") == "value"


def test_restore_proof_has_a_bounded_size() -> None:
    request = RestoreRequest(
        StorageProfile.STANDALONE,
        Path("/unused"),
        1,
        "x" * 1025,
        data_directory=Path("/target"),
    )
    with pytest.raises(RecoveryError, match="proof"):
        RecoveryService().restore(request)
