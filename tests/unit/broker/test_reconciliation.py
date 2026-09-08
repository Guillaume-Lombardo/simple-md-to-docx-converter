"""Restart reconciliation coverage for the reverse-isolation broker."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.fake_runtime import (
    FakeIsolationRuntime,
    FakeRuntimeError,
    FakeRuntimeState,
    FakeRuntimeUnit,
    FaultPoint,
)
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.broker.service import IsolationBrokerService

pytestmark = pytest.mark.unit

PRINCIPAL = AuthenticatedPrincipal(UUID("11000000-0000-4000-8000-000000000001"))
ATTEMPT_ID = UUID("21000000-0000-4000-8000-000000000001")
UNIT_ID = UUID("31000000-0000-4000-8000-000000000001")
UNKNOWN_UNIT_ID = UUID("31000000-0000-4000-8000-000000000002")
POLICY = BrokerPolicy(
    "t71-v1",
    "sha256:" + "b" * 64,
    RuntimeLimits(100_000, 100_000, 512_000_000, 32, 16_000_000, 30_000),
    RuntimeChannelLimits(1_000_000, 2_000_000),
)
ROLLED_POLICY = BrokerPolicy(
    "t71-v2",
    "sha256:" + "d" * 64,
    RuntimeLimits(90_000, 100_000, 384_000_000, 24, 12_000_000, 25_000),
    RuntimeChannelLimits(900_000, 1_800_000),
)


def _inventory(tmp_path: Path) -> SQLiteBrokerInventory:
    return SQLiteBrokerInventory(
        tmp_path / "reconciliation.sqlite3",
        b"r" * 32,
        max_records=32,
    )


def _service(
    inventory: SQLiteBrokerInventory,
    runtime: FakeIsolationRuntime,
    policy: BrokerPolicy = POLICY,
) -> IsolationBrokerService:
    return IsolationBrokerService(
        inventory,
        runtime,
        policy,
        max_discovered_units=32,
        unit_id_factory=lambda: UNIT_ID,
    )


def _reserve(inventory: SQLiteBrokerInventory) -> ManagedUnit:
    unit = ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        PRINCIPAL,
        1,
        POLICY.revision,
        policy_specification_evidence(POLICY),
        ManagedUnitState.RESERVED,
        0,
    )
    return inventory.reserve(unit, ReplayPosition(PRINCIPAL, 1))


def _intent(inventory: SQLiteBrokerInventory) -> ManagedUnit:
    reserved = _reserve(inventory)
    return inventory.transition(
        reserved.unit_id,
        expected_revision=reserved.revision,
        target=ManagedUnitState.CREATE_INTENT,
    )


def _created(
    inventory: SQLiteBrokerInventory,
    runtime: FakeIsolationRuntime,
) -> tuple[ManagedUnit, FakeRuntimeUnit]:
    intent = _intent(inventory)
    runtime_unit = runtime.create(intent, POLICY)
    created = inventory.transition(
        intent.unit_id,
        expected_revision=intent.revision,
        target=ManagedUnitState.CREATED,
        runtime_incarnation=runtime_unit.incarnation,
    )
    return created, runtime_unit


def test_start_discards_reserved_without_creating_and_retains_high_water(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    _reserve(inventory)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)

    broker.start()

    assert broker.ready
    assert inventory.get(UNIT_ID) is None
    assert inventory.create_sequence_high_watermark(PRINCIPAL.principal_id) == 1
    assert "create:before" not in runtime.calls


def test_create_intent_without_runtime_is_ambiguous_and_fails_closed(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    _intent(inventory)
    broker = _service(inventory, FakeIsolationRuntime())

    with pytest.raises(BrokerError) as caught:
        broker.start()

    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert not broker.ready
    retained = inventory.get(UNIT_ID)
    assert retained is not None
    assert retained.state is ManagedUnitState.CREATE_INTENT


def test_create_intent_with_exact_discovery_is_terminated_and_proven(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    intent = _intent(inventory)
    runtime = FakeIsolationRuntime()
    runtime_unit = runtime.create(intent, POLICY)
    broker = _service(inventory, runtime)

    broker.start()

    removed = inventory.get(UNIT_ID)
    assert broker.ready
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert removed.runtime_incarnation == runtime_unit.incarnation
    assert inventory.get_proof(UNIT_ID) is not None


def test_unknown_label_only_runtime_fails_closed(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    runtime.seed(
        UNKNOWN_UNIT_ID,
        RuntimeIncarnation(
            UUID("41000000-0000-4000-8000-000000000001"),
            policy_specification_evidence(POLICY),
        ),
    )
    broker = _service(inventory, runtime)

    with pytest.raises(BrokerError) as caught:
        broker.start()

    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert not broker.ready


def test_discovered_specification_mismatch_fails_closed(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    intent = _intent(inventory)
    runtime = FakeIsolationRuntime()
    runtime.seed(
        UNIT_ID,
        RuntimeIncarnation(
            UUID("41000000-0000-4000-8000-000000000002"),
            EvidenceDigest("sha256:" + "c" * 64),
        ),
    )
    broker = _service(inventory, runtime)

    with pytest.raises(BrokerError) as caught:
        broker.start()

    assert intent.state is ManagedUnitState.CREATE_INTENT
    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert not broker.ready


def test_created_state_resumes_the_common_termination_path(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    created, _ = _created(inventory, runtime)
    broker = _service(inventory, runtime)

    broker.start()

    removed = inventory.get(created.unit_id)
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert inventory.get_proof(created.unit_id) is not None


def test_exit_confirmed_state_resumes_at_empty_confirmation(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    created, runtime_unit = _created(inventory, runtime)
    runtime.hard_terminate(runtime_unit)
    exit_evidence = runtime.confirm_exit(runtime_unit)
    exited = inventory.transition(
        created.unit_id,
        expected_revision=created.revision,
        target=ManagedUnitState.EXIT_CONFIRMED,
        evidence=exit_evidence,
    )
    call_count = len(runtime.calls)

    _service(inventory, runtime).start()

    assert "hard_terminate:before" not in runtime.calls[call_count:]
    removed = inventory.get(exited.unit_id)
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED


def test_absent_after_durable_empty_finishes_removal_from_prior_evidence(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    created, runtime_unit = _created(inventory, runtime)
    runtime.hard_terminate(runtime_unit)
    exit_evidence = runtime.confirm_exit(runtime_unit)
    exited = inventory.transition(
        created.unit_id,
        expected_revision=created.revision,
        target=ManagedUnitState.EXIT_CONFIRMED,
        evidence=exit_evidence,
    )
    empty_evidence = runtime.confirm_empty(runtime_unit)
    empty = inventory.transition(
        exited.unit_id,
        expected_revision=exited.revision,
        target=ManagedUnitState.EMPTY_CONFIRMED,
        evidence=empty_evidence,
    )
    runtime.remove(runtime_unit)
    assert runtime.discover(limit=32) == ()

    broker = _service(inventory, runtime)
    broker.start()

    removed = inventory.get(empty.unit_id)
    assert broker.ready
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert inventory.get_proof(empty.unit_id) is not None


def test_removed_tombstone_is_verified_without_repeating_runtime_actions(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    proof = broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)
    call_count = len(runtime.calls)

    restarted = _service(inventory, runtime)
    restarted.start()

    assert restarted.ready
    assert restarted.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id) == proof
    assert runtime.calls[call_count:] == ("discover:before", "discover:after")


def test_runtime_create_after_fault_is_reconciled_on_restart(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    runtime.inject_fault("create", point="after")
    broker = _service(inventory, runtime)
    broker.start()

    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    assert caught.value.category is BrokerErrorCategory.RUNTIME_FAILURE
    assert not broker.ready
    intent = inventory.get(UNIT_ID)
    assert intent is not None
    assert intent.state is ManagedUnitState.CREATE_INTENT

    restarted = _service(inventory, runtime)
    restarted.start()
    assert restarted.ready
    removed = inventory.get(UNIT_ID)
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED


def test_runtime_fault_during_reconciliation_keeps_readiness_closed(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    _created(inventory, runtime)
    runtime.inject_fault("hard_terminate")
    broker = _service(inventory, runtime)

    with pytest.raises(BrokerError) as caught:
        broker.start()

    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert not broker.ready


@pytest.mark.parametrize(
    ("operation", "point", "interrupted_state"),
    (
        ("hard_terminate", "before", ManagedUnitState.CREATED),
        ("hard_terminate", "after", ManagedUnitState.CREATED),
        ("confirm_exit", "before", ManagedUnitState.CREATED),
        ("confirm_exit", "after", ManagedUnitState.CREATED),
        ("confirm_empty", "before", ManagedUnitState.EXIT_CONFIRMED),
        ("confirm_empty", "after", ManagedUnitState.EXIT_CONFIRMED),
        ("remove", "before", ManagedUnitState.EMPTY_CONFIRMED),
        ("remove", "after", ManagedUnitState.EMPTY_CONFIRMED),
        ("confirm_removed", "before", ManagedUnitState.EMPTY_CONFIRMED),
        ("confirm_removed", "after", ManagedUnitState.EMPTY_CONFIRMED),
    ),
)
def test_every_runtime_fault_resumes_exactly_on_restart(
    tmp_path: Path,
    operation: str,
    point: FaultPoint,
    interrupted_state: ManagedUnitState,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    runtime.inject_fault(operation, point=point)

    with pytest.raises(BrokerError) as caught:
        broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
    assert not broker.ready
    interrupted = inventory.get(created.unit_id)
    assert interrupted is not None
    assert interrupted.state is interrupted_state
    assert inventory.get_proof(created.unit_id) is None

    restarted = _service(inventory, runtime)
    restarted.start()
    removed = inventory.get(created.unit_id)
    assert restarted.ready
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert restarted.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id) is not None


@pytest.mark.parametrize(
    ("commit", "target", "interrupted_state"),
    (
        (
            "transition",
            ManagedUnitState.EXIT_CONFIRMED,
            ManagedUnitState.EXIT_CONFIRMED,
        ),
        (
            "transition",
            ManagedUnitState.EMPTY_CONFIRMED,
            ManagedUnitState.EMPTY_CONFIRMED,
        ),
        ("mark_removed", ManagedUnitState.REMOVED, ManagedUnitState.REMOVED),
    ),
)
def test_persistence_failure_after_commit_resumes_exactly_on_restart(
    tmp_path: Path,
    mocker: MockerFixture,
    commit: str,
    target: ManagedUnitState,
    interrupted_state: ManagedUnitState,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    original = getattr(inventory, commit)

    def commit_then_fail(*args, **kwargs):
        result = original(*args, **kwargs)
        observed_target = kwargs.get("target", ManagedUnitState.REMOVED)
        if observed_target is target:
            raise BrokerError(BrokerErrorCategory.INVENTORY_FAILURE)
        return result

    patch = mocker.patch.object(inventory, commit, side_effect=commit_then_fail)
    with pytest.raises(BrokerError) as caught:
        broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    assert not broker.ready
    interrupted = inventory.get(created.unit_id)
    assert interrupted is not None
    assert interrupted.state is interrupted_state
    retained = inventory.get_proof(created.unit_id)
    assert (retained is not None) is (target is ManagedUnitState.REMOVED)

    mocker.stop(patch)
    restarted = _service(inventory, runtime)
    restarted.start()
    proof = restarted.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id)
    assert restarted.ready
    assert proof is not None


def test_policy_rollover_reconciles_old_created_unit(tmp_path: Path) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    old_broker = _service(inventory, runtime)
    old_broker.start()
    created = old_broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert created.policy_specification == policy_specification_evidence(POLICY)

    rolled_broker = _service(inventory, runtime, ROLLED_POLICY)
    rolled_broker.start()

    removed = inventory.get(created.unit_id)
    assert rolled_broker.ready
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert removed.policy_revision == POLICY.revision
    assert removed.policy_specification == policy_specification_evidence(POLICY)


def test_policy_rollover_reconciles_old_create_intent_by_persisted_specification(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    intent = _intent(inventory)
    runtime = FakeIsolationRuntime()
    runtime.create(intent, POLICY)

    rolled_broker = _service(inventory, runtime, ROLLED_POLICY)
    rolled_broker.start()

    removed = inventory.get(intent.unit_id)
    assert rolled_broker.ready
    assert removed is not None
    assert removed.state is ManagedUnitState.REMOVED
    assert removed.policy_specification == policy_specification_evidence(POLICY)


def test_policy_rollover_keeps_old_tombstone_provable_and_acknowledgeable(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    old_broker = _service(inventory, runtime)
    old_broker.start()
    created = old_broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    proof = old_broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    rolled_broker = _service(inventory, runtime, ROLLED_POLICY)
    rolled_broker.start()

    assert rolled_broker.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id) == proof
    assert rolled_broker.acknowledge(
        PRINCIPAL, ATTEMPT_ID, created.unit_id, proof.proof_id
    )


def test_reserved_discard_failure_keeps_readiness_closed(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    inventory = _inventory(tmp_path)
    _reserve(inventory)
    runtime = FakeIsolationRuntime()
    mocker.patch.object(inventory, "discard_reserved", return_value=False)
    broker = _service(inventory, runtime)

    with pytest.raises(BrokerError) as caught:
        broker.start()

    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert not broker.ready
    assert "create:before" not in runtime.calls


def test_created_runtime_absence_cannot_be_used_as_termination_proof(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    runtime.forget(created.unit_id)

    with pytest.raises(BrokerError) as caught:
        broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
    assert not broker.ready


def test_discovered_incarnation_must_match_durable_incarnation(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    runtime.forget(created.unit_id)
    runtime.seed(
        created.unit_id,
        RuntimeIncarnation(
            UUID("41000000-0000-4000-8000-000000000099"),
            policy_specification_evidence(POLICY),
        ),
    )

    with pytest.raises(BrokerError) as caught:
        broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
    assert not broker.ready


def test_missing_retained_proof_fails_closed(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    broker = _service(inventory, runtime)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)
    mocker.patch.object(inventory, "get_proof", return_value=None)

    with pytest.raises(BrokerError) as caught:
        broker.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
    assert not broker.ready


@pytest.mark.parametrize(
    "values",
    (
        (False, False, False, True),
        (False, False, True, False),
        (False, True, False, False),
        (False, 1, False, False),
    ),
)
def test_fake_runtime_state_rejects_impossible_evidence_order(
    values: tuple[bool, bool, bool, bool],
) -> None:
    with pytest.raises(ValueError):
        FakeRuntimeState(*values)


def test_fake_runtime_rejects_invalid_faults_and_unknown_units() -> None:
    runtime = FakeIsolationRuntime()
    incarnation = RuntimeIncarnation(
        UUID("41000000-0000-4000-8000-000000000003"),
        policy_specification_evidence(POLICY),
    )
    unknown = FakeRuntimeUnit(UNIT_ID, incarnation)

    for operation, point, count in (
        ("", "before", 1),
        ("create", "bad", 1),
        ("create", "before", 0),
    ):
        with pytest.raises(ValueError):
            runtime.inject_fault(operation, point=cast(FaultPoint, point), count=count)
    with pytest.raises(FakeRuntimeError):
        runtime.hard_terminate(unknown)
    with pytest.raises(ValueError):
        runtime.seed(UNIT_ID, incarnation, cast(FakeRuntimeState, object()))

    runtime.inject_fault("discover", count=2)
    with pytest.raises(FakeRuntimeError):
        runtime.discover(limit=1)
    with pytest.raises(FakeRuntimeError):
        runtime.discover(limit=1)
    assert runtime.discover(limit=1) == ()


def test_fake_runtime_enforces_lifecycle_and_discovery_limits(
    tmp_path: Path,
) -> None:
    inventory = _inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    intent = _intent(inventory)

    with pytest.raises(FakeRuntimeError):
        runtime.create(_reserve_for_other_attempt(), POLICY)
    with pytest.raises(FakeRuntimeError):
        runtime.create(
            replace(
                intent,
                policy_specification=EvidenceDigest("sha256:" + "e" * 64),
            ),
            POLICY,
        )
    runtime_unit = runtime.create(intent, POLICY)
    with pytest.raises(FakeRuntimeError):
        runtime.create(intent, POLICY)
    with pytest.raises(FakeRuntimeError):
        runtime.confirm_exit(runtime_unit)
    with pytest.raises(FakeRuntimeError):
        runtime.confirm_empty(runtime_unit)
    with pytest.raises(FakeRuntimeError):
        runtime.remove(runtime_unit)
    with pytest.raises(FakeRuntimeError):
        runtime.confirm_removed(runtime_unit, EvidenceDigest("sha256:" + "f" * 64))
    with pytest.raises(FakeRuntimeError):
        runtime.discover(limit=0)

    runtime.hard_terminate(runtime_unit)
    runtime.confirm_exit(runtime_unit)
    empty_evidence = runtime.confirm_empty(runtime_unit)
    runtime.remove(runtime_unit)
    runtime.confirm_removed(runtime_unit, empty_evidence)
    with pytest.raises(FakeRuntimeError):
        runtime.hard_terminate(runtime_unit)

    runtime.forget(runtime_unit.unit_id)
    replacement = runtime.seed(runtime_unit.unit_id, runtime_unit.incarnation)
    with pytest.raises(ValueError):
        runtime.seed(runtime_unit.unit_id, runtime_unit.incarnation)
    second = runtime.seed(
        UNKNOWN_UNIT_ID,
        RuntimeIncarnation(
            UUID("41000000-0000-4000-8000-000000000004"),
            policy_specification_evidence(POLICY),
        ),
    )
    with pytest.raises(FakeRuntimeError):
        runtime.discover(limit=1)
    assert replacement.unit_id != second.unit_id


def _reserve_for_other_attempt() -> ManagedUnit:
    return ManagedUnit(
        UUID("21000000-0000-4000-8000-000000000099"),
        UUID("31000000-0000-4000-8000-000000000099"),
        PRINCIPAL,
        2,
        POLICY.revision,
        policy_specification_evidence(POLICY),
        ManagedUnitState.RESERVED,
        0,
    )
