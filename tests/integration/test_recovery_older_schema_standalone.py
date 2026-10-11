"""Standalone restore of recovery sets created by an earlier supported schema."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

from markweave.config import StorageProfile
from markweave.persistence.migrations import upgrade_database, upgrade_database_to
from markweave.persistence.schema import ComposerRevisionRow, ConversionJobRow
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.recovery_manifest import RecoveryError
from markweave.recovery_schema import OLDEST_RECOVERY_SET_REVISION, head_revision
from markweave.recovery_service import BackupRequest, RecoveryService, RestoreRequest
from tests.recovery_older_schema import (
    PRE_FILL_PLAN_REVISION,
    OlderSchemaRecords,
    populate_older_schema,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _older_data(root: Path, revision: str = PRE_FILL_PLAN_REVISION):
    data = root / "data"
    (data / "objects").mkdir(parents=True)
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database_to(engine, revision)
        records = populate_older_schema(engine)
    finally:
        engine.dispose()
    for key, content in records.objects.items():
        path = data / "objects" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return data, records


def _alter_source(data: Path, *statements: str) -> None:
    with closing(sqlite3.connect(data / "metadata.sqlite3")) as connection:
        for statement in statements:
            connection.execute(statement)
        connection.commit()


def _restore(data: Path, root: Path) -> Path:
    service = RecoveryService()
    manifest = service.backup(
        BackupRequest(
            StorageProfile.STANDALONE,
            (root / "sets").resolve(),
            60,
            data_directory=data.resolve(),
        )
    )
    target = (root / "restored").resolve()
    result = service.restore(
        RestoreRequest(
            StorageProfile.STANDALONE,
            (root / "sets" / manifest.backup_id).resolve(),
            60,
            "offline-older-schema-proof",
            data_directory=target,
        )
    )
    assert result.backup_id == manifest.backup_id
    return target


def _assert_restored_at_head(target: Path, records: OlderSchemaRecords) -> None:
    engine = create_database_engine(standalone_database_url(target))
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == head_revision()
            )
            assert "composer_fill_plans" in inspect(connection).get_table_names()
            job = connection.execute(
                select(ConversionJobRow.state, ConversionJobRow.owner_id).where(
                    ConversionJobRow.id == records.job_id
                )
            ).one()
            assert job == ("succeeded", records.owner_id)
            assert (
                connection.scalar(
                    select(ComposerRevisionRow.publication_state).where(
                        ComposerRevisionRow.id == records.revision_id
                    )
                )
                == "published"
            )
    finally:
        engine.dispose()
    for key, content in records.objects.items():
        assert (target / "objects" / key).read_bytes() == content


@pytest.mark.parametrize("revision", [PRE_FILL_PLAN_REVISION, "20260923_22"])
def test_standalone_restores_earlier_schema_set_to_head_with_references(
    tmp_path: Path, revision: str
) -> None:
    data, records = _older_data(tmp_path, revision)
    _assert_restored_at_head(_restore(data, tmp_path), records)


def test_standalone_restores_oldest_supported_recovery_set(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "objects").mkdir(parents=True)
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database_to(engine, OLDEST_RECOVERY_SET_REVISION)
    finally:
        engine.dispose()
    target = _restore(data, tmp_path)
    engine = create_database_engine(standalone_database_url(target))
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == head_revision()
            )
    finally:
        engine.dispose()


def test_standalone_refuses_earlier_set_missing_a_retained_object(
    tmp_path: Path,
) -> None:
    data, records = _older_data(tmp_path)
    (data / "objects" / records.key("composer-artifacts")).unlink()
    with pytest.raises(RecoveryError, match="stable object references"):
        _restore(data, tmp_path)
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    "statements",
    [
        ("DROP TABLE composer_questions",),
        ("ALTER TABLE composer_sources DROP COLUMN kind",),
        ("DROP TABLE alembic_version",),
    ],
)
def test_standalone_refuses_incomplete_earlier_schema(
    tmp_path: Path, statements: tuple[str, ...]
) -> None:
    data, _records = _older_data(tmp_path)
    _alter_source(data, *statements)
    with pytest.raises(RecoveryError, match="schema is incomplete"):
        _restore(data, tmp_path)
    assert not (tmp_path / "restored").exists()


def test_standalone_still_refuses_incomplete_current_schema(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "objects").mkdir(parents=True)
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()
    _alter_source(data, "DROP TABLE composer_fill_plans")
    with pytest.raises(RecoveryError, match="schema is incomplete"):
        _restore(data, tmp_path)
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize(
    ("revision", "message"),
    [
        ("20991231_99", "unknown to this release"),
        ("20260828_13", "predates supported recovery sets"),
    ],
)
def test_standalone_refuses_unsupported_schema_revisions(
    tmp_path: Path, revision: str, message: str
) -> None:
    data, _records = _older_data(tmp_path)
    _alter_source(data, f"UPDATE alembic_version SET version_num = '{revision}'")  # noqa: S608 - fixed test revisions
    with pytest.raises(RecoveryError, match=message):
        _restore(data, tmp_path)
    # The revision preflight refuses before staging or publishing anything.
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".restored.restore-*"))


def test_standalone_refuses_current_schema_relabelled_as_earlier(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    (data / "objects").mkdir(parents=True)
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()
    relabel = f"UPDATE alembic_version SET version_num = '{PRE_FILL_PLAN_REVISION}'"  # noqa: S608 - fixed test revision
    _alter_source(data, relabel)
    with pytest.raises(RecoveryError, match="migration failed"):
        _restore(data, tmp_path)
    assert not (tmp_path / "restored").exists()
