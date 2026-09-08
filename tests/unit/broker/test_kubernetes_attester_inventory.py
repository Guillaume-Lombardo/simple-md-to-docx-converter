"""Durability and tamper tests for the node-attester lifecycle ledger."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

import pytest

from markweave.broker.kubernetes_attester_inventory import (
    AttesterLifecycleRecord,
    AttesterLifecycleState,
    SQLiteNodeAttesterLedger,
)
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError
from markweave.broker.models import EvidenceDigest

KEY = b"node-attester-ledger-key-material" * 2
POD = UUID("11111111-1111-4111-8111-111111111111")
OTHER_POD = UUID("22222222-2222-4222-8222-222222222222")
EXIT = EvidenceDigest(f"sha256:{'1' * 64}")
EMPTY = EvidenceDigest(f"sha256:{'2' * 64}")
REMOVED = EvidenceDigest(f"sha256:{'3' * 64}")


def _record(pod_uid: UUID = POD) -> AttesterLifecycleRecord:
    return AttesterLifecycleRecord(
        pod_uid,
        AttesterLifecycleState.BOUND,
        b'{"contract":"content-free"}',
        b'{"sandbox":"content-free"}',
    )


@pytest.mark.unit
def test_ledger_persists_every_transition_before_exact_replay(tmp_path: Path) -> None:
    path = tmp_path / "attester.sqlite3"
    ledger = SQLiteNodeAttesterLedger(path, KEY, max_records=2)
    bound = ledger.reserve(_record())
    exited = ledger.transition(
        POD,
        expected_revision=bound.revision,
        target=AttesterLifecycleState.EXIT,
        evidence=EXIT,
    )
    empty = ledger.transition(
        POD,
        expected_revision=exited.revision,
        target=AttesterLifecycleState.EMPTY,
        evidence=EMPTY,
    )
    removed = ledger.transition(
        POD,
        expected_revision=empty.revision,
        target=AttesterLifecycleState.REMOVED,
        evidence=REMOVED,
    )

    restarted = SQLiteNodeAttesterLedger(path, KEY, max_records=2)
    assert restarted.get(POD) == removed
    with pytest.raises(KubernetesRuntimeError, match="binding conflicts"):
        restarted.reserve(_record())
    assert restarted.discard_removed(POD, REMOVED)
    assert restarted.discard_removed(POD, REMOVED)
    assert restarted.records() == ()


@pytest.mark.unit
def test_ledger_fails_closed_at_capacity_without_eviction(tmp_path: Path) -> None:
    ledger = SQLiteNodeAttesterLedger(tmp_path / "attester.sqlite3", KEY, max_records=1)
    ledger.reserve(_record())
    with pytest.raises(KubernetesRuntimeError, match="ledger is full"):
        ledger.reserve(_record(OTHER_POD))
    assert ledger.records() == (_record(),)


@pytest.mark.unit
@pytest.mark.parametrize("target", ["row", "manifest", "deletion"])
def test_ledger_rejects_authenticated_row_or_manifest_tamper(
    tmp_path: Path, target: str
) -> None:
    path = tmp_path / "attester.sqlite3"
    SQLiteNodeAttesterLedger(path, KEY, max_records=2).reserve(_record())
    with closing(sqlite3.connect(path)) as connection, connection:
        if target == "row":
            connection.execute(
                "UPDATE lifecycle SET sandbox_payload = ? WHERE pod_uid = ?",
                (b'{"sandbox":"substituted"}', str(POD)),
            )
        elif target == "manifest":
            connection.execute(
                "UPDATE lifecycle_manifest SET record_count = 0 WHERE singleton_id = 1"
            )
        else:
            connection.execute("DELETE FROM lifecycle WHERE pod_uid = ?", (str(POD),))
    with pytest.raises(KubernetesRuntimeError, match="ledger failed"):
        SQLiteNodeAttesterLedger(path, KEY, max_records=2)


@pytest.mark.unit
def test_ledger_rejects_wrong_authentication_key(tmp_path: Path) -> None:
    path = tmp_path / "attester.sqlite3"
    SQLiteNodeAttesterLedger(path, KEY, max_records=2).reserve(_record())

    with pytest.raises(KubernetesRuntimeError, match="ledger failed"):
        SQLiteNodeAttesterLedger(path, b"x" * 32, max_records=2)
