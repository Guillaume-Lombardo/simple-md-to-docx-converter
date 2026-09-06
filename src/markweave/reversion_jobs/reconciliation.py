"""Principal-exclusive broker inventory reconciliation orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from markweave.broker.errors import BrokerError
from markweave.broker.models import AuthenticatedPrincipal
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
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

        cursor = self._store.begin_reconciliation(
            principal, owner, token, now, expires_at
        )
        while pending_batch := self._store.pending_reconciliation_acknowledgements(
            principal, token, now_factory(), self._ack_batch_limit
        ):
            for pending in pending_batch:
                self._ack(principal, token, pending, now_factory())

        stable_high_water: int | None = None
        while True:
            request = ReconciliationRequest(self._request_id_factory(), cursor)
            response = self._broker.reconcile(request)
            if type(response) is ReconciliationErrorResponse:
                raise BrokerError(response.category)
            if type(response) is not ReconciliationResponse:
                raise ValueError("Broker reconciliation response is invalid")
            tombstone = self._store.record_reconciliation_page(
                principal, token, response, now_factory()
            )
            if tombstone is not None:
                self._ack(principal, token, tombstone, now_factory())
                cursor = tombstone.create_sequence
                stable_high_water = None
                continue
            if stable_high_water == response.create_sequence_high_water:
                self._store.complete_reconciliation(principal, token, now_factory())
                return
            stable_high_water = response.create_sequence_high_water

    def _ack(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        tombstone: ReconciliationTombstone,
        now: datetime,
    ) -> None:
        proof = tombstone.proof
        response = self._broker.request(
            AcknowledgeRequest(
                self._request_id_factory(),
                tombstone.create_sequence,
                proof.attempt_id,
                proof.unit_id,
                proof.proof_id,
            )
        )
        if type(response) is ErrorResponse:
            raise BrokerError(response.category)
        if (
            type(response) is not AcknowledgeResponse
            or not response.acknowledged
            or (response.attempt_id, response.unit_id, response.proof_id)
            != (proof.attempt_id, proof.unit_id, proof.proof_id)
        ):
            raise ValueError("Broker reconciliation acknowledgement is invalid")
        self._store.mark_reconciliation_acknowledged(principal, token, tombstone, now)
