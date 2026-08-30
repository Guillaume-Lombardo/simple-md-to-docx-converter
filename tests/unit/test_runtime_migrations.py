"""Unit coverage for profile-safe operational migration assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from markweave.config import StorageProfile
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import MigrationResult, _current_revision
from markweave.runtime_migrations import (
    _prepare_standalone_directory,
    migrate_configured_profile,
)

pytestmark = pytest.mark.unit


def test_standalone_and_distributed_profiles_build_only_the_selected_engine(
    mocker, tmp_path: Path
) -> None:
    engine = mocker.Mock()
    create = mocker.patch(
        "markweave.runtime_migrations.create_database_engine", return_value=engine
    )
    upgrade = mocker.patch(
        "markweave.runtime_migrations.upgrade_database_observed",
        return_value=MigrationResult(None, "head"),
    )
    standalone = mocker.Mock()
    standalone.storage_profile = StorageProfile.STANDALONE
    standalone.standalone_data_directory = tmp_path / "data"
    result = migrate_configured_profile(standalone, timeout_seconds=2)
    assert result.changed and result.profile is StorageProfile.STANDALONE
    assert standalone.standalone_data_directory.is_dir()
    create.assert_called_once_with(mocker.ANY, timeout_seconds=2)
    upgrade.assert_called_once_with(engine)
    engine.dispose.assert_called_once_with()

    create.reset_mock()
    engine.dispose.reset_mock()
    upgrade.return_value = MigrationResult("head", "head")
    distributed = mocker.Mock()
    distributed.storage_profile = StorageProfile.DISTRIBUTED
    distributed.distributed_database_url.get_secret_value.return_value = (
        "postgresql://db"
    )
    result = migrate_configured_profile(distributed)
    assert not result.changed and result.profile is StorageProfile.DISTRIBUTED
    create.assert_called_once_with("postgresql://db", timeout_seconds=None)
    engine.dispose.assert_called_once_with()


def test_migration_disposes_failures_and_rejects_invalid_results(
    mocker, tmp_path: Path
) -> None:
    settings = mocker.Mock()
    settings.storage_profile = StorageProfile.STANDALONE
    settings.standalone_data_directory = tmp_path / "data"
    engine = mocker.Mock()
    mocker.patch(
        "markweave.runtime_migrations.create_database_engine", return_value=engine
    )
    upgrade = mocker.patch(
        "markweave.runtime_migrations.upgrade_database_observed",
        side_effect=PersistenceError,
    )
    with pytest.raises(PersistenceError):
        migrate_configured_profile(settings)
    engine.dispose.assert_called_once_with()

    upgrade.side_effect = None
    upgrade.return_value = MigrationResult(None, None)
    with pytest.raises(PersistenceError):
        migrate_configured_profile(settings)

    target = tmp_path / "file"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(PersistenceError):
        _prepare_standalone_directory(target)


def test_migration_rejects_defensively_incomplete_validated_profiles(mocker) -> None:
    settings = mocker.Mock()
    settings.storage_profile = StorageProfile.STANDALONE
    settings.standalone_data_directory = None
    with pytest.raises(RuntimeError, match="standalone"):
        migrate_configured_profile(settings)

    settings.storage_profile = StorageProfile.DISTRIBUTED
    settings.distributed_database_url = None
    with pytest.raises(RuntimeError, match="distributed"):
        migrate_configured_profile(settings)

    directory = mocker.Mock(spec=Path)
    directory.is_dir.return_value = False
    with pytest.raises(PersistenceError):
        _prepare_standalone_directory(directory)


def test_observed_revision_reader_is_profile_bounded_and_requires_one_head(
    mocker,
) -> None:
    connection = mocker.Mock()
    connection.dialect.name = "postgresql"
    exists = mocker.Mock()
    exists.scalar_one_or_none.return_value = "alembic_version"
    revision = mocker.Mock()
    revision.scalars.return_value = ("head",)
    connection.exec_driver_sql.side_effect = (exists, revision)
    assert _current_revision(connection) == "head"

    missing = mocker.Mock()
    missing.dialect.name = "postgresql"
    missing.exec_driver_sql.return_value.scalar_one_or_none.return_value = None
    assert _current_revision(missing) is None

    invalid = mocker.Mock()
    invalid.dialect.name = "postgresql"
    invalid_revision = mocker.Mock()
    invalid_revision.scalars.return_value = ("one", "two")
    invalid.exec_driver_sql.side_effect = (exists, invalid_revision)
    with pytest.raises(PersistenceError):
        _current_revision(invalid)

    unsupported = mocker.Mock()
    unsupported.dialect.name = "mysql"
    with pytest.raises(PersistenceError):
        _current_revision(unsupported)
