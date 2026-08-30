"""Profile-neutral production backup and restore orchestration."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp

from markweave.config import StorageProfile
from markweave.recovery_adapters import (
    PostgreSQLRecoveryAdapter,
    RecoveryDeadline,
    S3Configuration,
    S3RecoveryAdapter,
    StandaloneRecoveryAdapter,
    filesystem_lock,
)
from markweave.recovery_manifest import (
    RecoveryError,
    RecoveryIdentity,
    RecoveryManifest,
    build_manifest,
    canonical_json,
    load_and_verify_manifest,
    write_manifest,
)

MAX_PROOF_CHARACTERS = 1024


@dataclass(frozen=True, slots=True)
class BackupRequest:
    """Explicit, coherent backup request."""

    profile: StorageProfile
    destination: Path
    timeout_seconds: float
    data_directory: Path | None = None
    database_url: str | None = None
    s3: S3Configuration | None = None
    consistency_proof: str | None = None

    def __repr__(self) -> str:
        return (
            f"BackupRequest(profile={self.profile!r}, destination={self.destination!r}, "
            "timeout_seconds={!r}, data_directory={!r}, database_url=<redacted>, "
            "s3={!r}, consistency_proof={!r})"
        ).format(
            self.timeout_seconds,
            self.data_directory,
            self.s3,
            self.consistency_proof,
        )


@dataclass(frozen=True, slots=True)
class RestoreRequest:
    """Explicit, isolated restore request."""

    profile: StorageProfile
    source: Path
    timeout_seconds: float
    offline_proof: str
    data_directory: Path | None = None
    database_url: str | None = None
    s3: S3Configuration | None = None

    def __repr__(self) -> str:
        return (
            f"RestoreRequest(profile={self.profile!r}, source={self.source!r}, "
            "timeout_seconds={!r}, offline_proof={!r}, data_directory={!r}, "
            "database_url=<redacted>, s3={!r})"
        ).format(
            self.timeout_seconds,
            self.offline_proof,
            self.data_directory,
            self.s3,
        )


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Content-free restore and readiness evidence."""

    backup_id: str
    evidence_id: str
    backup_created_at: datetime


