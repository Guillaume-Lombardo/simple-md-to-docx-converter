from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.broker.protocol import (
    AcknowledgeResponse,
    BrokerOperation,
    ErrorResponse,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationErrorResponse,
    ReconciliationResponse,
    ReconciliationTombstone,
)
from markweave.reversion_jobs.errors import ReversionJobConflictError
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 6, tzinfo=UTC)
PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000001"))
TOKEN = UUID("20000000-0000-4000-8000-000000000001")
REQUEST_IDS = iter(
    UUID(f"30000000-0000-4000-8000-{value:012d}") for value in range(1, 8)
)
PROOF = TerminationProof(
    UUID("40000000-0000-4000-8000-000000000001"),
    UUID("50000000-0000-4000-8000-000000000001"),
    UUID("60000000-0000-4000-8000-000000000001"),
    PRINCIPAL,
    "policy-v1",
    EvidenceDigest("sha256:" + "1" * 64),
    EvidenceDigest("sha256:" + "2" * 64),
    EvidenceDigest("sha256:" + "3" * 64),
)
TOMBSTONE = ReconciliationTombstone(4, EvidenceDigest("sha256:" + "4" * 64), PROOF)


def test_reconciler_drains_ack_then_requires_stable_fixed_point(
    mocker: MockerFixture,
) -> None:
    store = mocker.Mock()
    broker = mocker.Mock()
    store.begin_reconciliation.return_value = 4
    store.pending_reconciliation_acknowledgements.side_effect = ((TOMBSTONE,), ())
    store.record_reconciliation_page.side_effect = (None, None)
    store.complete_reconciliation.side_effect = (
        ReversionJobConflictError(),
        None,
    )
    broker.request.return_value = AcknowledgeResponse(
        UUID("30000000-0000-4000-8000-000000000001"),
        PROOF.attempt_id,
        PROOF.unit_id,
        PROOF.proof_id,
        True,
    )
    broker.reconcile.side_effect = (
        ReconciliationResponse(
            UUID("30000000-0000-4000-8000-000000000002"),
            PRINCIPAL.principal_id,
            4,
            7,
            None,
            True,
        ),
        ReconciliationResponse(
            UUID("30000000-0000-4000-8000-000000000003"),
            PRINCIPAL.principal_id,
            4,
            7,
            None,
            True,
        ),
    )
    clock = mocker.Mock(return_value=NOW)
    reconciler = ReversionBrokerReconciler(
        store, broker, ack_batch_limit=2, request_id_factory=lambda: next(REQUEST_IDS)
    )

    reconciler.reconcile(
        PRINCIPAL,
        "reconciler",
        TOKEN,
        NOW,
        NOW + timedelta(minutes=1),
        now_factory=clock,
    )

    store.mark_reconciliation_acknowledged.assert_called_once_with(
        PRINCIPAL, TOKEN, TOMBSTONE, NOW
    )
    assert broker.reconcile.call_count == 2
    assert store.complete_reconciliation.call_count == 2
    store.complete_reconciliation.assert_called_with(PRINCIPAL, TOKEN, NOW)


