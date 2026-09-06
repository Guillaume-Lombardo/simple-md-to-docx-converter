"""Atomic reverse-result publication and broker-proof acknowledgement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from markweave.broker.errors import BrokerError
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    ErrorResponse,
)
from markweave.reversion_jobs.models import (
    ReversionAttempt,
    ReversionJobState,
    ReversionJobStep,
    reversion_result_object_id,
)
from markweave.reversion_jobs.result_validation import ValidatedReverseResult
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker_execution import (
    ExecutedReversion,
    ReversionHeartbeat,
)
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.storage import ObjectKey, ObjectScope


@dataclass(frozen=True, slots=True)
class PublishedReversion:
    """Effective terminal state and deterministic result identity."""

    state: ReversionJobState
    result_object_id: UUID | None


class ReversionPublicationService:
    """Publish one proof-gated result, then retire its broker tombstone."""

    def __init__(self, runtime: ReversionWorkerRuntime) -> None:
        self._runtime = runtime

    def publish(
        self,
        executed: ExecutedReversion,
        result: ValidatedReverseResult,
        heartbeat: ReversionHeartbeat,
    ) -> PublishedReversion:
        """Commit the result under the exact attempt fence and compensate races."""

        claimed = executed.claimed
        job = claimed.job
        result_id = reversion_result_object_id(job.id, job.attempt)
        key = ObjectKey(ObjectScope.REVERSION_RESULT, job.owner_id, result_id)
        heartbeat.progress(ReversionJobStep.PUBLISHING)
        committed = False
        try:
            self._runtime.objects.put(key, result.content)
            heartbeat.raise_if_interrupted()
            now = self._runtime.clock()
            finished = self._runtime.repository.succeed(
                job.id,
                claimed.attempt_id,
                self._runtime.worker_id,
                claimed.lease_token,
                result_id,
                result.trace.result_mode,
                result.sha256,
                result.size,
                result.trace,
                now,
                now + timedelta(seconds=self._runtime.policy.result_retention_seconds),
            )
            committed = True
        except BaseException:
            if not committed:
                self._runtime.objects.delete(key)
            raise
        if finished.state is ReversionJobState.CANCELLED:
            self._runtime.objects.delete(key)
            published = PublishedReversion(finished.state, None)
        elif finished.state is ReversionJobState.SUCCEEDED:
            published = PublishedReversion(finished.state, result_id)
        else:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self.acknowledge(executed)
        return published

    def acknowledge(self, executed: ExecutedReversion) -> None:
        """ACK the exact durable proof and record the local idempotent receipt."""

        claimed = executed.claimed
        attempt = self._runtime.repository.get_attempt(claimed.attempt_id)
        if (
            attempt is None
            or attempt.termination_proof != executed.proof
            or attempt.unit_id != executed.proof.unit_id
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self.acknowledge_attempt(attempt)

    def acknowledge_attempt(self, attempt: ReversionAttempt) -> None:
        """ACK one locally durable active or recovery proof idempotently."""

        proof = attempt.termination_proof
        if proof is None or attempt.unit_id != proof.unit_id:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        response = self._runtime.broker.request(
            AcknowledgeRequest(
                self._runtime.request_id_factory(),
                attempt.create_sequence,
                attempt.attempt_id,
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
            != (attempt.attempt_id, proof.unit_id, proof.proof_id)
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self._runtime.repository.acknowledge_termination_proof(
            attempt.attempt_id, proof.proof_id, self._runtime.clock()
        )
