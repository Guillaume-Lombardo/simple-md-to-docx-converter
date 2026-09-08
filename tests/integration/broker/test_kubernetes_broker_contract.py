"""Persistent broker integration for the optional Kubernetes runtime contract.

This suite intentionally uses a deterministic Kubernetes/CRI adapter. The required
real-cluster suite is a separate acceptance gate and this test is not evidence for it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.inventory import SQLiteBrokerInventory
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
from tests.unit.broker.test_kubernetes_runtime import _runtime

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
