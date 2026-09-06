"""Proof-gated reverse-attempt recovery and bounded object cleanup."""

from __future__ import annotations

from datetime import timedelta

from markweave.broker.errors import BrokerError
from markweave.broker.protocol import (
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    TerminateRequest,
    TerminateResponse,
)
from markweave.reversion_jobs.models import ReversionAttempt
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker_publication import ReversionPublicationService
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.storage import ObjectKey, ObjectScope


class ReversionMaintenanceService:
    """Recover only proven-empty attempts and expire retry-safe object bundles."""

    def __init__(self, runtime: ReversionWorkerRuntime) -> None:
        self._runtime = runtime
        self._publication = ReversionPublicationService(runtime)

    def recover(self) -> int:
        """Prove expired created units empty before making their jobs claimable."""

        now = self._runtime.clock()
        attempts = self._runtime.repository.claim_recovery(
            f"{self._runtime.worker_id}-recovery",
            now,
            now + timedelta(seconds=self._runtime.policy.recovery_lease_seconds),
            self._runtime.policy.recovery_batch_size,
        )
        for attempt in attempts:
            recorded = self._recover_attempt(attempt)
            self._publication.acknowledge_attempt(recorded)
        now = self._runtime.clock()
        return self._runtime.repository.recover_expired_leases(
            now,
            now + timedelta(seconds=self._runtime.policy.result_retention_seconds),
            now - timedelta(seconds=self._runtime.policy.incomplete_submission_seconds),
        )

    def _recover_attempt(self, attempt: ReversionAttempt) -> ReversionAttempt:
        unit_id = attempt.unit_id
        if unit_id is None:
            create_request = CreateRequest(
                self._runtime.request_id_factory(),
                attempt.create_sequence,
                attempt.attempt_id,
            )
            created = self._runtime.broker.request(create_request)
            self._raise_broker_error(created)
            if (
                type(created) is not CreateResponse
                or created.request_id != create_request.request_id
                or created.attempt_id != attempt.attempt_id
            ):
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            unit_id = created.unit_id
        terminate_request = TerminateRequest(
            self._runtime.request_id_factory(),
            attempt.create_sequence,
            attempt.attempt_id,
            unit_id,
        )
        terminated = self._runtime.broker.request(terminate_request)
        self._raise_broker_error(terminated)
        if (
            type(terminated) is not TerminateResponse
            or terminated.request_id != terminate_request.request_id
            or terminated.proof.attempt_id != attempt.attempt_id
            or terminated.proof.unit_id != unit_id
            or terminated.proof.principal != self._runtime.principal
            or terminated.proof.policy_revision != self._runtime.broker_policy.revision
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        if attempt.recovery_token is None:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return self._runtime.repository.record_recovery_termination_proof(
            attempt.job_id,
            attempt.attempt_id,
            attempt.recovery_token,
            terminated.proof,
            self._runtime.clock(),
        )

    def cleanup(self) -> int:
        """Delete one configured bounded terminal batch and close each fence."""

        now = self._runtime.clock()
        expired = self._runtime.repository.expire_terminal(
            f"{self._runtime.worker_id}-cleanup",
            now,
            now + timedelta(seconds=self._runtime.policy.cleanup_lease_seconds),
            self._runtime.policy.cleanup_batch_size,
        )
        for candidate in expired:
            self._runtime.objects.delete(
                ObjectKey(
                    ObjectScope.REVERSION_UPLOAD,
                    candidate.owner_id,
                    candidate.source_object_id,
                )
            )
            for object_id in candidate.result_object_ids:
                self._runtime.objects.delete(
                    ObjectKey(
                        ObjectScope.REVERSION_RESULT,
                        candidate.owner_id,
                        object_id,
                    )
                )
            self._runtime.repository.complete_cleanup(
                candidate.job_id, candidate.cleanup_token
            )
        return len(expired)

    @staticmethod
    def _raise_broker_error(response: object) -> None:
        if type(response) is ErrorResponse:
            raise BrokerError(response.category)