def test_reconcile_step_persists_one_page_and_defers_its_ack(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    store = mocker.Mock()
    broker = mocker.Mock()
    store.begin_reconciliation.return_value = 0
    store.pending_reconciliation_acknowledgements.side_effect = ((), (TOMBSTONE,))
    store.record_reconciliation_page.side_effect = lambda *_args: (
        events.append("persist") or TOMBSTONE
    )
    broker.reconcile.side_effect = lambda request: ReconciliationResponse(
        request.request_id, PRINCIPAL.principal_id, 0, 4, TOMBSTONE, False
    )
    broker.request.side_effect = lambda request: (
        events.append("ack")
        or AcknowledgeResponse(
            request.request_id, PROOF.attempt_id, PROOF.unit_id, PROOF.proof_id, True
        )
    )
    store.complete_reconciliation.side_effect = ReversionJobConflictError()
    reconciler = ReversionBrokerReconciler(
        store, broker, ack_batch_limit=2, request_id_factory=lambda: next(REQUEST_IDS)
    )

    assert not reconciler.reconcile_step(
        PRINCIPAL,
        "reconciler",
        TOKEN,
        NOW,
        NOW + timedelta(minutes=1),
        now_factory=lambda: NOW,
    )
    assert events == ["persist"]
    assert not reconciler.reconcile_step(
        PRINCIPAL,
        "reconciler",
        TOKEN,
        NOW,
        NOW + timedelta(minutes=1),
        now_factory=lambda: NOW,
    )
    assert events[:2] == ["persist", "ack"]


@pytest.mark.parametrize(
    ("limit", "factory"),
    [
        (0, lambda: REQUEST_IDS),
        (True, lambda: REQUEST_IDS),
        (1, None),
    ],
)
def test_reconciler_rejects_invalid_configuration(
    mocker: MockerFixture, limit: object, factory: object
) -> None:
    with pytest.raises(ValueError):
        ReversionBrokerReconciler(
            mocker.Mock(),
            mocker.Mock(),
            ack_batch_limit=cast(Any, limit),
            request_id_factory=cast(Any, factory),
        )


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            ReconciliationErrorResponse(
                UUID(int=1), BrokerErrorCategory.INVENTORY_FAILURE
            ),
            BrokerError,
        ),
        (
            ReconciliationErrorResponse(
                UUID(int=9), BrokerErrorCategory.INVENTORY_FAILURE
            ),
            BrokerError,
        ),
        (
            ReconciliationResponse(
                UUID(int=9), PRINCIPAL.principal_id, 0, 0, None, True
            ),
            BrokerError,
        ),
        (object(), BrokerError),
    ],
)
def test_reconciler_rejects_broker_page_failures(
    mocker: MockerFixture, response: object, error_type: type[Exception]
) -> None:
    store = mocker.Mock()
    store.begin_reconciliation.return_value = 0
    store.pending_reconciliation_acknowledgements.return_value = ()
    broker = mocker.Mock()
    broker.reconcile.return_value = response
    reconciler = ReversionBrokerReconciler(
        store, broker, ack_batch_limit=1, request_id_factory=lambda: UUID(int=1)
    )

    def reconcile() -> None:
        reconciler.reconcile(
            PRINCIPAL,
            "reconciler",
            TOKEN,
            NOW,
            NOW + timedelta(minutes=1),
            now_factory=lambda: NOW,
        )

    if error_type is BrokerError:
        with pytest.raises(BrokerError) as caught:
            reconcile()
        expected = (
            BrokerErrorCategory.INVENTORY_FAILURE
            if isinstance(response, ReconciliationErrorResponse)
            and response.request_id == UUID(int=1)
            else BrokerErrorCategory.PROTOCOL_ERROR
        )
        assert caught.value.category is expected
    else:
        with pytest.raises(error_type):
            reconcile()
    store.record_reconciliation_page.assert_not_called()
    store.complete_reconciliation.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        ErrorResponse(
            UUID(int=1), BrokerOperation.ACK, BrokerErrorCategory.INVENTORY_FAILURE
        ),
        ErrorResponse(
            UUID(int=9), BrokerOperation.ACK, BrokerErrorCategory.INVENTORY_FAILURE
        ),
        ErrorResponse(
            UUID(int=1), BrokerOperation.CREATE, BrokerErrorCategory.INVENTORY_FAILURE
        ),
        object(),
        AcknowledgeResponse(
            UUID(int=9), PROOF.attempt_id, PROOF.unit_id, PROOF.proof_id, True
        ),
        AcknowledgeResponse(
            UUID(int=1), PROOF.attempt_id, PROOF.unit_id, PROOF.proof_id, False
        ),
        AcknowledgeResponse(
            UUID(int=1), UUID(int=2), PROOF.unit_id, PROOF.proof_id, True
        ),
        AcknowledgeResponse(
            UUID(int=1), PROOF.attempt_id, UUID(int=2), PROOF.proof_id, True
        ),
        AcknowledgeResponse(
            UUID(int=1), PROOF.attempt_id, PROOF.unit_id, UUID(int=2), True
        ),
    ],
)
def test_reconciler_rejects_invalid_acknowledgements(
    mocker: MockerFixture, response: object
) -> None:
    store = mocker.Mock()
    broker = mocker.Mock()
    broker.request.return_value = response
    reconciler = ReversionBrokerReconciler(
        store, broker, ack_batch_limit=1, request_id_factory=lambda: UUID(int=1)
    )
    if (
        isinstance(response, ErrorResponse)
        and response.request_id == UUID(int=1)
        and response.operation is BrokerOperation.ACK
    ):
        with pytest.raises(BrokerError) as caught:
            reconciler._ack(PRINCIPAL, TOKEN, TOMBSTONE, NOW)
        assert caught.value.category is BrokerErrorCategory.INVENTORY_FAILURE
    else:
        with pytest.raises(BrokerError) as caught:
            reconciler._ack(PRINCIPAL, TOKEN, TOMBSTONE, NOW)
        assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR
    store.mark_reconciliation_acknowledged.assert_not_called()
