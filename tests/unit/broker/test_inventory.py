"""Unit tests for the crash-consistent authenticated SQLite broker inventory."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from markweave.broker import inventory as inventory_module
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    RuntimeRecoveryBinding,
    TerminationProof,
)

pytestmark = pytest.mark.unit

KEY = bytes(range(32))
OTHER_KEY = bytes(reversed(range(32)))
PRINCIPAL_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_PRINCIPAL_ID = UUID("10000000-0000-0000-0000-000000000002")
ATTEMPT_ID = UUID("20000000-0000-0000-0000-000000000001")
UNIT_ID = UUID("30000000-0000-0000-0000-000000000001")
INCARNATION_ID = UUID("40000000-0000-0000-0000-000000000001")
PROOF_ID = UUID("50000000-0000-0000-0000-000000000001")
SECOND_ATTEMPT_ID = UUID("20000000-0000-0000-0000-000000000002")
SECOND_UNIT_ID = UUID("30000000-0000-0000-0000-000000000002")
SECOND_PROOF_ID = UUID("50000000-0000-0000-0000-000000000002")
PRINCIPAL = AuthenticatedPrincipal(PRINCIPAL_ID)
EXIT = EvidenceDigest("sha256:" + "1" * 64)
EMPTY = EvidenceDigest("sha256:" + "2" * 64)
REMOVAL = EvidenceDigest("sha256:" + "3" * 64)
SPECIFICATION = EvidenceDigest("sha256:" + "4" * 64)
POLICY_SPECIFICATION = SPECIFICATION
INCARNATION = RuntimeIncarnation(INCARNATION_ID, SPECIFICATION)


def _inventory(
    path: Path, *, key: bytes = KEY, capacity: int = 8
) -> SQLiteBrokerInventory:
    return SQLiteBrokerInventory(path, key, max_records=capacity)


def _reserved(
    *,
    attempt_id: UUID = ATTEMPT_ID,
    unit_id: UUID = UNIT_ID,
    principal: AuthenticatedPrincipal = PRINCIPAL,
    sequence: int = 1,
) -> ManagedUnit:
    return ManagedUnit(
        attempt_id=attempt_id,
        unit_id=unit_id,
        principal=principal,
        create_sequence=sequence,
        policy_revision="policy-v1",
        policy_specification=POLICY_SPECIFICATION,
        state=ManagedUnitState.RESERVED,
        revision=0,
    )


def _replay(
    sequence: int = 1, principal: AuthenticatedPrincipal = PRINCIPAL
) -> ReplayPosition:
    return ReplayPosition(principal, sequence)


def _advance_reserved_to_empty(
    inventory: SQLiteBrokerInventory, unit: ManagedUnit
) -> ManagedUnit:
    inventory.reserve(unit, _replay(unit.create_sequence, unit.principal))
    inventory.transition(
        unit.unit_id, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
    )
    inventory.transition(
        unit.unit_id,
        expected_revision=1,
        target=ManagedUnitState.CREATED,
        runtime_incarnation=INCARNATION,
    )
    inventory.transition(
        unit.unit_id,
        expected_revision=2,
        target=ManagedUnitState.EXIT_CONFIRMED,
        evidence=EXIT,
    )
    return inventory.transition(
        unit.unit_id,
        expected_revision=3,
        target=ManagedUnitState.EMPTY_CONFIRMED,
        evidence=EMPTY,
    )


def _advance_to_empty(inventory: SQLiteBrokerInventory) -> ManagedUnit:
    return _advance_reserved_to_empty(inventory, _reserved())


def _proof(
    *,
    proof_id: UUID = PROOF_ID,
    attempt_id: UUID = ATTEMPT_ID,
    unit_id: UUID = UNIT_ID,
) -> TerminationProof:
    return TerminationProof(
        proof_id=proof_id,
        attempt_id=attempt_id,
        unit_id=unit_id,
        principal=PRINCIPAL,
        policy_revision="policy-v1",
        exit_evidence=EXIT,
        empty_evidence=EMPTY,
        removal_evidence=REMOVAL,
    )


def _raw(path: Path, statement: str, parameters: tuple[object, ...] = ()) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(statement, parameters)


def _assert_category(
    captured: pytest.ExceptionInfo[BrokerError], category: BrokerErrorCategory
) -> None:
    assert captured.value.category is category


def _downgrade_to_authenticated_v2(path: Path) -> None:
    mac = object.__new__(SQLiteBrokerInventory)
    mac._key = KEY
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        principal = connection.execute(inventory_module._SELECT_PRINCIPALS).fetchone()
        unit = connection.execute(inventory_module._SELECT_UNITS).fetchone()
        assert principal is not None and unit is not None
        connection.execute("ALTER TABLE units RENAME TO units_v3")
        connection.execute(inventory_module._LEGACY_CREATE_UNITS)
        old_unit_values = tuple(
            unit[column] for column in inventory_module._LEGACY_UNIT_COLUMNS[:-1]
        )
        columns = ", ".join(inventory_module._LEGACY_UNIT_COLUMNS)
        placeholders = ", ".join("?" for _ in inventory_module._LEGACY_UNIT_COLUMNS)
        connection.execute(
            f"INSERT INTO units ({columns}) VALUES ({placeholders})",  # noqa: S608
            (
                *old_unit_values,
                mac._mac_for(2, "unit", old_unit_values),
            ),
        )
        connection.execute("DROP TABLE units_v3")
        principal_values = tuple(principal[:-1])
        connection.execute(
            "UPDATE principals SET mac = ? WHERE principal_id = ?",
            (mac._mac_for(2, "principal", principal_values), principal[0]),
        )
        principals = connection.execute(
            inventory_module._SELECT_PRINCIPALS + " ORDER BY principal_id"
        ).fetchall()
        units = connection.execute(
            inventory_module._LEGACY_SELECT_UNITS + " ORDER BY unit_id"
        ).fetchall()
        manifest = connection.execute(inventory_module._SELECT_MANIFEST).fetchone()
        assert manifest is not None
        manifest_values = (
            1,
            manifest[1] + 1,
            len(principals),
            len(units),
            mac._aggregate_digest("principals", principals),
            mac._aggregate_digest("units", units),
            1,
        )
        connection.execute(
            "UPDATE inventory_manifest SET generation=?,principal_count=?,"
            "unit_count=?,principals_digest=?,units_digest=?,mac_version=?,mac=? "
            "WHERE singleton_id=1",
            (*manifest_values[1:], mac._mac_for(2, "manifest", manifest_values)),
        )
        connection.execute("PRAGMA user_version = 2")


def test_authenticated_v2_inventory_migrates_atomically_and_re_macs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    inventory.reserve(_reserved(), _replay())
    _downgrade_to_authenticated_v2(path)

    migrated = _inventory(path)

    assert migrated.get(UNIT_ID) == _reserved()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(units)")
        )
    assert columns == inventory_module._UNIT_COLUMNS
    _inventory(path)


def test_v2_migration_rejects_tamper_before_schema_mutation(tmp_path: Path) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    inventory.reserve(_reserved(), _replay())
    _downgrade_to_authenticated_v2(path)
    _raw(path, "UPDATE units SET attempt_id = ?", (str(SECOND_ATTEMPT_ID),))

    with pytest.raises(BrokerError) as caught:
        _inventory(path)
    _assert_category(caught, BrokerErrorCategory.INVENTORY_FAILURE)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_initialization_uses_wal_full_sync_closed_schema_and_never_stores_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    _inventory(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        connection.execute("PRAGMA synchronous = FULL")
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {
            "inventory_manifest",
            "principals",
            "units",
        }
        columns = {row[1] for row in connection.execute("PRAGMA table_info(units)")}
        assert not columns.intersection(
            {"source", "result", "filename", "path", "secret", "content_digest"}
        )
    assert KEY not in path.read_bytes()
    _inventory(path)


def test_runtime_recovery_binding_is_authenticated_and_round_trips(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    reserved = inventory.reserve(_reserved(), _replay())
    intent = inventory.transition(
        UNIT_ID,
        expected_revision=reserved.revision,
        target=ManagedUnitState.CREATE_INTENT,
    )
    binding = RuntimeRecoveryBinding(
        "kubernetes", 1, b'{"content_free":"runtime-identity"}'
    )
    created = inventory.transition(
        UNIT_ID,
        expected_revision=intent.revision,
        target=ManagedUnitState.CREATED,
        runtime_incarnation=INCARNATION,
        runtime_recovery=binding,
    )
    assert created.runtime_recovery == binding
    assert _inventory(path).get(UNIT_ID) == created
    _raw(path, "UPDATE units SET recovery_version = 2")
    with pytest.raises(BrokerError) as captured:
        _inventory(path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)


def test_reservation_is_write_ahead_authenticated_and_lookups_are_scoped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    unit = inventory.reserve(_reserved(), _replay())

    assert unit == _reserved()
    assert inventory.get(UNIT_ID) == unit
    assert inventory.find_attempt(PRINCIPAL_ID, ATTEMPT_ID) == unit
    assert inventory.find_attempt(OTHER_PRINCIPAL_ID, ATTEMPT_ID) is None
    assert inventory.create_sequence_high_watermark(PRINCIPAL_ID) == 1
    assert inventory.create_sequence_high_watermark(OTHER_PRINCIPAL_ID) == 0
    assert inventory.unacknowledged(limit=8) == (unit,)
    with closing(sqlite3.connect(path)) as connection:
        runtime_name = connection.execute("SELECT runtime_name FROM units").fetchone()[
            0
        ]
    assert runtime_name == f"markweave-reverse-{UNIT_ID.hex}"


def test_exact_create_replay_returns_existing_without_rerun_or_revision_change(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    first = inventory.reserve(_reserved(), _replay())
    replayed = inventory.reserve(
        _reserved(unit_id=UUID("30000000-0000-0000-0000-000000000099")),
        _replay(),
    )
    assert replayed == first
    assert inventory.create_sequence_high_watermark(PRINCIPAL_ID) == 1
    assert len(inventory.unacknowledged(limit=8)) == 1


def test_discard_reserved_is_cas_scoped_and_retains_create_high_water(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    inventory.reserve(_reserved(), _replay())
    assert not inventory.discard_reserved(UNIT_ID, expected_revision=1)
    assert inventory.discard_reserved(UNIT_ID, expected_revision=0)
    assert not inventory.discard_reserved(UNIT_ID, expected_revision=0)
    assert _inventory(path).create_sequence_high_watermark(PRINCIPAL_ID) == 1
    with pytest.raises(BrokerError) as captured:
        inventory.reserve(_reserved(), _replay())
    _assert_category(captured, BrokerErrorCategory.REPLAY_REJECTED)


def test_discard_reserved_never_deletes_after_create_intent(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    inventory.reserve(_reserved(), _replay())
    inventory.transition(
        UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
    )
    assert not inventory.discard_reserved(UNIT_ID, expected_revision=1)
    remaining = inventory.get(UNIT_ID)
    assert remaining is not None
    assert remaining.state is ManagedUnitState.CREATE_INTENT


@pytest.mark.parametrize(
    ("unit", "replay"),
    [
        (
            _reserved(attempt_id=UUID("20000000-0000-0000-0000-000000000002")),
            _replay(),
        ),
        (
            _reserved(sequence=2),
            _replay(2),
        ),
    ],
)
def test_conflicting_sequence_and_reused_attempt_are_rejected(
    tmp_path: Path, unit: ManagedUnit, replay: ReplayPosition
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    inventory.reserve(_reserved(), _replay())
    if unit.create_sequence == 2:
        unit = _reserved(sequence=2, unit_id=UNIT_ID)
    with pytest.raises(BrokerError) as captured:
        inventory.reserve(unit, replay)
    _assert_category(captured, BrokerErrorCategory.REPLAY_REJECTED)


def test_capacity_is_bounded_and_sweep_limit_is_validated(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3", capacity=1)
    inventory.reserve(_reserved(), _replay())
    other_principal = AuthenticatedPrincipal(OTHER_PRINCIPAL_ID)
    with pytest.raises(BrokerError) as captured:
        inventory.reserve(
            _reserved(
                attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
                unit_id=UUID("30000000-0000-0000-0000-000000000002"),
                principal=other_principal,
            ),
            _replay(principal=other_principal),
        )
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FULL)
    with pytest.raises(BrokerError, match="invalid"):
        inventory.unacknowledged(limit=2)


def test_sweep_rejects_overflow_instead_of_silently_truncating(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3", capacity=2)
    inventory.reserve(_reserved(), _replay())
    inventory.reserve(
        _reserved(
            attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
            unit_id=UUID("30000000-0000-0000-0000-000000000002"),
            sequence=2,
        ),
        _replay(2),
    )

    with pytest.raises(BrokerError) as captured:
        inventory.unacknowledged(limit=1)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FULL)


def test_principal_replay_ledger_remains_bounded_after_unit_discard(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3", capacity=1)
    inventory.reserve(_reserved(), _replay())
    assert inventory.discard_reserved(UNIT_ID, expected_revision=0)
    other_principal = AuthenticatedPrincipal(OTHER_PRINCIPAL_ID)
    with pytest.raises(BrokerError) as captured:
        inventory.reserve(
            _reserved(
                attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
                unit_id=UUID("30000000-0000-0000-0000-000000000002"),
                principal=other_principal,
            ),
            _replay(principal=other_principal),
        )
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FULL)


def test_each_transition_requires_exact_revision_and_exact_payload(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    inventory.reserve(_reserved(), _replay())
    create_intent = inventory.transition(
        UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
    )
    assert create_intent.revision == 1
    assert create_intent.runtime_incarnation is None

    created = inventory.transition(
        UNIT_ID,
        expected_revision=1,
        target=ManagedUnitState.CREATED,
        runtime_incarnation=INCARNATION,
    )
    exited = inventory.transition(
        UNIT_ID,
        expected_revision=2,
        target=ManagedUnitState.EXIT_CONFIRMED,
        evidence=EXIT,
    )
    empty = inventory.transition(
        UNIT_ID,
        expected_revision=3,
        target=ManagedUnitState.EMPTY_CONFIRMED,
        evidence=EMPTY,
    )
    assert created.runtime_incarnation == INCARNATION
    assert exited.exit_evidence == EXIT
    assert empty.empty_evidence == EMPTY


@pytest.mark.parametrize(
    ("target", "evidence", "incarnation"),
    [
        (ManagedUnitState.CREATED, None, None),
        (ManagedUnitState.CREATE_INTENT, EXIT, None),
        (ManagedUnitState.CREATE_INTENT, None, INCARNATION),
        (ManagedUnitState.REMOVED, REMOVAL, None),
    ],
)
def test_illegal_or_incomplete_transition_rolls_back(
    tmp_path: Path,
    target: ManagedUnitState,
    evidence: EvidenceDigest | None,
    incarnation: RuntimeIncarnation | None,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    before = inventory.reserve(_reserved(), _replay())
    with pytest.raises(BrokerError):
        inventory.transition(
            UNIT_ID,
            expected_revision=0,
            target=target,
            evidence=evidence,
            runtime_incarnation=incarnation,
        )
    assert inventory.get(UNIT_ID) == before


def test_stale_revision_and_skipped_state_are_replay_rejected(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    inventory.reserve(_reserved(), _replay())
    with pytest.raises(BrokerError) as captured:
        inventory.transition(
            UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATED
        )
    _assert_category(captured, BrokerErrorCategory.REPLAY_REJECTED)
    inventory.transition(
        UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
    )
    with pytest.raises(BrokerError) as captured:
        inventory.transition(
            UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATED
        )
    _assert_category(captured, BrokerErrorCategory.REPLAY_REJECTED)


def test_removed_state_and_proof_are_one_atomic_authenticated_tombstone(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    _advance_to_empty(inventory)
    removed = inventory.mark_removed(
        UNIT_ID,
        expected_revision=4,
        removal_evidence=REMOVAL,
        proof=_proof(),
    )
    assert removed.state is ManagedUnitState.REMOVED
    assert removed.removal_evidence == REMOVAL
    assert inventory.get_proof(UNIT_ID) == _proof()
    assert inventory.unacknowledged(limit=8) == (removed,)
    assert _inventory(tmp_path / "inventory.sqlite3").get_proof(UNIT_ID) == _proof()


def test_mismatched_proof_rolls_back_without_partial_removal(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    empty = _advance_to_empty(inventory)
    wrong = TerminationProof(
        proof_id=PROOF_ID,
        attempt_id=ATTEMPT_ID,
        unit_id=UNIT_ID,
        principal=PRINCIPAL,
        policy_revision="policy-v1",
        exit_evidence=EXIT,
        empty_evidence=EMPTY,
        removal_evidence=EvidenceDigest("sha256:" + "9" * 64),
    )
    with pytest.raises(BrokerError):
        inventory.mark_removed(
            UNIT_ID,
            expected_revision=4,
            removal_evidence=REMOVAL,
            proof=wrong,
        )
    assert inventory.get(UNIT_ID) == empty
    assert inventory.get_proof(UNIT_ID) is None


def test_acknowledgement_requires_exact_four_way_binding_and_retains_high_water(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    _advance_to_empty(inventory)
    inventory.mark_removed(
        UNIT_ID, expected_revision=4, removal_evidence=REMOVAL, proof=_proof()
    )
    assert not inventory.acknowledge(OTHER_PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    assert not inventory.acknowledge(
        PRINCIPAL_ID,
        UUID("20000000-0000-0000-0000-000000000099"),
        UNIT_ID,
        PROOF_ID,
    )
    assert inventory.get_proof(UNIT_ID) == _proof()
    assert inventory.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    reopened = _inventory(path)
    assert reopened.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    assert reopened.acknowledge(
        PRINCIPAL_ID,
        ATTEMPT_ID,
        UNIT_ID,
        UUID("50000000-0000-0000-0000-000000000099"),
    )
    assert reopened.get(UNIT_ID) is None
    assert reopened.create_sequence_high_watermark(PRINCIPAL_ID) == 1
    with pytest.raises(BrokerError) as captured:
        inventory.reserve(_reserved(), _replay())
    _assert_category(captured, BrokerErrorCategory.REPLAY_REJECTED)


def test_durable_acknowledgement_marker_survives_restart_before_exact_discard(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    _advance_to_empty(inventory)
    removed = inventory.mark_removed(
        UNIT_ID, expected_revision=4, removal_evidence=REMOVAL, proof=_proof()
    )

    marked = inventory.mark_acknowledged(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)

    assert marked is not None
    assert marked.proof_acknowledged
    assert marked.revision == removed.revision + 1
    reopened = _inventory(path)
    assert reopened.get(UNIT_ID) == marked
    assert not reopened.discard_acknowledged(
        UNIT_ID, expected_revision=removed.revision
    )
    assert reopened.discard_acknowledged(UNIT_ID, expected_revision=marked.revision)
    assert not reopened.discard_acknowledged(UNIT_ID, expected_revision=marked.revision)
    assert reopened.get(UNIT_ID) is None


def test_missing_arbitrary_acknowledgement_is_a_state_free_success(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    existing = inventory.reserve(_reserved(), _replay())

    assert inventory.acknowledge(
        OTHER_PRINCIPAL_ID,
        SECOND_ATTEMPT_ID,
        SECOND_UNIT_ID,
        SECOND_PROOF_ID,
    )
    assert inventory.get(UNIT_ID) == existing
    assert inventory.create_sequence_high_watermark(PRINCIPAL_ID) == 1


def test_old_acknowledgement_cannot_delete_reused_active_unit_id(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    _advance_to_empty(inventory)
    inventory.mark_removed(
        UNIT_ID, expected_revision=4, removal_evidence=REMOVAL, proof=_proof()
    )
    assert inventory.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    replacement = _reserved(
        attempt_id=SECOND_ATTEMPT_ID,
        unit_id=UNIT_ID,
        sequence=2,
    )
    inventory.reserve(replacement, _replay(2))

    assert not inventory.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    assert inventory.get(UNIT_ID) == replacement


def test_all_acknowledgements_survive_interleaving_restart_and_later_activity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    first_proof = _proof()
    _advance_to_empty(inventory)
    inventory.mark_removed(
        UNIT_ID,
        expected_revision=4,
        removal_evidence=REMOVAL,
        proof=first_proof,
    )
    assert inventory.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)

    second = _reserved(
        attempt_id=SECOND_ATTEMPT_ID,
        unit_id=SECOND_UNIT_ID,
        sequence=2,
    )
    second_proof = _proof(
        proof_id=SECOND_PROOF_ID,
        attempt_id=SECOND_ATTEMPT_ID,
        unit_id=SECOND_UNIT_ID,
    )
    _advance_reserved_to_empty(inventory, second)
    inventory.mark_removed(
        SECOND_UNIT_ID,
        expected_revision=4,
        removal_evidence=REMOVAL,
        proof=second_proof,
    )
    assert inventory.acknowledge(
        PRINCIPAL_ID, SECOND_ATTEMPT_ID, SECOND_UNIT_ID, SECOND_PROOF_ID
    )

    reopened = _inventory(path)
    assert reopened.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    assert reopened.acknowledge(
        PRINCIPAL_ID, SECOND_ATTEMPT_ID, SECOND_UNIT_ID, SECOND_PROOF_ID
    )
    assert reopened.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, SECOND_UNIT_ID, PROOF_ID)
    later = _reserved(
        attempt_id=UUID("20000000-0000-0000-0000-000000000003"),
        unit_id=UUID("30000000-0000-0000-0000-000000000003"),
        sequence=3,
    )
    reopened.reserve(later, _replay(3))
    assert reopened.acknowledge(PRINCIPAL_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID)
    assert reopened.create_sequence_high_watermark(PRINCIPAL_ID) == 3


def test_forged_unit_mac_is_detected_even_when_getting_an_unrelated_unit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    inventory.reserve(_reserved(), _replay())
    other = _reserved(
        attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
        unit_id=UUID("30000000-0000-0000-0000-000000000002"),
        sequence=2,
    )
    inventory.reserve(other, _replay(2))
    _raw(
        path,
        "UPDATE units SET policy_revision = 'forged' WHERE unit_id = ?",
        (str(UNIT_ID),),
    )
    with pytest.raises(BrokerError) as captured:
        inventory.get(other.unit_id)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)
    with pytest.raises(BrokerError):
        _inventory(path)


def test_forged_principal_mac_and_wrong_key_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "inventory.sqlite3"
    inventory = _inventory(path)
    inventory.reserve(_reserved(), _replay())
    with pytest.raises(BrokerError):
        _inventory(path, key=OTHER_KEY)
    _raw(path, "UPDATE principals SET high_water = 99")
    with pytest.raises(BrokerError):
        inventory.unacknowledged(limit=8)


def test_manifest_detects_deleted_unit_principal_and_removed_tombstone(
    tmp_path: Path,
) -> None:
    unit_path = tmp_path / "deleted-unit.sqlite3"
    unit_inventory = _inventory(unit_path)
    unit_inventory.reserve(_reserved(), _replay())
    _raw(unit_path, "DELETE FROM units")
    with pytest.raises(BrokerError) as captured:
        _inventory(unit_path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)

    principal_path = tmp_path / "deleted-principal.sqlite3"
    principal_inventory = _inventory(principal_path)
    principal_inventory.reserve(_reserved(), _replay())
    assert principal_inventory.discard_reserved(UNIT_ID, expected_revision=0)
    _raw(principal_path, "DELETE FROM principals")
    with pytest.raises(BrokerError) as captured:
        _inventory(principal_path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)

    removed_path = tmp_path / "deleted-tombstone.sqlite3"
    removed_inventory = _inventory(removed_path)
    _advance_to_empty(removed_inventory)
    removed_inventory.mark_removed(
        UNIT_ID, expected_revision=4, removal_evidence=REMOVAL, proof=_proof()
    )
    _raw(removed_path, "DELETE FROM units")
    with pytest.raises(BrokerError) as captured:
        _inventory(removed_path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)


def test_manifest_detects_valid_authenticated_unit_substitution(tmp_path: Path) -> None:
    target_path = tmp_path / "target.sqlite3"
    donor_path = tmp_path / "donor.sqlite3"
    _inventory(target_path).reserve(_reserved(), _replay())
    donor_unit = _reserved(
        attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
        unit_id=UUID("30000000-0000-0000-0000-000000000002"),
    )
    _inventory(donor_path).reserve(donor_unit, _replay())
    with closing(sqlite3.connect(donor_path)) as donor:
        row = donor.execute("SELECT * FROM units").fetchone()
    assert row is not None
    with closing(sqlite3.connect(target_path)) as target, target:
        target.execute("DELETE FROM units")
        target.execute(
            "INSERT INTO units VALUES ("  # noqa: S608 - fixed test schema
            + ",".join("?" for _ in row)
            + ")",
            row,
        )

    with pytest.raises(BrokerError) as captured:
        _inventory(target_path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)


def test_manifest_detects_valid_authenticated_principal_substitution(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "target.sqlite3"
    donor_path = tmp_path / "donor.sqlite3"
    target = _inventory(target_path)
    target.reserve(_reserved(), _replay())
    assert target.discard_reserved(UNIT_ID, expected_revision=0)
    other_principal = AuthenticatedPrincipal(OTHER_PRINCIPAL_ID)
    donor = _inventory(donor_path)
    donor.reserve(
        _reserved(principal=other_principal), _replay(principal=other_principal)
    )
    assert donor.discard_reserved(UNIT_ID, expected_revision=0)
    with closing(sqlite3.connect(donor_path)) as donor_connection:
        row = donor_connection.execute("SELECT * FROM principals").fetchone()
    assert row is not None
    with closing(sqlite3.connect(target_path)) as target_connection, target_connection:
        target_connection.execute("DELETE FROM principals")
        target_connection.execute(
            "INSERT INTO principals VALUES ("  # noqa: S608 - fixed test schema
            + ",".join("?" for _ in row)
            + ")",
            row,
        )

    with pytest.raises(BrokerError) as captured:
        _inventory(target_path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)


@pytest.mark.parametrize(
    "statement",
    [
        "PRAGMA user_version = 99",
        "PRAGMA application_id = 0",
        "CREATE TABLE extra (value TEXT)",
        "CREATE VIEW extra AS SELECT principal_id FROM principals",
    ],
)
def test_unknown_schema_version_application_or_table_fails_closed(
    tmp_path: Path, statement: str
) -> None:
    path = tmp_path / "inventory.sqlite3"
    _inventory(path)
    _raw(path, statement)
    with pytest.raises(BrokerError) as captured:
        _inventory(path)
    _assert_category(captured, BrokerErrorCategory.INVENTORY_FAILURE)


def test_concurrent_conflicting_reservations_create_exactly_one_unit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.sqlite3"
    _inventory(path)
    units = (
        _reserved(),
        _reserved(
            attempt_id=UUID("20000000-0000-0000-0000-000000000002"),
            unit_id=UUID("30000000-0000-0000-0000-000000000002"),
        ),
    )

    def reserve(unit: ManagedUnit) -> ManagedUnit | BrokerErrorCategory:
        try:
            return _inventory(path).reserve(unit, _replay())
        except BrokerError as error:
            return error.category

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(reserve, units))
    assert sum(isinstance(outcome, ManagedUnit) for outcome in outcomes) == 1
    assert BrokerErrorCategory.REPLAY_REJECTED in outcomes
    assert len(_inventory(path).unacknowledged(limit=8)) == 1


@pytest.mark.parametrize(
    ("path_factory", "key", "capacity"),
    [
        (lambda root: Path("relative.sqlite3"), KEY, 1),
        (lambda root: root / "inventory.sqlite3", b"short", 1),
        (lambda root: root / "inventory.sqlite3", KEY, 0),
    ],
)
def test_constructor_rejects_unsafe_configuration(
    tmp_path: Path,
    path_factory: Callable[[Path], Path],
    key: bytes,
    capacity: int,
) -> None:
    with pytest.raises(ValueError):
        SQLiteBrokerInventory(path_factory(tmp_path), key, max_records=capacity)


def test_public_operations_reject_invalid_identity_and_missing_unit(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path / "inventory.sqlite3")
    invalid_uuid = cast(UUID, "not-a-uuid")
    with pytest.raises(BrokerError):
        inventory.reserve(cast(ManagedUnit, object()), _replay())
    with pytest.raises(BrokerError):
        inventory.create_sequence_high_watermark(invalid_uuid)
    with pytest.raises(BrokerError):
        inventory.discard_reserved(invalid_uuid, expected_revision=0)
    with pytest.raises(BrokerError):
        inventory.get(invalid_uuid)
    with pytest.raises(BrokerError):
        inventory.find_attempt(invalid_uuid, ATTEMPT_ID)
    with pytest.raises(BrokerError):
        inventory.transition(
            UNIT_ID, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
        )
    with pytest.raises(BrokerError):
        inventory.mark_removed(
            UNIT_ID, expected_revision=0, removal_evidence=REMOVAL, proof=_proof()
        )
    with pytest.raises(BrokerError):
        inventory.get_proof(invalid_uuid)
    with pytest.raises(BrokerError):
        inventory.acknowledge(invalid_uuid, ATTEMPT_ID, UNIT_ID, PROOF_ID)


def test_database_open_error_and_physical_corruption_fail_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(BrokerError):
        _inventory(tmp_path)
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(BrokerError):
        _inventory(path)


def test_exact_schema_shape_and_definition_are_verified(tmp_path: Path) -> None:
    columns_path = tmp_path / "columns.sqlite3"
    _inventory(columns_path)
    _raw(columns_path, "ALTER TABLE principals ADD COLUMN extra TEXT")
    with pytest.raises(BrokerError):
        _inventory(columns_path)

    definition_path = tmp_path / "definition.sqlite3"
    _inventory(definition_path)
    with closing(sqlite3.connect(definition_path)) as connection, connection:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE name = 'principals'",
            ("high_water > 0", "high_water >= 0"),
        )
        connection.execute("PRAGMA writable_schema = OFF")
    with pytest.raises(BrokerError):
        _inventory(definition_path)
