"""Principal-exclusive broker inventory reconciliation orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
    BrokerResponse,
    ErrorResponse,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationErrorResponse,
    ReconciliationRequest,
    ReconciliationResponse,
    ReconciliationResult,
    ReconciliationTombstone,
)
from markweave.reversion_jobs.errors import ReversionJobConflictError


class ReconciliationBroker(Protocol):
    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult: ...
    def request(self, request: AcknowledgeRequest) -> BrokerResponse: ...


class ReconciliationStore(Protocol):
    def begin_reconciliation(
        self,
        principal: AuthenticatedPrincipal,
        owner: str,
        token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> int: ...
    def pending_reconciliation_acknowledgements(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        now: datetime,
        limit: int,
    ) -> tuple[ReconciliationTombstone, ...]: ...
    def record_reconciliation_page(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        page: ReconciliationResponse,
        now: datetime,
    ) -> ReconciliationTombstone | None: ...
    def mark_reconciliation_acknowledged(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        tombstone: ReconciliationTombstone,
        now: datetime,
    ) -> None: ...
    def complete_reconciliation(
        self, principal: AuthenticatedPrincipal, token: UUID, now: datetime
    ) -> None: ...


class ReversionBrokerReconciler:
    """Drain durable ACKs and reach a stable broker high-water fixed point."""

    def __init__(
        self,
        store: ReconciliationStore,
        broker: ReconciliationBroker,
        *,
        ack_batch_limit: int,
        request_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(ack_batch_limit) is not int
            or ack_batch_limit <= 0
            or not callable(request_id_factory)
        ):
            raise ValueError("Reconciliation service configuration is invalid")
        self._store = store
        self._broker = broker
        self._request_id_factory = request_id_factory
        self._ack_batch_limit = ack_batch_limit

    def reconcile(  # noqa: PLR0913 - explicit lease boundary
        self,
        principal: AuthenticatedPrincipal,
        owner: str,
        token: UUID,
        now: datetime,
        expires_at: datetime,
        *,
        now_factory: Callable[[], datetime],
    ) -> None:
        """Reconcile outside DB transactions and publish readiness at fixed point."""

        while not self.reconcile_step(
            principal,
            owner,
            token,
            now,
            expires_at,
            now_factory=now_factory,
        ):
            now = now_factory()

    def reconcile_step(  # noqa: PLR0913 - explicit lease boundary
        self,
        principal: AuthenticatedPrincipal,
        owner: str,
        token: UUID,
        now: datetime,
        expires_at: datetime,
        *,
        now_factory: Callable[[], datetime],
    ) -> bool:
        """Process at most one configured ACK batch and one inventory page."""

        cursor = self._store.begin_reconciliation(
            principal, owner, token, now, expires_at
        )
        pending = self._store.pending_reconciliation_acknowledgements(
            principal, token, now_factory(), self._ack_batch_limit
        )
        for tombstone in pending:
            self._ack(principal, token, tombstone, now_factory())

        request = ReconciliationRequest(self._request_id_factory(), cursor)
        response = self._broker.reconcile(request)
        if type(response) is ReconciliationErrorResponse:
            if response.request_id != request.request_id:
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            raise BrokerError(response.category)
        if (
            type(response) is not ReconciliationResponse
            or response.request_id != request.request_id
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        tombstone = self._store.record_reconciliation_page(
            principal, token, response, now_factory()
        )
        if tombstone is not None:
            return False
        try:
            self._store.complete_reconciliation(principal, token, now_factory())
        except ReversionJobConflictError:
            return False
        return True

    def _ack(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        tombstone: ReconciliationTombstone,
        now: datetime,
    ) -> None:
        proof = tombstone.proof
        request = AcknowledgeRequest(
            self._request_id_factory(),
            tombstone.create_sequence,
            proof.attempt_id,
            proof.unit_id,
            proof.proof_id,
        )
        response = self._broker.request(request)
        if type(response) is ErrorResponse:
            if (
                response.request_id != request.request_id
                or response.operation is not BrokerOperation.ACK
            ):
                raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
            raise BrokerError(response.category)
        if (
            type(response) is not AcknowledgeResponse
            or response.request_id != request.request_id
            or not response.acknowledged
            or (response.attempt_id, response.unit_id, response.proof_id)
            != (proof.attempt_id, proof.unit_id, proof.proof_id)
        ):
            raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
        self._store.mark_reconciliation_acknowledged(principal, token, tombstone, now)
