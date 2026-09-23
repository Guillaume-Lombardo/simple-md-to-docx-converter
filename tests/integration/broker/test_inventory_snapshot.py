"""Real SQLite snapshot coverage for authenticated broker inventory reads."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker import inventory_storage
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

KEY = bytes(range(32))
PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-0000-0000-000000000001"))
FIRST_ATTEMPT_ID = UUID("20000000-0000-0000-0000-000000000001")
SECOND_ATTEMPT_ID = UUID("20000000-0000-0000-0000-000000000002")
FIRST_UNIT_ID = UUID("30000000-0000-0000-0000-000000000001")
SECOND_UNIT_ID = UUID("30000000-0000-0000-0000-000000000002")
POLICY_SPECIFICATION = EvidenceDigest("sha256:" + "4" * 64)


def _inventory(path: Path) -> SQLiteBrokerInventory:
    return SQLiteBrokerInventory(path, KEY, max_records=8)


def _unit(*, sequence: int, attempt_id: UUID, unit_id: UUID) -> ManagedUnit:
    return ManagedUnit(
        attempt_id=attempt_id,
        unit_id=unit_id,
        principal=PRINCIPAL,
        create_sequence=sequence,
        policy_revision="policy-v1",
        policy_specification=POLICY_SPECIFICATION,
        state=ManagedUnitState.RESERVED,
        revision=0,
    )


def _first_unit() -> ManagedUnit:
    return _unit(sequence=1, attempt_id=FIRST_ATTEMPT_ID, unit_id=FIRST_UNIT_ID)


def _second_unit() -> ManagedUnit:
    return _unit(sequence=2, attempt_id=SECOND_ATTEMPT_ID, unit_id=SECOND_UNIT_ID)


def _replay(sequence: int) -> ReplayPosition:
    return ReplayPosition(PRINCIPAL, sequence)


def test_read_uses_one_snapshot_when_writer_commits_during_verification(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "inventory.sqlite3"
    reader = _inventory(path)
    first = reader.reserve(_first_unit(), _replay(1))
    writer = _inventory(path)
    second = _second_unit()
    original = reader._verify_manifest
    interleaved = False

    def commit_during_verification(
        connection: sqlite3.Connection,
        principals: list[sqlite3.Row],
        units: list[sqlite3.Row],
    ) -> None:
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            writer.reserve(second, _replay(2))
        original(connection, principals, units)

    mocker.patch.object(
        reader, "_verify_manifest", side_effect=commit_during_verification
    )

    assert reader.get(FIRST_UNIT_ID) == first
    assert interleaved
    assert writer.get(SECOND_UNIT_ID) == second
    assert reader.get(SECOND_UNIT_ID) == second


def test_caller_read_stays_on_snapshot_when_writer_commits_after_verification(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "inventory.sqlite3"
    reader = _inventory(path)
    first = reader.reserve(_first_unit(), _replay(1))
    writer = _inventory(path)
    original = reader._verify_all
    interleaved = False

    def commit_after_verification(connection: sqlite3.Connection) -> None:
        nonlocal interleaved
        original(connection)
        if not interleaved:
            interleaved = True
            writer.transition(
                FIRST_UNIT_ID,
                expected_revision=0,
                target=ManagedUnitState.CREATE_INTENT,
            )

    mocker.patch.object(reader, "_verify_all", side_effect=commit_after_verification)

    assert reader.get(FIRST_UNIT_ID) == first
    assert interleaved
    updated = writer.get(FIRST_UNIT_ID)
    assert updated is not None
    assert updated.state is ManagedUnitState.CREATE_INTENT
    assert updated.revision == 1
    assert reader.get(FIRST_UNIT_ID) == updated


def test_constructor_verification_uses_one_snapshot_during_concurrent_commit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / "inventory.sqlite3"
    writer = _inventory(path)
    first = writer.reserve(_first_unit(), _replay(1))
    second = _second_unit()
    original = inventory_storage._SQLiteInventoryStorage._verify_manifest
    interleaved = False

    def commit_during_verification(
        storage: inventory_storage._SQLiteInventoryStorage,
        connection: sqlite3.Connection,
        principals: list[sqlite3.Row],
        units: list[sqlite3.Row],
    ) -> None:
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            writer.reserve(second, _replay(2))
        original(storage, connection, principals, units)

    mocker.patch.object(
        inventory_storage._SQLiteInventoryStorage,
        "_verify_manifest",
        autospec=True,
        side_effect=commit_during_verification,
    )

    reopened = _inventory(path)

    assert interleaved
    assert reopened.unacknowledged(limit=8) == (first, second)
