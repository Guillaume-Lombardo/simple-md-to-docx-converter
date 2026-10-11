"""Unit coverage for the earlier-schema recovery-set contract."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from alembic.util.exc import CommandError
from sqlalchemy import func, select
from sqlalchemy import inspect as sqlalchemy_inspect

from markweave import recovery_schema
from markweave.persistence import migrations
from markweave.persistence.errors import PersistenceError
from markweave.persistence.migrations import (
    migration_lineage,
    upgrade_connection,
    upgrade_database_to,
)
from markweave.persistence.schema import ComposerArtifactRow
from markweave.persistence.sql import create_database_engine, standalone_database_url
from markweave.recovery_adapters import (
    PostgreSQLRecoveryAdapter,
    RecoveryDeadline,
    StandaloneRecoveryAdapter,
    _regular_object,
    _verify_database_references,
)
from markweave.recovery_manifest import RecoveryError, canonical_json
from markweave.recovery_schema import (
    AUXILIARY_TABLES,
    OLDEST_RECOVERY_SET_REVISION,
    head_revision,
    read_reference_rows,
    reference_shape,
    require_complete_shape,
    require_restorable_revision,
    sqlite_revision,
    sqlite_shape,
)
from tests.e2e.recovery_cli_setup import historical_database_payload
from tests.recovery_older_schema import PRE_FILL_PLAN_REVISION, populate_older_schema
from tests.unit.test_recovery_provider_adapters import _PostgreSQLEngine

pytestmark = pytest.mark.unit


@pytest.fixture
def uncached_lineage() -> Iterator[None]:
    recovery_schema._lineage.cache_clear()
    yield
    recovery_schema._lineage.cache_clear()


def test_restorable_revisions_follow_the_linear_migration_history() -> None:
    lineage = migration_lineage()
    assert lineage[-1] == head_revision()
    assert require_restorable_revision(head_revision()) == head_revision()
    assert require_restorable_revision(PRE_FILL_PLAN_REVISION) == PRE_FILL_PLAN_REVISION
    assert (
        require_restorable_revision(OLDEST_RECOVERY_SET_REVISION)
        == OLDEST_RECOVERY_SET_REVISION
    )
    for unknown in ("20991231_99", "", None, 42):
        with pytest.raises(RecoveryError, match="unknown to this release"):
            require_restorable_revision(unknown)
    older = lineage[lineage.index(OLDEST_RECOVERY_SET_REVISION) - 1]
    with pytest.raises(RecoveryError, match="predates supported recovery sets"):
        require_restorable_revision(older)


@pytest.mark.usefixtures("uncached_lineage")
def test_unavailable_migration_history_fails_closed(mocker) -> None:
    mocker.patch(
        "markweave.recovery_schema.migration_lineage", side_effect=PersistenceError
    )
    with pytest.raises(RecoveryError, match="Migration history is unavailable"):
        require_restorable_revision(PRE_FILL_PLAN_REVISION)


def test_migration_lineage_requires_one_known_head(mocker) -> None:
    script = mocker.Mock()
    script.get_heads.return_value = ("one", "two")
    factory = mocker.patch.object(
        migrations.ScriptDirectory, "from_config", return_value=script
    )
    with pytest.raises(PersistenceError):
        migration_lineage()
    factory.side_effect = CommandError("broken history")
    with pytest.raises(PersistenceError):
        migration_lineage()


def test_targeted_upgrades_reject_blank_revisions_and_translate_failures(
    tmp_path: Path, mocker
) -> None:
    engine = create_database_engine(standalone_database_url(tmp_path))
    try:
        with pytest.raises(ValueError, match="blank"):
            upgrade_database_to(engine, " ")
        with engine.begin() as connection:
            with pytest.raises(ValueError, match="blank"):
                upgrade_connection(connection, "")
            mocker.patch.object(
                migrations.command, "upgrade", side_effect=CommandError("bad")
            )
            with pytest.raises(PersistenceError):
                upgrade_connection(connection, "head")
    finally:
        engine.dispose()

    connection = mocker.Mock()
    connection.dialect.name = "postgresql"
    upgrade = mocker.patch.object(migrations.command, "upgrade", return_value=None)
    upgrade_connection(connection, PRE_FILL_PLAN_REVISION)
    lock_parameters = connection.execute.call_args.args[1]
    assert lock_parameters == {"lock_id": migrations.POSTGRES_MIGRATION_LOCK}
    assert upgrade.call_args.args[1] == PRE_FILL_PLAN_REVISION


def test_reference_shape_follows_the_recorded_revision(mocker) -> None:
    earlier = reference_shape(PRE_FILL_PLAN_REVISION)
    current = reference_shape(head_revision())
    assert "composer_questions" in earlier
    assert "composer_fill_plans" not in earlier
    assert "composer_fill_plans" in current
    assert "typed_fill_snapshot" in current["composer_revisions"]
    assert "typed_fill_snapshot" not in earlier["composer_revisions"]
    assert set(current) >= AUXILIARY_TABLES
    assert "composer_sources" not in reference_shape(OLDEST_RECOVERY_SET_REVISION)

    reference_shape.cache_clear()
    mocker.patch(
        "markweave.recovery_schema.upgrade_database_to", side_effect=PersistenceError
    )
    try:
        with pytest.raises(RecoveryError, match="reference schema is unavailable"):
            reference_shape(PRE_FILL_PLAN_REVISION)
    finally:
        reference_shape.cache_clear()


def test_schema_completeness_refuses_missing_tables_and_columns() -> None:
    reference = {"composer_sources": frozenset({"id", "kind"})}
    require_complete_shape(
        {"composer_sources": frozenset({"id", "kind", "extra"}), "other": frozenset()},
        reference,
    )
    for observed in ({}, {"composer_sources": frozenset({"id"})}):
        with pytest.raises(RecoveryError, match="schema is incomplete"):
            require_complete_shape(observed, reference)


def test_sqlite_revision_and_shape_fail_closed(tmp_path: Path, mocker) -> None:
    with closing(sqlite3.connect(tmp_path / "database.sqlite3")) as connection:
        with pytest.raises(RecoveryError, match="schema is incomplete"):
            sqlite_revision(connection)
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.executemany(
            "INSERT INTO alembic_version VALUES (?)", (("one",), ("two",))
        )
        with pytest.raises(RecoveryError, match="schema is incomplete"):
            sqlite_revision(connection)
        assert sqlite_shape(connection) == {"alembic_version": {"version_num"}}

    broken = mocker.Mock()
    broken.execute.side_effect = sqlite3.DatabaseError("malformed")
    with pytest.raises(RecoveryError, match="schema is incomplete"):
        sqlite_shape(broken)


def test_reference_rows_read_only_columns_present_at_the_revision() -> None:
    statements: list[str] = []

    def query(statement: str) -> list[tuple[Any, ...]]:
        statements.append(statement)
        return [("owner", "source", True)]

    rows = read_reference_rows(
        query,
        {
            "conversion_jobs": frozenset(
                {"owner_id", "source_object_id", "source_ready"}
            )
        },
    )
    assert statements == [
        "SELECT owner_id, source_object_id, source_ready FROM conversion_jobs"
    ]
    assert rows == {
        "conversion_jobs": {
            "rows": [
                {
                    "owner_id": "owner",
                    "source_object_id": "source",
                    "source_ready": True,
                }
            ]
        }
    }


def test_reference_verification_refuses_unreadable_rows_and_unsafe_objects(
    tmp_path: Path,
) -> None:
    def failing(_statement: str) -> list[tuple[Any, ...]]:
        raise sqlite3.OperationalError("no such column")

    with pytest.raises(RecoveryError, match="schema is incomplete"):
        _verify_database_references(
            failing, {"template_versions": frozenset({"id"})}, lambda _key: True
        )

    regular = tmp_path / "regular"
    regular.write_bytes(b"object")
    linked = tmp_path / "linked"
    linked.symlink_to(regular)
    assert _regular_object(regular)
    assert not _regular_object(linked)
    assert not _regular_object(tmp_path)
    assert not _regular_object(tmp_path / "missing")


def _older_standalone(root: Path):
    data = (root / "data").resolve()
    (data / "objects").mkdir(parents=True)
    engine = create_database_engine(standalone_database_url(data))
    try:
        upgrade_database_to(engine, PRE_FILL_PLAN_REVISION)
        records = populate_older_schema(engine)
    finally:
        engine.dispose()
    for key, content in records.objects.items():
        path = data / "objects" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    staging = (root / "staging").resolve()
    staging.mkdir()
    adapter = StandaloneRecoveryAdapter()
    database, objects = adapter.backup(data, staging, RecoveryDeadline.after(30))
    return adapter, staging, database.members + objects.members


def test_standalone_restore_requires_the_migrated_head(tmp_path: Path, mocker) -> None:
    adapter, staging, members = _older_standalone(tmp_path)
    mocker.patch("markweave.recovery_adapters.upgrade_database", return_value=None)
    target = (tmp_path / "restored").resolve()
    with pytest.raises(RecoveryError, match="did not reach the head"):
        adapter.restore(staging, target, members, RecoveryDeadline.after(30))
    assert not target.exists()


def _distributed_fakes(tmp_path: Path, mocker, *, target_revision: str | None = None):
    source_engine = create_database_engine(standalone_database_url(tmp_path / "s"))
    target_engine = create_database_engine(standalone_database_url(tmp_path / "t"))
    upgrade_database_to(source_engine, PRE_FILL_PLAN_REVISION)
    records = populate_older_schema(source_engine)
    if target_revision is not None:
        upgrade_database_to(target_engine, target_revision)
    staging = tmp_path / "staging"
    (staging / "database").mkdir(parents=True)
    payload = staging / "database" / "metadata.json"
    with source_engine.connect() as connection:
        payload.write_bytes(
            canonical_json(historical_database_payload(connection)) + b"\n"
        )
    mocker.patch(
        "markweave.recovery_adapters.create_database_engine",
        return_value=_PostgreSQLEngine(
            target_engine, ("target", "main", "local", 5432)
        ),
    )
    mocker.patch(
        "markweave.recovery_adapters.inspect",
        side_effect=lambda connection: sqlalchemy_inspect(connection.raw),
    )
    mocker.patch(
        "markweave.recovery_adapters.upgrade_connection",
        side_effect=lambda connection, revision: upgrade_connection(
            connection.raw, revision
        ),
    )
    return (
        PostgreSQLRecoveryAdapter(),
        "source-identity",
        payload,
        records,
        (
            source_engine,
            target_engine,
        ),
    )


def _restore(adapter, source_identity: str, payload: Path, keys: frozenset[str]) -> str:
    return adapter.restore(
        "postgresql://target",
        payload,
        source_identity=source_identity,
        object_keys=keys,
        deadline=RecoveryDeadline.after(30),
    )


def test_distributed_adapter_restores_earlier_schema_payload_to_head(
    tmp_path: Path, mocker
) -> None:
    for name in ("s", "t"):
        (tmp_path / name).mkdir()
    adapter, backup, payload, records, (source, target) = _distributed_fakes(
        tmp_path, mocker
    )
    document = json.loads(payload.read_bytes())
    names = {table["name"] for table in document["tables"]}
    assert document["revision"] == PRE_FILL_PLAN_REVISION
    assert "composer_questions" in names
    assert "composer_fill_plans" not in names
    assert not names & AUXILIARY_TABLES

    with pytest.raises(RecoveryError, match="stable object references"):
        _restore(adapter, backup, payload, frozenset())
    assert _restore(adapter, backup, payload, frozenset(records.objects))
    with target.connect() as connection:
        assert (
            sqlalchemy_inspect(connection)
            .get_table_names()
            .count("composer_fill_plans")
        )
        assert (
            connection.scalar(select(func.count()).select_from(ComposerArtifactRow))
            == 1
        )
    source.dispose()
    target.dispose()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("unknown", "unknown to this release"),
        ("missing-column", "incompatible"),
        ("non-string-column", "incompatible"),
        ("extra-table", "incompatible"),
    ],
)
def test_distributed_adapter_refuses_mismatched_earlier_payloads(
    tmp_path: Path, mocker, change: str, message: str
) -> None:
    for name in ("s", "t"):
        (tmp_path / name).mkdir()
    adapter, backup, payload, records, (source, target) = _distributed_fakes(
        tmp_path, mocker
    )
    document = json.loads(payload.read_bytes())
    sources = next(t for t in document["tables"] if t["name"] == "composer_sources")
    if change == "unknown":
        document["revision"] = "20991231_99"
    elif change == "missing-column":
        sources["columns"].remove("kind")
    elif change == "non-string-column":
        sources["columns"][0] = 7
    else:
        document["tables"].append(
            {"name": "composer_fill_plans", "columns": [], "rows": []}
        )
    payload.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RecoveryError, match=message):
        _restore(adapter, backup, payload, frozenset(records.objects))
    source.dispose()
    target.dispose()


def test_distributed_adapter_refuses_earlier_payload_into_migrated_target(
    tmp_path: Path, mocker
) -> None:
    for name in ("s", "t"):
        (tmp_path / name).mkdir()
    adapter, backup, payload, records, (source, target) = _distributed_fakes(
        tmp_path, mocker, target_revision="head"
    )
    with pytest.raises(RecoveryError, match="must be empty for an earlier schema"):
        _restore(adapter, backup, payload, frozenset(records.objects))
    source.dispose()
    target.dispose()


def test_distributed_adapter_translates_migration_failure(
    tmp_path: Path, mocker
) -> None:
    for name in ("s", "t"):
        (tmp_path / name).mkdir()
    adapter, backup, payload, records, (source, target) = _distributed_fakes(
        tmp_path, mocker
    )
    mocker.patch(
        "markweave.recovery_adapters.upgrade_connection", side_effect=PersistenceError
    )
    with pytest.raises(RecoveryError, match="restore migration failed"):
        _restore(adapter, backup, payload, frozenset(records.objects))
    source.dispose()
    target.dispose()


def test_standalone_preflight_reads_the_set_without_writing(
    tmp_path: Path, mocker
) -> None:
    adapter, staging, members = _older_standalone(tmp_path)
    before = sorted(path.name for path in (staging / "database").iterdir())
    assert StandaloneRecoveryAdapter.preflight(staging, members) == (
        PRE_FILL_PLAN_REVISION
    )
    assert sorted(path.name for path in (staging / "database").iterdir()) == before
    database_members = tuple(
        member for member in members if member.path.startswith("objects/")
    )
    with pytest.raises(RecoveryError, match="integrity"):
        StandaloneRecoveryAdapter.preflight(staging, database_members)
    database = staging / "database" / "metadata.sqlite3"
    connect = mocker.patch(
        "markweave.recovery_adapters.sqlite3.connect",
        side_effect=sqlite3.OperationalError("unable to open"),
    )
    with pytest.raises(RecoveryError, match="integrity"):
        StandaloneRecoveryAdapter.preflight(staging, members)
    mocker.stop(connect)
    database.write_bytes(b"not a database")
    with pytest.raises(RecoveryError, match="schema is incomplete"):
        StandaloneRecoveryAdapter.preflight(staging, members)
    target = (tmp_path / "restored").resolve()
    with pytest.raises(RecoveryError, match="schema is incomplete"):
        adapter.restore(staging, target, members, RecoveryDeadline.after(30))
    assert not target.exists()
    assert not list(tmp_path.glob(".restored.restore-*"))
