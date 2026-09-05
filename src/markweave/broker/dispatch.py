"""Pure authenticated request dispatch for the reverse-isolation broker."""

from __future__ import annotations

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal, ReplayPosition
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
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
from markweave.broker.service import IsolationBrokerService
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceOperation,
    WorkspaceResponse,
    WorkspaceStageRequest,
)


def request_operation(request: BrokerRequest) -> BrokerOperation:
    """Return the closed protocol operation for a decoded request."""

    match request:
        case CreateRequest():
            return BrokerOperation.CREATE
        case StatusRequest():
            return BrokerOperation.STATUS
        case TerminateRequest():
            return BrokerOperation.TERMINATE
        case ProofRequest():
            return BrokerOperation.PROOF
        case AcknowledgeRequest():
            return BrokerOperation.ACK
        case ReadyRequest():
            return BrokerOperation.READY


class BrokerDispatcher:
    """Bind a transport-authenticated principal to the broker service."""

    def __init__(self, service: IsolationBrokerService) -> None:
        if not isinstance(service, IsolationBrokerService):
            raise ValueError("Broker service is invalid")
        self._service = service

    def dispatch(
        self,
        principal: AuthenticatedPrincipal,
        request: BrokerRequest,
    ) -> BrokerResponse:
        """Dispatch one request and return only a canonical response model."""

        if type(principal) is not AuthenticatedPrincipal:
            raise ValueError("Authenticated broker principal is invalid")
        operation = request_operation(request)
        try:
            response: BrokerResponse
            match request:
                case CreateRequest(request_id, sequence, attempt_id):
                    unit = self._service.create(
                        ReplayPosition(principal, sequence), attempt_id
                    )
                    response = CreateResponse(
                        request_id, unit.attempt_id, unit.unit_id, unit.state
                    )
                case StatusRequest(request_id, _, attempt_id, unit_id):
                    unit = self._service.status(principal, attempt_id, unit_id)
                    response = StatusResponse(
                        request_id, unit.attempt_id, unit.unit_id, unit.state
                    )
                case TerminateRequest(request_id, _, attempt_id, unit_id):
                    proof = self._service.terminate(principal, attempt_id, unit_id)
                    response = TerminateResponse(request_id, proof)
                case ProofRequest(request_id, _, attempt_id, unit_id):
                    proof = self._service.proof(principal, attempt_id, unit_id)
                    if proof is None:
                        raise BrokerError(BrokerErrorCategory.TERMINATION_UNPROVEN)
                    response = ProofResponse(request_id, proof)
                case AcknowledgeRequest(request_id, _, attempt_id, unit_id, proof_id):
                    acknowledged = self._service.acknowledge(
                        principal, attempt_id, unit_id, proof_id
                    )
                    if not acknowledged:
                        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
                    response = AcknowledgeResponse(
                        request_id, attempt_id, unit_id, proof_id, True
                    )
                case ReadyRequest(request_id):
                    response = ReadyResponse(request_id, self._service.ready)
        except BrokerError as error:
            return ErrorResponse(request.request_id, operation, error.category)
        return response

    def start(self) -> None:
        """Complete fail-closed reconciliation before a transport is exposed."""

        self._service.start()

    def dispatch_workspace(
        self,
        principal: AuthenticatedPrincipal,
        request: WorkspaceStageRequest | WorkspaceCollectRequest,
    ) -> WorkspaceResponse:
        """Dispatch one separately versioned workspace operation."""

        if type(principal) is not AuthenticatedPrincipal:
            raise ValueError("Authenticated broker principal is invalid")
        if type(request) is WorkspaceStageRequest:
            operation = WorkspaceOperation.STAGE
        elif type(request) is WorkspaceCollectRequest:
            operation = WorkspaceOperation.COLLECT
        else:
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        try:
            if type(request) is WorkspaceStageRequest:
                return self._service.stage_workspace(principal, request)
            if type(request) is WorkspaceCollectRequest:
                return self._service.collect_workspace(principal, request)
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        except BrokerError as error:
            return WorkspaceErrorResponse(request.request_id, operation, error.category)
