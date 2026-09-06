from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    ReplayPosition,
    RuntimeIncarnation,
    TerminationProof,
)
from markweave.broker.reconciliation_protocol import (
    PROTOCOL_NAME,
    ReconciliationErrorResponse,
    ReconciliationRequest,
    ReconciliationResponse,
    ReconciliationTombstone,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)

pytestmark = pytest.mark.unit

REQUEST = UUID("10000000-0000-4000-8000-000000000001")
PRINCIPAL = AuthenticatedPrincipal(UUID("20000000-0000-4000-8000-000000000001"))
OTHER = AuthenticatedPrincipal(UUID("20000000-0000-4000-8000-000000000002"))
ATTEMPT = UUID("30000000-0000-4000-8000-000000000001")
UNIT = UUID("40000000-0000-4000-8000-000000000001")
INCARNATION = RuntimeIncarnation(
    UUID("50000000-0000-4000-8000-000000000001"),
    EvidenceDigest("sha256:" + "4" * 64),
)
PROOF = TerminationProof(
    UUID("60000000-0000-4000-8000-000000000001"),
    ATTEMPT,
    UNIT,
    PRINCIPAL,
    "policy-v1",
    EvidenceDigest("sha256:" + "1" * 64),
    EvidenceDigest("sha256:" + "2" * 64),
    EvidenceDigest("sha256:" + "3" * 64),
)
TOMBSTONE = ReconciliationTombstone(2, INCARNATION.specification, PROOF)


def test_protocol_round_trips_closed_request_success_and_error() -> None:
    request = ReconciliationRequest(REQUEST, 1)
    response = ReconciliationResponse(
        REQUEST, PRINCIPAL.principal_id, 1, 2, TOMBSTONE, False
    )
    error = ReconciliationErrorResponse(
        REQUEST, BrokerErrorCategory.RECONCILIATION_INCOMPLETE
    )

    assert decode_request(encode_request(request)) == request
    assert decode_response(encode_response(response)) == response
    assert decode_response(encode_response(error)) == error
    assert PROTOCOL_NAME not in {
        "markweave-reverse-broker",
        "markweave-reverse-broker-workspace",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        {"protocol": "markweave-reverse-broker"},
        {"version": 2},
        {"after_create_sequence": -1},
        {"principal_id": str(OTHER.principal_id)},
    ],
)
def test_protocol_rejects_substitution_and_invalid_frames(
    mutation: dict[str, object],
) -> None:
    encoded = encode_response(
        ReconciliationResponse(REQUEST, PRINCIPAL.principal_id, 1, 2, TOMBSTONE, False)
    )
    value = json.loads(encoded[4:])
    value.update(mutation)
    payload = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    with pytest.raises(BrokerError) as captured:
        decode_response(len(payload).to_bytes(4, "big") + payload)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_response_model_binds_cursor_principal_and_high_water() -> None:
    with pytest.raises(ValueError):
        ReconciliationResponse(REQUEST, OTHER.principal_id, 1, 2, TOMBSTONE, False)
    with pytest.raises(ValueError):
        ReconciliationResponse(REQUEST, PRINCIPAL.principal_id, 2, 2, TOMBSTONE, False)
    with pytest.raises(ValueError):
        ReconciliationResponse(REQUEST, PRINCIPAL.principal_id, 1, 1, TOMBSTONE, False)


def test_inventory_pages_only_exact_principal_tombstones(tmp_path: Path) -> None:
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", bytes(range(32)), max_records=8
    )
    unit = ManagedUnit(
        ATTEMPT,
        UNIT,
        PRINCIPAL,
        2,
        "policy-v1",
        INCARNATION.specification,
        ManagedUnitState.RESERVED,
        0,
    )
    inventory.reserve(unit, ReplayPosition(PRINCIPAL, 2))
    inventory.transition(
        UNIT, expected_revision=0, target=ManagedUnitState.CREATE_INTENT
    )
    inventory.transition(
        UNIT,
        expected_revision=1,
        target=ManagedUnitState.CREATED,
        runtime_incarnation=INCARNATION,
    )
    inventory.transition(
        UNIT,
        expected_revision=2,
        target=ManagedUnitState.EXIT_CONFIRMED,
        evidence=PROOF.exit_evidence,
    )
    inventory.transition(
        UNIT,
        expected_revision=3,
        target=ManagedUnitState.EMPTY_CONFIRMED,
        evidence=PROOF.empty_evidence,
    )
    inventory.mark_removed(
        UNIT, expected_revision=4, removal_evidence=PROOF.removal_evidence, proof=PROOF
    )

    assert inventory.reconciliation_page(PRINCIPAL.principal_id, 0) == (2, TOMBSTONE)
    assert inventory.reconciliation_page(PRINCIPAL.principal_id, 2) == (2, None)
    assert inventory.reconciliation_page(OTHER.principal_id, 0) == (0, None)


@pytest.mark.parametrize("invalid", [-1, True, 1.5, "1"])
def test_inventory_rejects_invalid_cursor(tmp_path: Path, invalid: object) -> None:
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", bytes(range(32)), max_records=8
    )
    with pytest.raises(BrokerError):
        inventory.reconciliation_page(PRINCIPAL.principal_id, cast(Any, invalid))