class RecoveryService:
    """Create authenticated sets and restore only into isolated empty targets."""

    def __init__(self) -> None:
        self._standalone = StandaloneRecoveryAdapter()
        self._postgresql = PostgreSQLRecoveryAdapter()

    def backup(self, request: BackupRequest) -> RecoveryManifest:
        """Create one complete set and publish it by its manifest identity."""

        self._validate_backup_request(request)
        destination = self._backup_root(request.destination)
        with filesystem_lock(destination / ".markweave-recovery.lock"):
            return self._backup_locked(request, destination)

    def _backup_locked(
        self, request: BackupRequest, destination: Path
    ) -> RecoveryManifest:
        """Build one set while the destination publication lock is held."""

        deadline = RecoveryDeadline.after(request.timeout_seconds)
        staging = Path(mkdtemp(prefix=".pending-", dir=destination))
        os.chmod(staging, 0o700)
        try:
            if request.profile is StorageProfile.STANDALONE:
                data_directory = _required(request.data_directory)
                database, objects = self._standalone.backup(
                    data_directory, staging, deadline
                )
                consistency = "sqlite-online-snapshot+stable-object-tree"
            else:
                database_url = _required(request.database_url)
                s3_configuration = _required(request.s3)
                consistency_proof = _required(request.consistency_proof)
                database = self._postgresql.backup(database_url, staging, deadline)
                objects = S3RecoveryAdapter(s3_configuration, deadline).backup(staging)
                consistency = consistency_proof
            manifest = build_manifest(
                request.profile,
                created_at=datetime.now(UTC),
                identity=RecoveryIdentity(
                    database.identity,
                    objects.identity,
                    consistency,
                    f"{database.source_identity}.{objects.source_identity}",
                ),
                members=database.members + objects.members,
            )
            write_manifest(staging, manifest)
            target = destination / manifest.backup_id
            if target.exists() or target.is_symlink():
                existing = load_and_verify_manifest(target)
                if existing != manifest:
                    raise RecoveryError(
                        "Backup identity already exists with other content"
                    )
                shutil.rmtree(staging)
                return existing
            os.replace(staging, target)
            self._sync_directory(destination)
            return manifest
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def restore(self, request: RestoreRequest) -> RestoreResult:
        """Verify a set before any mutation, then restore one isolated profile."""

        self._validate_restore_request(request)
        manifest = load_and_verify_manifest(request.source)
        if manifest.profile != request.profile.value:
            raise RecoveryError("Recovery profile does not match the backup set")
        try:
            database_source, object_source = manifest.source_identity.split(".", 1)
        except ValueError:
            raise RecoveryError("Recovery source identity is invalid") from None
        deadline = RecoveryDeadline.after(request.timeout_seconds)
        if request.profile is StorageProfile.STANDALONE:
            data_directory = _required(request.data_directory)
            evidence = self._standalone.restore(
                request.source,
                data_directory,
                manifest.members,
                deadline,
            )
        else:
            database_url = _required(request.database_url)
            s3_configuration = _required(request.s3)
            s3 = S3RecoveryAdapter(s3_configuration, deadline)
            object_evidence, object_keys = s3.ensure_empty_and_restore(
                request.source,
                manifest.members,
                source_identity=object_source,
            )
            try:
                database_evidence = self._postgresql.restore(
                    database_url,
                    request.source / "database/metadata.json",
                    source_identity=database_source,
                    object_keys=object_keys,
                    deadline=deadline,
                )
            except BaseException:
                s3.remove(object_keys)
                raise
            evidence = f"{database_evidence}.{object_evidence}"
        evidence = hashlib.sha256(
            canonical_json([evidence, request.offline_proof])
        ).hexdigest()
        return RestoreResult(
            backup_id=manifest.backup_id,
            evidence_id=evidence,
            backup_created_at=datetime.fromisoformat(manifest.created_at),
        )

    @staticmethod
    def _validate_backup_request(request: BackupRequest) -> None:
        if request.profile is StorageProfile.STANDALONE:
            if (
                request.data_directory is None
                or request.database_url is not None
                or request.s3 is not None
                or request.consistency_proof is not None
            ):
                raise RecoveryError(
                    "Standalone recovery configuration is mixed or incomplete"
                )
        elif (
            request.data_directory is not None
            or request.database_url is None
            or request.s3 is None
            or request.consistency_proof is None
            or not request.consistency_proof.strip()
        ):
            raise RecoveryError(
                "Distributed recovery configuration is mixed or incomplete"
            )

    @staticmethod
    def _validate_restore_request(request: RestoreRequest) -> None:
        if (
            not request.offline_proof.strip()
            or len(request.offline_proof) > MAX_PROOF_CHARACTERS
        ):
            raise RecoveryError("An offline or isolated restore proof is required")
        if request.profile is StorageProfile.STANDALONE:
            if (
                request.data_directory is None
                or request.database_url is not None
                or request.s3 is not None
            ):
                raise RecoveryError(
                    "Standalone recovery configuration is mixed or incomplete"
                )
        elif (
            request.data_directory is not None
            or request.database_url is None
            or request.s3 is None
        ):
            raise RecoveryError(
                "Distributed recovery configuration is mixed or incomplete"
            )

    @staticmethod
    def _backup_root(path: Path) -> Path:
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise RecoveryError("Backup destination is unsafe")
        try:
            if not path.exists():
                if path.parent.is_symlink() or not path.parent.is_dir():
                    raise RecoveryError("Backup destination parent is unsafe")
                path.mkdir(mode=0o700)
            if not path.is_dir():
                raise RecoveryError("Backup destination is unsafe")
            return path
        except OSError:
            raise RecoveryError("Backup destination is unavailable") from None

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _required[T](value: T | None) -> T:
    if value is None:
        raise RecoveryError("Recovery configuration is incomplete")
    return value
