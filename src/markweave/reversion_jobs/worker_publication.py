"""Atomic reverse-result publication and broker-proof acknowledgement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID

from markweave.broker.errors import BrokerError
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
    ErrorResponse,
)
from markweave.reversion_jobs.models import (
    ReversionAttempt,
    ReversionJob,
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
        try:
            self._runtime.objects.put(key, result.content)
        except BaseException:
            self._runtime.objects.delete(key)
            raise
        try:
            heartbeat.raise_if_interrupted()
        except BaseException:
            self._runtime.objects.delete(key)
            raise
        now = self._runtime.clock()
        try:
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
        except BaseException as commit_error:
            try:
                retained = self._runtime.repository.get_internal(job.id)
            except BaseException as lookup_error:
                raise commit_error from lookup_error
            if self._matches_commit(retained, result_id, result) or (
                retained is not None and retained.state is ReversionJobState.CANCELLED
            ):
                finished = cast(ReversionJob, retained)
            else:
                self._runtime.objects.delete(key)
                raise commit_error
        if finished.state is ReversionJobState.CANCELLED:
            self._runtime.objects.delete(key)
            published = PublishedReversion(finished.state, None)
        elif finished.state is ReversionJobState.SUCCEEDED:
            published = PublishedReversion(finished.state, result_id)
        else:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self.acknowledge(executed)
        return published

    @staticmethod
    def _matches_commit(
        retained: ReversionJob | None,
        result_id: UUID,
        result: ValidatedReverseResult,
    ) -> bool:
        return (
            retained is not None
            and retained.state is ReversionJobState.SUCCEEDED
            and retained.result_object_id == result_id
            and retained.result_mode is result.trace.result_mode
            and retained.result_sha256 == result.sha256
            and retained.result_size == result.size
            and retained.trace == result.trace
        )

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
        request = AcknowledgeRequest(
            self._runtime.request_id_factory(),
            attempt.create_sequence,
            attempt.attempt_id,
            proof.unit_id,
            proof.proof_id,
        )
        response = self._runtime.broker.request(request)
        if type(response) is ErrorResponse:
            if (
                response.request_id != request.request_id
                or response.operation is not BrokerOperation.ACK
            ):
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            raise BrokerError(response.category)
        if (
            type(response) is not AcknowledgeResponse
            or response.request_id != request.request_id
            or not response.acknowledged
            or (response.attempt_id, response.unit_id, response.proof_id)
            != (attempt.attempt_id, proof.unit_id, proof.proof_id)
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self._runtime.repository.acknowledge_termination_proof(
            attempt.attempt_id, proof.proof_id, self._runtime.clock()
        )
