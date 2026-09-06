"""Claim, heartbeat, broker execution, and proof persistence for reverse jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from threading import Event, Lock, Thread
from uuid import UUID

from markweave.broker.errors import BrokerError
from markweave.broker.models import (
    ManagedUnitState,
    TerminationProof,
    policy_specification_evidence,
)
from markweave.broker.protocol import (
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    TerminateRequest,
    TerminateResponse,
)
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceFailureResponse,
    WorkspacePendingResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.reversion_jobs.errors import (
    ReversionJobLeaseLostError,
    ReversionWorkerInterruptedError,
)
from markweave.reversion_jobs.models import (
    ReversionJob,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
)
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.models import ReverseAttemptSuccess
from markweave.storage import ObjectKey, ObjectScope, ObjectTooLargeError


@dataclass(frozen=True, slots=True)
class ClaimedReversion:
    """One exact durable claim and its monotonic deadline origin."""

    job: ReversionJob
    attempt_id: UUID
    lease_token: UUID
    started_monotonic: float

    def __post_init__(self) -> None:
        if type(self.attempt_id) is not UUID or type(self.lease_token) is not UUID:
            raise ValueError("Reverse claim fencing identity is invalid")


@dataclass(frozen=True, slots=True)
class ExecutedReversion:
    """Unpublished child result paired with its durable termination proof."""

    claimed: ClaimedReversion
    result: ReverseAttemptSuccess
    proof: TerminationProof


class ReversionHeartbeat:
    """Maintain an exact reverse-attempt lease and expose interruption state."""

    def __init__(
        self, runtime: ReversionWorkerRuntime, claimed: ClaimedReversion
    ) -> None:
        self._runtime = runtime
        self._claimed = claimed
        self._step = ReversionJobStep.ISOLATING
        self._lock = Lock()
        self._stop = Event()
        self._lease_lost = Event()
        self._error: BaseException | None = None
        self._thread = Thread(
            target=self._keepalive,
            name=f"{runtime.worker_id}-reverse-heartbeat",
            daemon=False,
        )

    def __enter__(self) -> ReversionHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()

    def progress(self, step: ReversionJobStep) -> None:
        with self._lock:
            self._step = step
            self._heartbeat()

    def raise_if_interrupted(self) -> None:
        if self._error is not None:
            raise self._error
        if self._lease_lost.is_set():
            raise ReversionJobLeaseLostError("Reverse job lease was lost")
        policy = self._runtime.policy
        if (
            self._runtime.monotonic_clock() - self._claimed.started_monotonic
            >= policy.max_job_duration_seconds
        ):
            reject(ReverseErrorCategory.TIMED_OUT)
        if self._runtime.shutdown_requested():
            raise ReversionWorkerInterruptedError("Reverse worker shutdown requested")
        job = self._claimed.job
        if self._runtime.repository.cancellation_requested(
            job.id,
            self._claimed.attempt_id,
            self._runtime.worker_id,
            self._claimed.lease_token,
        ):
            reject(ReverseErrorCategory.CANCELLED)

    def _heartbeat(self) -> None:
        now = self._runtime.clock()
        if not self._runtime.repository.heartbeat(
            ReversionLeaseHeartbeat(
                self._claimed.job.id,
                self._claimed.attempt_id,
                self._runtime.worker_id,
                self._claimed.lease_token,
                now,
                now + timedelta(seconds=self._runtime.policy.lease_seconds),
                self._step,
            )
        ):
            self._lease_lost.set()
            raise ReversionJobLeaseLostError("Reverse job lease was lost")

    def _keepalive(self) -> None:
        while not self._stop.wait(self._runtime.policy.heartbeat_seconds):
            with self._lock:
                try:
                    self._heartbeat()
                except BaseException as error:
                    self._error = error
                    self._lease_lost.set()
                    return


class ReversionClaimService:
    """Claim only after the principal broker inventory is reconciled."""

    def __init__(self, runtime: ReversionWorkerRuntime) -> None:
        self._runtime = runtime

    def claim(self) -> ClaimedReversion | None:
        now = self._runtime.clock()
        job = self._runtime.repository.claim(
            self._runtime.worker_id,
            self._runtime.principal,
            now,
            now + timedelta(seconds=self._runtime.policy.lease_seconds),
        )
        if job is None:
            return None
        if job.current_attempt_id is None or job.lease_token is None:
            raise ReversionJobLeaseLostError("Reverse job claim is incomplete")
        return ClaimedReversion(
            job,
            job.current_attempt_id,
            job.lease_token,
            self._runtime.monotonic_clock(),
        )


class ReversionAttemptExecutor:
    """Run one claimed source and persist proof before exposing its result."""

    def __init__(self, runtime: ReversionWorkerRuntime) -> None:
        self._runtime = runtime

    def execute(
        self, claimed: ClaimedReversion, heartbeat: ReversionHeartbeat
    ) -> ExecutedReversion:
        job = claimed.job
        source = self._source(job)
        heartbeat.raise_if_interrupted()
        specification = policy_specification_evidence(self._runtime.broker_policy)
        attempt = self._runtime.repository.reserve_create_intent(
            job.id,
            claimed.attempt_id,
            self._runtime.worker_id,
            claimed.lease_token,
            self._runtime.broker_policy.revision,
            specification,
            self._runtime.clock(),
        )
        request = CreateRequest(
            self._runtime.request_id_factory(),
            attempt.create_sequence,
            claimed.attempt_id,
        )
        response = self._runtime.broker.request(request)
        self._raise_broker_error(response)
        if (
            type(response) is not CreateResponse
            or response.request_id != request.request_id
            or response.attempt_id != claimed.attempt_id
            or response.state is not ManagedUnitState.CREATED
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        self._runtime.repository.record_broker_unit(
            job.id,
            claimed.attempt_id,
            self._runtime.worker_id,
            claimed.lease_token,
            response.unit_id,
            self._runtime.clock(),
        )
        result: ReverseAttemptSuccess | None = None
        pending_error: BaseException | None = None
        proof: TerminationProof | None = None
        try:
            heartbeat.progress(ReversionJobStep.CONVERTING)
            receipt = self._stage(
                claimed, response.unit_id, attempt.create_sequence, source
            )
            result = self._collect(claimed, receipt, heartbeat)
        except BaseException as error:
            pending_error = error
        try:
            proof = self._terminate(claimed, response.unit_id, attempt.create_sequence)
            self._runtime.repository.record_active_termination_proof(
                job.id,
                claimed.attempt_id,
                self._runtime.worker_id,
                claimed.lease_token,
                proof,
                self._runtime.clock(),
            )
        except BaseException as termination_error:
            if pending_error is not None:
                raise termination_error from pending_error
            raise
        if pending_error is not None:
            raise pending_error
        if result is None or proof is None:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        heartbeat.progress(ReversionJobStep.VALIDATING)
        return ExecutedReversion(claimed, result, proof)

    def _source(self, job: ReversionJob) -> bytes:
        try:
            source = self._runtime.objects.get_bounded(
                ObjectKey(
                    ObjectScope.REVERSION_UPLOAD, job.owner_id, job.source_object_id
                ),
                self._runtime.content_limits.max_input_bytes,
            )
        except ObjectTooLargeError:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        if (
            len(source) != job.source_size
            or sha256(source).hexdigest() != job.source_sha256
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return source

    def _stage(
        self,
        claimed: ClaimedReversion,
        unit_id: UUID,
        create_sequence: int,
        source: bytes,
    ) -> WorkspaceStageReceipt:
        if type(unit_id) is not UUID:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        request = WorkspaceStageRequest(
            self._runtime.request_id_factory(),
            create_sequence,
            claimed.attempt_id,
            unit_id,
            create_sequence,
            claimed.job.admission.extension,
            self._runtime.content_limits,
            source,
        )
        response = self._runtime.broker.stage_workspace(request)
        if type(response) is WorkspaceErrorResponse:
            raise BrokerError(response.category)
        if type(response) is not WorkspaceStageReceipt or (
            response.request_id,
            response.stage_sequence,
            response.attempt_id,
            response.unit_id,
            response.create_sequence,
        ) != (
            request.request_id,
            request.sequence,
            request.attempt_id,
            request.unit_id,
            request.create_sequence,
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return response

    def _collect(
        self,
        claimed: ClaimedReversion,
        receipt: WorkspaceStageReceipt,
        heartbeat: ReversionHeartbeat,
    ) -> ReverseAttemptSuccess:
        while True:
            heartbeat.raise_if_interrupted()
            request = WorkspaceCollectRequest(
                self._runtime.request_id_factory(),
                receipt.stage_sequence,
                receipt.request_id,
                receipt.stage_sequence,
                claimed.attempt_id,
                receipt.unit_id,
                receipt.create_sequence,
                receipt.incarnation_id,
            )
            response = self._runtime.broker.collect_workspace(request)
            if getattr(response, "request_id", None) != request.request_id:
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            if type(response) is WorkspacePendingResponse:
                if response.receipt != receipt:
                    reject(ReverseErrorCategory.PROTOCOL_ERROR)
                self._runtime.wait(self._runtime.policy.collect_poll_seconds)
                continue
            if type(response) is WorkspaceErrorResponse:
                raise BrokerError(response.category)
            if type(response) is WorkspaceFailureResponse:
                if response.receipt != receipt:
                    reject(ReverseErrorCategory.PROTOCOL_ERROR)
                reject(response.category)
            if type(response) is not WorkspaceSuccessResponse:
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            if response.receipt != receipt:
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            return ReverseAttemptSuccess(
                claimed.attempt_id, response.mode, response.result
            )

    def _terminate(
        self, claimed: ClaimedReversion, unit_id: UUID, create_sequence: int
    ) -> TerminationProof:
        if type(unit_id) is not UUID:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        request = TerminateRequest(
            self._runtime.request_id_factory(),
            create_sequence,
            claimed.attempt_id,
            unit_id,
        )
        response = self._runtime.broker.request(request)
        self._raise_broker_error(response)
        if (
            type(response) is not TerminateResponse
            or response.request_id != request.request_id
            or response.proof.attempt_id != claimed.attempt_id
            or response.proof.unit_id != unit_id
            or response.proof.principal != self._runtime.principal
            or response.proof.policy_revision != self._runtime.broker_policy.revision
        ):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return response.proof

    @staticmethod
    def _raise_broker_error(response: object) -> None:
        if type(response) is ErrorResponse:
            raise BrokerError(response.category)
