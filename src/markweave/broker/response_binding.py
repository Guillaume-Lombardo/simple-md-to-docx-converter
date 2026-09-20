"""Validate lifecycle and workspace response identities for broker clients."""

from __future__ import annotations

from uuid import UUID

from markweave.broker.dispatch import request_operation
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerRequest,
    BrokerResponse,
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
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceOperation,
    WorkspaceResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
)


def _validate_response_binding(
    request: BrokerRequest,
    response: BrokerResponse,
    principal: AuthenticatedPrincipal,
) -> None:
    """Reject a canonical response that is not bound to the exact request."""

    if response.request_id != request.request_id:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    if isinstance(response, ErrorResponse):
        if response.operation is not request_operation(request):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    valid = False
    match request, response:
        case CreateRequest(attempt_id=attempt), CreateResponse(attempt_id=answer):
            valid = answer == attempt
        case (
            StatusRequest(attempt_id=attempt, unit_id=unit),
            StatusResponse(attempt_id=answer_attempt, unit_id=answer_unit),
        ):
            valid = (answer_attempt, answer_unit) == (attempt, unit)
        case (
            TerminateRequest(attempt_id=attempt, unit_id=unit),
            TerminateResponse(proof=proof),
        ) | (
            ProofRequest(attempt_id=attempt, unit_id=unit),
            ProofResponse(proof=proof),
        ):
            valid = (proof.attempt_id, proof.unit_id, proof.principal) == (
                attempt,
                unit,
                principal,
            )
        case (
            AcknowledgeRequest(attempt_id=attempt, unit_id=unit, proof_id=proof_id),
            AcknowledgeResponse(
                attempt_id=answer_attempt,
                unit_id=answer_unit,
                proof_id=answer_proof,
                acknowledged=True,
            ),
        ):
            valid = (answer_attempt, answer_unit, answer_proof) == (
                attempt,
                unit,
                proof_id,
            )
        case ReadyRequest(), ReadyResponse():
            valid = True
    if not valid:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _validate_workspace_response_binding(
    request: WorkspaceStageRequest | WorkspaceCollectRequest,
    response: WorkspaceResponse,
) -> None:
    if response.request_id != request.request_id:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    operation = (
        WorkspaceOperation.STAGE
        if type(request) is WorkspaceStageRequest
        else WorkspaceOperation.COLLECT
    )
    if type(response) is WorkspaceErrorResponse:
        if response.operation is not operation:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    if isinstance(request, WorkspaceStageRequest):
        if (
            type(response) is not WorkspaceStageReceipt
            or response.request_id != request.request_id
            or response.stage_sequence != request.sequence
            or response.attempt_id != request.attempt_id
            or response.unit_id != request.unit_id
            or response.create_sequence != request.create_sequence
            or type(response.incarnation_id) is not UUID
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        return
    if not isinstance(request, WorkspaceCollectRequest):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    receipt = getattr(response, "receipt", None)
    expected = WorkspaceStageReceipt(
        request.receipt_request_id,
        request.stage_sequence,
        request.attempt_id,
        request.unit_id,
        request.create_sequence,
        request.incarnation_id,
    )
    if receipt != expected:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
