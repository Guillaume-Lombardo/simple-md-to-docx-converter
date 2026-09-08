"""Persistent broker integration for the optional Kubernetes runtime contract.

This suite intentionally uses a deterministic Kubernetes/CRI adapter. The required
real-cluster suite is a separate acceptance gate and this test is not evidence for it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker import kubernetes_attester_transport as attester_transport
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_attester_inventory import SQLiteNodeAttesterLedger
from markweave.broker.kubernetes_attester_transport import (
    AttesterReadinessPolicy,
    HttpsNodeAttesterClient,
    NodeAttesterService,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesIsolationRuntime,
    KubernetesPodIdentity,
)
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.broker.service import IsolationBrokerService
from tests.unit.broker.test_kubernetes_runtime import (
    CONFIG,
    ControlPlaneDouble,
    InspectorDouble,
    _runtime,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

PRINCIPAL = AuthenticatedPrincipal(UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))
ATTEMPT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
UNIT = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
POLICY = BrokerPolicy(
    "t74-integration",
    f"sha256:{'d' * 64}",
    RuntimeLimits(100_000, 100_000, 268_435_456, 31, 16_777_216, 30_000),
    RuntimeChannelLimits(1_000_000, 2_000_000),
)
INVENTORY_KEY = b"k" * 32


class _LocalAttesterClient(HttpsNodeAttesterClient):
    def __init__(self, service: NodeAttesterService) -> None:
        self._service = service
        self._readiness = AttesterReadinessPolicy(1, 1)
        self._monotonic = lambda: 0.0
        self._sleep = lambda _: None
        self._bound = {}

    def _exchange(
        self, node_name: str, request: Mapping[str, object]
    ) -> Mapping[str, object]:
        assert node_name == "reverse-node-1"
        response = self._service.handle(attester_transport._encode(request))
        return attester_transport._decode(response)


class _LostCreateReplyControl(ControlPlaneDouble):
    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity:
        super().create(manifest)
        raise RuntimeError("injected lost Kubernetes create reply")


def _intent() -> ManagedUnit:
    return ManagedUnit(
        ATTEMPT,
        UNIT,
        PRINCIPAL,
        1,
        POLICY.revision,
        policy_specification_evidence(POLICY),
        ManagedUnitState.CREATE_INTENT,
        1,
    )


@pytest.mark.integration
def test_sqlite_inventory_preserves_kubernetes_proof_across_restart(
    tmp_path: Path,
) -> None:
    runtime, control, inspector = _runtime(_intent(), POLICY)
    inventory_path = tmp_path / "kubernetes-broker.sqlite3"
    inventory = SQLiteBrokerInventory(inventory_path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    created = broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    assert created.state is ManagedUnitState.CREATED

    restarted_runtime, _, _ = _runtime(
        _intent(), POLICY, control=control, inspector=inspector
    )
    restarted_inventory = SQLiteBrokerInventory(
        inventory_path, INVENTORY_KEY, max_records=4
    )
    restarted = IsolationBrokerService(
        restarted_inventory,
        restarted_runtime,
        POLICY,
        max_discovered_units=4,
    )
    restarted.start()
    proof = restarted.terminate(PRINCIPAL, ATTEMPT, UNIT)

    retained = restarted_inventory.get(UNIT)
    assert retained is not None
    assert retained.state is ManagedUnitState.REMOVED
    assert retained.removal_evidence == proof.removal_evidence
    assert restarted.acknowledge(PRINCIPAL, ATTEMPT, UNIT, proof.proof_id)
    assert restarted_inventory.get(UNIT) is None
    assert control.present is False


@pytest.mark.integration
@pytest.mark.parametrize("substitution", ["attempt", "principal"])
def test_restart_rejects_discovered_identity_not_bound_to_inventory(
    tmp_path: Path, substitution: str
) -> None:
    runtime, control, inspector = _runtime(_intent(), POLICY)
    inventory_path = tmp_path / f"kubernetes-{substitution}.sqlite3"
    inventory = SQLiteBrokerInventory(inventory_path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    assert control.manifest is not None
    metadata = control.manifest["metadata"]
    assert isinstance(metadata, dict)
    labels = metadata["labels"]
    assert isinstance(labels, dict)
    if substitution == "attempt":
        replacement = replace(_intent(), attempt_id=UUID(int=91))
        labels["reverse.markweave.dev/attempt-id"] = str(replacement.attempt_id)
    else:
        principal = AuthenticatedPrincipal(UUID(int=92))
        replacement = replace(_intent(), principal=principal)
        labels["reverse.markweave.dev/principal-id"] = str(principal.principal_id)
    control.unit = replacement

    restarted_runtime, _, _ = _runtime(
        replacement, POLICY, control=control, inspector=inspector
    )
    restarted = IsolationBrokerService(
        SQLiteBrokerInventory(inventory_path, INVENTORY_KEY, max_records=4),
        restarted_runtime,
        POLICY,
        max_discovered_units=4,
    )

    with pytest.raises(BrokerError) as raised:
        restarted.start()
    assert raised.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE


@pytest.mark.integration
def test_restart_uses_creation_policy_binding_after_policy_rollover(
    tmp_path: Path,
) -> None:
    runtime, control, inspector = _runtime(_intent(), POLICY)
    path = tmp_path / "kubernetes-policy-rollover.sqlite3"
    inventory = SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4)
    first = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    first.start()
    created = first.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    assert created.runtime_recovery is not None

    rolled = replace(POLICY, revision="t74-integration-v2")
    restarted_runtime, _, _ = _runtime(
        replace(_intent(), policy_revision=rolled.revision),
        rolled,
        control=control,
        inspector=inspector,
    )
    restarted = IsolationBrokerService(
        SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4),
        restarted_runtime,
        rolled,
        max_discovered_units=4,
    )
    restarted.start()

    proof = restarted.proof(PRINCIPAL, ATTEMPT, UNIT)
    assert proof is not None
    assert proof.policy_revision == POLICY.revision


@pytest.mark.integration
@pytest.mark.parametrize(
    "crash_point", ["before_api", "lost_create_reply", "before_bind"]
)
def test_prepared_create_recovers_exact_pod_across_policy_rollover(
    tmp_path: Path, crash_point: str
) -> None:
    intent = _intent()
    control = (
        _LostCreateReplyControl(intent)
        if crash_point == "lost_create_reply"
        else ControlPlaneDouble(intent)
    )
    inspector = InspectorDouble(control, POLICY)
    if crash_point == "before_api":
        control.fail_operation = "create"
    elif crash_point == "before_bind":
        inspector.fail_operation = "sandbox"
    runtime = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=POLICY,
        config=CONFIG,
        control_plane=control,
        node_attester=NodeAttestationEngine(inspector),
    )
    path = tmp_path / f"prepared-{crash_point}.sqlite3"
    inventory = SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    with pytest.raises(BrokerError):
        broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    prepared = inventory.get(UNIT)
    assert prepared is not None
    assert prepared.state is ManagedUnitState.CREATE_INTENT
    assert prepared.runtime_recovery is not None
    assert control.present is (crash_point != "before_api")

    inspector.fail_operation = None
    control.fail_operation = None
    rolled = replace(POLICY, revision="t74-after-lost-create")
    restarted_runtime = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=rolled,
        config=CONFIG,
        control_plane=control,
        node_attester=NodeAttestationEngine(inspector),
    )
    restarted = IsolationBrokerService(
        SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4),
        restarted_runtime,
        rolled,
        max_discovered_units=4,
    )

    restarted.start()

    proof = restarted.proof(PRINCIPAL, ATTEMPT, UNIT)
    assert proof is not None
    assert proof.policy_revision == POLICY.revision


@pytest.mark.integration
def test_startup_finishes_post_ack_runtime_cleanup_after_broker_crash(
    tmp_path: Path,
) -> None:
    runtime, control, inspector = _runtime(_intent(), POLICY)
    path = tmp_path / "kubernetes-ack-recovery.sqlite3"
    inventory = SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    proof = broker.terminate(PRINCIPAL, ATTEMPT, UNIT)
    marked = inventory.mark_acknowledged(
        PRINCIPAL.principal_id, ATTEMPT, UNIT, proof.proof_id
    )
    assert marked is not None and marked.proof_acknowledged

    restarted_runtime, _, _ = _runtime(
        _intent(), POLICY, control=control, inspector=inspector
    )
    restarted_inventory = SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4)
    restarted = IsolationBrokerService(
        restarted_inventory,
        restarted_runtime,
        POLICY,
        max_discovered_units=4,
    )
    restarted.start()
    assert restarted_inventory.get(UNIT) is None
    assert restarted.ready


@pytest.mark.integration
def test_failed_post_ack_cleanup_keeps_durable_marker_for_startup_retry(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    runtime, _, _ = _runtime(_intent(), POLICY)
    path = tmp_path / "kubernetes-ack-retry.sqlite3"
    inventory = SQLiteBrokerInventory(path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    proof = broker.terminate(PRINCIPAL, ATTEMPT, UNIT)
    failure = mocker.patch.object(
        runtime,
        "acknowledge_recovery",
        side_effect=RuntimeError("injected post-ack cleanup failure"),
    )

    with pytest.raises(BrokerError) as raised:
        broker.acknowledge(PRINCIPAL, ATTEMPT, UNIT, proof.proof_id)

    assert raised.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    retained = inventory.get(UNIT)
    assert retained is not None and retained.proof_acknowledged
    assert not broker.ready
    mocker.stop(failure)
    broker.start()
    assert broker.ready
    assert inventory.get(UNIT) is None


@pytest.mark.integration
def test_restart_replays_attester_removed_after_crash_before_broker_tombstone(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    intent = _intent()
    control = ControlPlaneDouble(intent)
    inspector = InspectorDouble(control, POLICY)
    ledger_path = tmp_path / "node-attester.sqlite3"
    node_service = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(ledger_path, b"a" * 32, max_records=4),
        node_name="reverse-node-1",
    )
    runtime = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=POLICY,
        config=CONFIG,
        control_plane=control,
        node_attester=_LocalAttesterClient(node_service),
    )
    inventory_path = tmp_path / "broker.sqlite3"
    inventory = SQLiteBrokerInventory(inventory_path, INVENTORY_KEY, max_records=4)
    broker = IsolationBrokerService(
        inventory,
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    broker.start()
    broker.create(ReplayPosition(PRINCIPAL, 1), ATTEMPT)
    failure = mocker.patch.object(
        inventory,
        "mark_removed",
        side_effect=RuntimeError("injected crash before broker tombstone"),
    )

    with pytest.raises(BrokerError):
        broker.terminate(PRINCIPAL, ATTEMPT, UNIT)
    mocker.stop(failure)
    stranded = inventory.get(UNIT)
    assert stranded is not None
    assert stranded.state is ManagedUnitState.EMPTY_CONFIRMED

    restarted_service = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(ledger_path, b"a" * 32, max_records=4),
        node_name="reverse-node-1",
    )
    restarted_runtime = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=POLICY,
        config=CONFIG,
        control_plane=control,
        node_attester=_LocalAttesterClient(restarted_service),
    )
    restarted = IsolationBrokerService(
        SQLiteBrokerInventory(inventory_path, INVENTORY_KEY, max_records=4),
        restarted_runtime,
        POLICY,
        max_discovered_units=4,
    )

    restarted.start()

    proof = restarted.proof(PRINCIPAL, ATTEMPT, UNIT)
    assert proof is not None
