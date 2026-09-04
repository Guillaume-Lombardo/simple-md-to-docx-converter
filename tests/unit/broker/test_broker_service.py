"""Unit coverage for broker admission and the common termination lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from typing import cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.fake_runtime import FakeIsolationRuntime
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnitState,
    ReplayPosition,
    RuntimeLimits,
)
from markweave.broker.service import IsolationBrokerService

pytestmark = pytest.mark.unit

PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000001"))
OTHER_PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000002"))
ATTEMPT_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_ATTEMPT_ID = UUID("20000000-0000-4000-8000-000000000002")
UNIT_IDS = (
    UUID("30000000-0000-4000-8000-000000000001"),
    UUID("30000000-0000-4000-8000-000000000002"),
    UUID("30000000-0000-4000-8000-000000000003"),
)
POLICY = BrokerPolicy(
    "t71-v1",
    "sha256:" + "a" * 64,
    RuntimeLimits(100_000, 100_000, 512_000_000, 32, 16_000_000, 30_000),
)


def inventory(tmp_path: Path) -> SQLiteBrokerInventory:
    return SQLiteBrokerInventory(
        tmp_path / "broker.sqlite3", b"inventory-authentication-key-32b", max_records=32
    )


def service(
    tmp_path: Path,
    runtime: FakeIsolationRuntime | None = None,
) -> tuple[IsolationBrokerService, SQLiteBrokerInventory, FakeIsolationRuntime]:
    broker_inventory = inventory(tmp_path)
    fake_runtime = runtime or FakeIsolationRuntime()
    identities = iter(UNIT_IDS)
    broker = IsolationBrokerService(
        broker_inventory,
        fake_runtime,
        POLICY,
        max_discovered_units=32,
        unit_id_factory=lambda: next(identities),
    )
    return broker, broker_inventory, fake_runtime


def test_admission_stays_closed_until_complete_start(tmp_path) -> None:
    broker, broker_inventory, runtime = service(tmp_path)

    assert not broker.ready
    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert caught.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    assert broker_inventory.find_attempt(PRINCIPAL.principal_id, ATTEMPT_ID) is None
    assert runtime.calls == ()

    broker.start()

    assert broker.ready
    assert runtime.calls == ("discover:before", "discover:after")


def test_create_persists_intent_before_runtime_and_is_exactly_idempotent(
    tmp_path,
) -> None:
    broker, broker_inventory, runtime = service(tmp_path)
    broker.start()

    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    assert created.state is ManagedUnitState.CREATED
    assert created.revision == 2
    assert created.runtime_incarnation is not None
    assert broker_inventory.get(created.unit_id) == created
    assert runtime.calls[-2:] == ("create:before", "create:after")

    replayed = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert replayed == created
    assert runtime.calls.count("create:before") == 1

    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 1), OTHER_ATTEMPT_ID)
    assert caught.value.category is BrokerErrorCategory.REPLAY_REJECTED
    assert broker_inventory.create_sequence_high_watermark(PRINCIPAL.principal_id) == 1


def test_termination_returns_only_the_complete_retained_proof(tmp_path) -> None:
    broker, broker_inventory, runtime = service(tmp_path)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    assert broker.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id) is None
    proof = broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert runtime.calls[-12:] == (
        "discover:before",
        "discover:after",
        "hard_terminate:before",
        "hard_terminate:after",
        "confirm_exit:before",
        "confirm_exit:after",
        "confirm_empty:before",
        "confirm_empty:after",
        "remove:before",
        "remove:after",
        "confirm_removed:before",
        "confirm_removed:after",
    )
    removed = broker.status(PRINCIPAL, ATTEMPT_ID, created.unit_id)
    assert removed.state is ManagedUnitState.REMOVED
    assert removed.exit_evidence == proof.exit_evidence
    assert removed.empty_evidence == proof.empty_evidence
    assert removed.removal_evidence == proof.removal_evidence
    assert broker_inventory.get_proof(created.unit_id) == proof
    assert broker.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id) == proof
    assert broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id) == proof

    assert broker.acknowledge(PRINCIPAL, ATTEMPT_ID, created.unit_id, proof.proof_id)
    assert broker.acknowledge(PRINCIPAL, ATTEMPT_ID, created.unit_id, proof.proof_id)
    assert broker_inventory.get(created.unit_id) is None
    assert broker_inventory.create_sequence_high_watermark(PRINCIPAL.principal_id) == 1


def test_wrong_principal_cannot_observe_or_mutate_a_unit(tmp_path) -> None:
    broker, _, _ = service(tmp_path)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    for operation in (broker.status, broker.terminate, broker.proof):
        with pytest.raises(BrokerError) as caught:
            operation(OTHER_PRINCIPAL, ATTEMPT_ID, created.unit_id)
        assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_runtime_fault_clears_readiness_and_never_returns_partial_proof(
    tmp_path,
) -> None:
    broker, broker_inventory, runtime = service(tmp_path)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    runtime.inject_fault("confirm_empty", point="after")

    with pytest.raises(BrokerError) as caught:
        broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)

    assert caught.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
    assert not broker.ready
    interrupted = broker_inventory.get(created.unit_id)
    assert interrupted is not None
    assert interrupted.state is ManagedUnitState.EXIT_CONFIRMED
    assert broker_inventory.get_proof(created.unit_id) is None
    with pytest.raises(BrokerError, match="reconciliation is incomplete"):
        broker.status(PRINCIPAL, ATTEMPT_ID, created.unit_id)


def test_ambiguous_inventory_failure_after_create_intent_clears_readiness(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    broker, broker_inventory, runtime = service(tmp_path)
    broker.start()
    transition = broker_inventory.transition

    def commit_then_fail(*args, **kwargs):
        result = transition(*args, **kwargs)
        if kwargs["target"] is ManagedUnitState.CREATE_INTENT:
            raise BrokerError(BrokerErrorCategory.INVENTORY_FAILURE)
        return result

    mocker.patch.object(broker_inventory, "transition", side_effect=commit_then_fail)

    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    assert not broker.ready
    retained = broker_inventory.find_attempt(PRINCIPAL.principal_id, ATTEMPT_ID)
    assert retained is not None
    assert retained.state is ManagedUnitState.CREATE_INTENT
    assert "create:before" not in runtime.calls


def test_constructor_rejects_invalid_dependencies(tmp_path: Path) -> None:
    broker_inventory = inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    invalid_values = (
        (
            cast(BrokerPolicy, object()),
            32,
            cast(Callable[[], UUID], lambda: UNIT_IDS[0]),
        ),
        (POLICY, 0, cast(Callable[[], UUID], lambda: UNIT_IDS[0])),
        (POLICY, 32, cast(Callable[[], UUID], None)),
    )
    for policy, limit, factory in invalid_values:
        with pytest.raises(ValueError):
            IsolationBrokerService(
                broker_inventory,
                runtime,
                policy,
                max_discovered_units=limit,
                unit_id_factory=factory,
            )


def test_runtime_reconnection_repeats_reconciliation(tmp_path: Path) -> None:
    broker, _, runtime = service(tmp_path)
    broker.start()

    broker.runtime_reconnected()

    assert broker.ready
    assert runtime.calls == (
        "discover:before",
        "discover:after",
        "discover:before",
        "discover:after",
    )


def test_create_rejects_invalid_or_failing_identity_factory(tmp_path: Path) -> None:
    broker_inventory = inventory(tmp_path)
    runtime = FakeIsolationRuntime()
    invalid = IsolationBrokerService(
        broker_inventory,
        runtime,
        POLICY,
        max_discovered_units=32,
        unit_id_factory=cast(Callable[[], UUID], lambda: "not-a-uuid"),
    )
    invalid.start()

    with pytest.raises(BrokerError) as caught:
        invalid.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert caught.value.category is BrokerErrorCategory.RUNTIME_FAILURE

    def fail_identity() -> UUID:
        raise ValueError("content-free test failure")

    failing = IsolationBrokerService(
        broker_inventory,
        runtime,
        POLICY,
        max_discovered_units=32,
        unit_id_factory=fail_identity,
    )
    failing.start()
    with pytest.raises(BrokerError) as caught:
        failing.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert caught.value.category is BrokerErrorCategory.RUNTIME_FAILURE


def test_missing_unit_and_wrong_acknowledgement_are_idempotent(tmp_path: Path) -> None:
    broker, _, _ = service(tmp_path)
    broker.start()

    with pytest.raises(BrokerError) as caught:
        broker.status(PRINCIPAL, ATTEMPT_ID, UNIT_IDS[0])
    assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR
    assert not broker.acknowledge(PRINCIPAL, ATTEMPT_ID, UNIT_IDS[0], UNIT_IDS[1])

    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    assert not broker.acknowledge(PRINCIPAL, ATTEMPT_ID, created.unit_id, UNIT_IDS[1])


def test_protocol_values_are_type_checked(tmp_path: Path) -> None:
    broker, _, _ = service(tmp_path)
    broker.start()

    with pytest.raises(BrokerError) as caught:
        broker.status(PRINCIPAL, ATTEMPT_ID, cast(UUID, "not-a-uuid"))
    assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR


@pytest.mark.parametrize("method", ("status", "terminate", "proof", "acknowledge"))
def test_post_start_inventory_faults_revoke_readiness(
    tmp_path: Path,
    mocker: MockerFixture,
    method: str,
) -> None:
    broker, broker_inventory, _ = service(tmp_path)
    broker.start()
    mocker.patch.object(
        broker_inventory,
        "get",
        side_effect=BrokerError(BrokerErrorCategory.INVENTORY_FAILURE),
    )

    arguments = (PRINCIPAL, ATTEMPT_ID, UNIT_IDS[0])
    operation = getattr(broker, method)
    if method == "acknowledge":
        arguments += (UNIT_IDS[1],)
    with pytest.raises(BrokerError) as caught:
        operation(*arguments)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    assert not broker.ready


def test_reservation_inventory_fault_revokes_readiness(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    broker, broker_inventory, _ = service(tmp_path)
    broker.start()
    mocker.patch.object(
        broker_inventory,
        "reserve",
        side_effect=BrokerError(BrokerErrorCategory.INVENTORY_FAILURE),
    )

    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    assert not broker.ready


@pytest.mark.parametrize(
    ("boundary", "method"), (("get_proof", "proof"), ("acknowledge", "acknowledge"))
)
def test_proof_storage_faults_revoke_readiness(
    tmp_path: Path,
    mocker: MockerFixture,
    boundary: str,
    method: str,
) -> None:
    broker, broker_inventory, _ = service(tmp_path)
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
    proof = broker.terminate(PRINCIPAL, ATTEMPT_ID, created.unit_id)
    mocker.patch.object(
        broker_inventory,
        boundary,
        side_effect=BrokerError(BrokerErrorCategory.INVENTORY_FAILURE),
    )

    with pytest.raises(BrokerError) as caught:
        if method == "proof":
            broker.proof(PRINCIPAL, ATTEMPT_ID, created.unit_id)
        else:
            broker.acknowledge(PRINCIPAL, ATTEMPT_ID, created.unit_id, proof.proof_id)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    assert not broker.ready


def test_caller_replay_and_protocol_errors_do_not_revoke_readiness(
    tmp_path: Path,
) -> None:
    broker, _, _ = service(tmp_path)
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 2), ATTEMPT_ID)

    with pytest.raises(BrokerError) as replay:
        broker.create(ReplayPosition(PRINCIPAL, 1), OTHER_ATTEMPT_ID)
    assert replay.value.category is BrokerErrorCategory.REPLAY_REJECTED
    assert broker.ready

    with pytest.raises(BrokerError) as protocol:
        broker.status(OTHER_PRINCIPAL, ATTEMPT_ID, UNIT_IDS[0])
    assert protocol.value.category is BrokerErrorCategory.PROTOCOL_ERROR
    assert broker.ready


def test_capacity_error_does_not_revoke_readiness(tmp_path: Path) -> None:
    broker_inventory = SQLiteBrokerInventory(
        tmp_path / "bounded.sqlite3",
        b"bounded-inventory-auth-key-32bytes",
        max_records=1,
    )
    runtime = FakeIsolationRuntime()
    identities = iter(UNIT_IDS)
    broker = IsolationBrokerService(
        broker_inventory,
        runtime,
        POLICY,
        max_discovered_units=1,
        unit_id_factory=lambda: next(identities),
    )
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)

    with pytest.raises(BrokerError) as caught:
        broker.create(ReplayPosition(PRINCIPAL, 2), OTHER_ATTEMPT_ID)

    assert caught.value.category is BrokerErrorCategory.INVENTORY_FULL
    assert broker.ready


def test_in_process_gate_prevents_overlapping_create(tmp_path) -> None:
    create_entered = Event()
    release_create = Event()
    second_finished = Event()
    blocking = False

    def observe(event: str) -> None:
        if event == "create:before" and blocking:
            create_entered.set()
            assert release_create.wait(3)

    runtime = FakeIsolationRuntime(observe)
    broker, _, _ = service(tmp_path, runtime)
    broker.start()
    blocking = True
    failures: list[BaseException] = []

    def first() -> None:
        try:
            broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT_ID)
        except BaseException as error:  # pragma: no cover - assertion reports contents
            failures.append(error)

    def second() -> None:
        try:
            broker.create(ReplayPosition(PRINCIPAL, 2), OTHER_ATTEMPT_ID)
            second_finished.set()
        except BaseException as error:  # pragma: no cover - assertion reports contents
            failures.append(error)

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    assert create_entered.wait(3)
    second_thread.start()
    assert not second_finished.wait(0.1)
    release_create.set()
    first_thread.join(3)
    second_thread.join(3)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert failures == []
    assert runtime.calls.count("create:before") == 2
