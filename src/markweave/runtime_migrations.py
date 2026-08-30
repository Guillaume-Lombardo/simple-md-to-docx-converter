"""Profile-safe operational database migration assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from markweave.config import Settings, StorageProfile
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import (
    MigrationResult,
    upgrade_database_observed,
)
from markweave.persistence.sql import create_database_engine, standalone_database_url


@dataclass(frozen=True, slots=True)
class ProfileMigrationResult:
    """Redacted migration result safe for operator output."""

    profile: StorageProfile
    previous_revision: str | None
    current_revision: str
    changed: bool


def migrate_configured_profile(
    settings: Settings | None = None, *, timeout_seconds: float | None = None
) -> ProfileMigrationResult:
    """Migrate exactly the configured profile and dispose its database engine."""
    resolved = settings or Settings.load()
    if resolved.storage_profile is StorageProfile.STANDALONE:
        data_directory = resolved.standalone_data_directory
        if data_directory is None:
            raise RuntimeError("Validated standalone settings are incomplete")
        _prepare_standalone_directory(data_directory)
        database_url: object = standalone_database_url(data_directory)
    else:
        secret = resolved.distributed_database_url
        if secret is None:
            raise RuntimeError("Validated distributed settings are incomplete")
        database_url = secret.get_secret_value()

    engine = create_database_engine(database_url, timeout_seconds=timeout_seconds)
    try:
        migration = upgrade_database_observed(engine)
    finally:
        engine.dispose()
    return _profile_result(resolved.storage_profile, migration)


def _prepare_standalone_directory(directory: Path) -> None:
    """Create only the configured data root and reject non-directory targets."""
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir():
            raise OSError
    except OSError:
        raise PersistenceError from None


def _profile_result(
    profile: StorageProfile, migration: MigrationResult
) -> ProfileMigrationResult:
    if migration.current_revision is None:
        raise PersistenceError from None
    return ProfileMigrationResult(
        profile=profile,
        previous_revision=migration.previous_revision,
        current_revision=migration.current_revision,
        changed=migration.previous_revision != migration.current_revision,
    )
