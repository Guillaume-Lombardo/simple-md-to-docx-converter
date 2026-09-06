"""Composition root for one durable reverse-worker supervisor."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.protocol import ReadyRequest, ReadyResponse
from markweave.reversion_jobs.errors import (
    ReversionJobLeaseLostError,
    ReversionProofRequiredError,
)
from markweave.reversion_jobs.models import (
    TERMINAL_REVERSION_STATES,
    ReversionFailure,
)
from markweave.reversion_jobs.result_validation import validate_reverse_result
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker_execution import (
    ClaimedReversion,
    ReversionAttemptExecutor,
    ReversionClaimService,
    ReversionHeartbeat,
)
from markweave.reversion_jobs.worker_maintenance import (
    ReversionMaintenanceService,
    ReversionRecoveryResult,
)
from markweave.reversion_jobs.worker_publication import ReversionPublicationService
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory


class ReversionWorker:
    """Reconcile, claim, execute, validate, publish, and retire one attempt."""

    def __init__(self, runtime: ReversionWorkerRuntime) -> None:
        self._runtime = runtime
        self._claims = ReversionClaimService(runtime)
        self._executor = ReversionAttemptExecutor(runtime)
        self._publication = ReversionPublicationService(runtime)
        self._maintenance = ReversionMaintenanceService(runtime)
        self._reconciliation_token = runtime.request_id_factory()

    def reconcile(self, token: UUID | None = None) -> None:
        """Reach the principal broker-inventory fixed point before queue work."""

        if self._runtime.require_ready:
            ready_request = ReadyRequest(self._runtime.request_id_factory(), 1)
            ready_response = self._runtime.broker.request(ready_request)
            if (
                type(ready_response) is not ReadyResponse
                or ready_response.request_id != ready_request.request_id
                or not ready_response.ready
            ):
                raise BrokerError(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
        now = self._runtime.clock()
        reconciliation_token = token or self._runtime.request_id_factory()
        self._runtime.reconciler.reconcile(
            self._runtime.principal,
            f"{self._runtime.worker_id}-reconciler",
            reconciliation_token,
            now,
            now + timedelta(seconds=self._runtime.policy.recovery_lease_seconds),
            now_factory=self._runtime.clock,
        )

    def reconcile_step(self) -> bool:
        """Advance one bounded authenticated reconciliation quantum."""

        if self._runtime.require_ready:
            ready_request = ReadyRequest(self._runtime.request_id_factory(), 1)
            ready_response = self._runtime.broker.request(ready_request)
            if (
                type(ready_response) is not ReadyResponse
                or ready_response.request_id != ready_request.request_id
                or not ready_response.ready
            ):
                raise BrokerError(BrokerErrorCategory.RECONCILIATION_INCOMPLETE)
        now = self._runtime.clock()
        return self._runtime.reconciler.reconcile_step(
            self._runtime.principal,
            f"{self._runtime.worker_id}-reconciler",
            self._reconciliation_token,
            now,
            now + timedelta(seconds=self._runtime.policy.recovery_lease_seconds),
            now_factory=self._runtime.clock,
        )

    def run_once(self) -> bool:
        """Run at most one exact reverse attempt after mandatory reconciliation."""

        if self._runtime.shutdown_requested():
            return False
        self.reconcile()
        if self._runtime.shutdown_requested():
            return False
        return self.run_reconciled_once()

    def run_reconciled_once(self) -> bool:
        """Claim and execute once after a caller-observed reconciliation fixed point."""

        if self._runtime.shutdown_requested():
            return False
        claimed = self._claims.claim()
        if claimed is None:
            return False
        with ReversionHeartbeat(self._runtime, claimed) as heartbeat:
            try:
                executed = self._executor.execute(claimed, heartbeat)
                validated = validate_reverse_result(
                    claimed.job,
                    executed.result.mode,
                    executed.result.result,
                    self._runtime.content_limits,
                )
                self._publication.publish(executed, validated, heartbeat)
            except ReverseConversionError as error:
                self._finish_rejection(claimed, error)
        return True

    def recover_step(self) -> ReversionRecoveryResult:
        """Run one recovery batch without starting another reconciliation drain."""

        return self._maintenance.recover_step()

    def recover(self) -> int:
        """Reconcile first, then recover only attempts with durable empty proof."""

        token = self._runtime.request_id_factory()
        recovered_total = 0
        while True:
            try:
                self.reconcile(token)
                break
            except ReversionProofRequiredError:
                result = self._maintenance.recover_step()
                if not result.progressed:
                    raise
                recovered_total += result.requeued
        return recovered_total + self._maintenance.recover_step().requeued

    def cleanup(self) -> int:
        """Run one configured bounded reverse retention batch."""

        return self._maintenance.cleanup()

    def _finish_rejection(
        self, claimed: ClaimedReversion, error: ReverseConversionError
    ) -> None:
        retained = self._runtime.repository.get_internal(claimed.job.id)
        if retained is None or retained.state in TERMINAL_REVERSION_STATES:
            raise error
        now = self._runtime.clock()
        expires_at = now + timedelta(
            seconds=self._runtime.policy.result_retention_seconds
        )
        if error.category is ReverseErrorCategory.LEASE_LOST:
            raise ReversionJobLeaseLostError("Reverse job lease was lost") from error
        if error.category is ReverseErrorCategory.CANCELLED:
            self._runtime.repository.finish_cancelled(
                claimed.job.id,
                claimed.attempt_id,
                self._runtime.worker_id,
                claimed.lease_token,
                now,
                expires_at,
            )
        else:
            self._runtime.repository.fail(
                ReversionFailure(
                    claimed.job.id,
                    claimed.attempt_id,
                    self._runtime.worker_id,
                    claimed.lease_token,
                    error.category.value,
                    error.message,
                    now,
                    expires_at,
                )
            )
            if self._runtime.metrics is not None:
                self._runtime.metrics.record_reversion_failure(error.category.value)
        attempt = self._runtime.repository.get_attempt(claimed.attempt_id)
        if attempt is not None and attempt.termination_proof is not None:
            self._publication.acknowledge_attempt(attempt)
