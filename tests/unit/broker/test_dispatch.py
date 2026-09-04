from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.dispatch import BrokerDispatcher, request_operation
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnitState,
    TerminationProof,
)
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    ProofRequest,
    ProofResponse,
    ReadyRequest,
    ReadyResponse,
    StatusRequest,
    StatusResponse,
    TerminateRequest,
    TerminateResponse,
)
from markweave.broker.service import IsolationBrokerService

pytestmark = pytest.mark.unit

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000002")
UNIT_ID = UUID("00000000-0000-4000-8000-000000000003")
PROOF_ID = UUID("00000000-0000-4000-8000-000000000004")
PRINCIPAL = AuthenticatedPrincipal(UUID("00000000-0000-4000-8000-000000000005"))
DIGEST = EvidenceDigest("sha256:" + "a" * 64)
PROOF = TerminationProof(
    PROOF_ID,
    ATTEMPT_ID,
    UNIT_ID,
    PRINCIPAL,
    "policy-v1",
    DIGEST,
    DIGEST,
    DIGEST,
)


def _dispatcher(mocker: MockerFixture) -> tuple[BrokerDispatcher, Any]:
    service = mocker.Mock(spec=IsolationBrokerService)
    service.ready = True
    return BrokerDispatcher(service), service


@pytest.mark.parametrize(
    ("broker_request", "operation"),
    [
        (CreateRequest(REQUEST_ID, 1, ATTEMPT_ID), BrokerOperation.CREATE),
        (StatusRequest(REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID), BrokerOperation.STATUS),
        (
            TerminateRequest(REQUEST_ID, 3, ATTEMPT_ID, UNIT_ID),
            BrokerOperation.TERMINATE,
        ),
        (ProofRequest(REQUEST_ID, 4, ATTEMPT_ID, UNIT_ID), BrokerOperation.PROOF),
        (
            AcknowledgeRequest(REQUEST_ID, 5, ATTEMPT_ID, UNIT_ID, PROOF_ID),
            BrokerOperation.ACK,
        ),
        (ReadyRequest(REQUEST_ID, 6), BrokerOperation.READY),
    ],
)
def test_request_operation_is_closed(
    broker_request, operation: BrokerOperation
) -> None:
    assert request_operation(broker_request) is operation


def test_dispatches_every_operation_with_authenticated_identity(
    mocker: MockerFixture,
) -> None:
    dispatcher, service = _dispatcher(mocker)
    unit = SimpleNamespace(
        attempt_id=ATTEMPT_ID, unit_id=UNIT_ID, state=ManagedUnitState.CREATED
    )
    service.create.return_value = unit
    service.status.return_value = unit
    service.terminate.return_value = PROOF
    service.proof.return_value = PROOF
    service.acknowledge.return_value = True

    assert dispatcher.dispatch(
        PRINCIPAL, CreateRequest(REQUEST_ID, 1, ATTEMPT_ID)
    ) == CreateResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED)
    assert dispatcher.dispatch(
        PRINCIPAL, StatusRequest(REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID)
    ) == StatusResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED)
    assert dispatcher.dispatch(
        PRINCIPAL, TerminateRequest(REQUEST_ID, 3, ATTEMPT_ID, UNIT_ID)
    ) == TerminateResponse(REQUEST_ID, PROOF)
    assert dispatcher.dispatch(
        PRINCIPAL, ProofRequest(REQUEST_ID, 4, ATTEMPT_ID, UNIT_ID)
    ) == ProofResponse(REQUEST_ID, PROOF)
    assert dispatcher.dispatch(
        PRINCIPAL,
        AcknowledgeRequest(REQUEST_ID, 5, ATTEMPT_ID, UNIT_ID, PROOF_ID),
    ) == AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True)
    assert dispatcher.dispatch(PRINCIPAL, ReadyRequest(REQUEST_ID, 6)) == ReadyResponse(
        REQUEST_ID, True
    )

    replay, attempt_id = service.create.call_args.args
    assert replay.principal is PRINCIPAL
    assert replay.sequence == 1
    assert attempt_id == ATTEMPT_ID
    service.status.assert_called_once_with(PRINCIPAL, ATTEMPT_ID, UNIT_ID)


@pytest.mark.parametrize(
    ("broker_request", "method", "result", "category"),
    [
        (
            CreateRequest(REQUEST_ID, 1, ATTEMPT_ID),
            "create",
            BrokerError(BrokerErrorCategory.INVENTORY_FULL),
            BrokerErrorCategory.INVENTORY_FULL,
        ),
        (
            ProofRequest(REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID),
            "proof",
            None,
            BrokerErrorCategory.TERMINATION_UNPROVEN,
        ),
        (
            AcknowledgeRequest(REQUEST_ID, 3, ATTEMPT_ID, UNIT_ID, PROOF_ID),
            "acknowledge",
            False,
            BrokerErrorCategory.PROTOCOL_ERROR,
        ),
    ],
)
def test_dispatch_maps_failures_to_content_free_protocol_errors(
    mocker: MockerFixture,
    broker_request,
    method: str,
    result: object,
    category: BrokerErrorCategory,
) -> None:
    dispatcher, service = _dispatcher(mocker)
    target = getattr(service, method)
    if isinstance(result, BrokerError):
        target.side_effect = result
    else:
        target.return_value = result

    response = dispatcher.dispatch(PRINCIPAL, broker_request)

    assert response == ErrorResponse(
        REQUEST_ID, request_operation(broker_request), category
    )


def test_dispatch_rejects_untrusted_principal(mocker: MockerFixture) -> None:
    dispatcher, _ = _dispatcher(mocker)

    with pytest.raises(ValueError, match="principal"):
        dispatcher.dispatch(
            cast("AuthenticatedPrincipal", object()), ReadyRequest(REQUEST_ID, 1)
        )
