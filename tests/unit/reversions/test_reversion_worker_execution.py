"""Unit coverage for fenced reverse-worker execution and cleanup."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, create_engine

from markweave.auth.models import Role, User
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    EvidenceDigest,
    ManagedUnitState,
    TerminationProof,
    policy_specification_evidence,
)
from markweave.broker.protocol import (
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
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
    WorkspaceOperation,
    WorkspacePendingResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.schema import Base
from markweave.persistence.sql import SqlUserRepository
from markweave.reversion_jobs.errors import (
    ReversionJobLeaseLostError,
    ReversionJobRepositoryError,
    ReversionProofRequiredError,
    ReversionWorkerInterruptedError,
)
from markweave.reversion_jobs.models import (
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    reversion_result_object_id,
)
from markweave.reversion_jobs.result_validation import validate_reverse_result
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.reversion_jobs.worker_execution import (
    ClaimedReversion,
    ReversionAttemptExecutor,
    ReversionClaimService,
    ReversionHeartbeat,
)
from markweave.reversion_jobs.worker_publication import ReversionPublicationService
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.models import ReverseOutputMode
from markweave.storage import (
    FilesystemObjectStore,
    ObjectKey,
    ObjectScope,
    ObjectTooLargeError,
)
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    PRINCIPAL,
    RETENTION_END,
    complete_empty_reconciliation,
    submission,
)
from tests.unit.reversions.test_reversion_worker_runtime import (
    BROKER_POLICY,
    CONTENT_LIMITS,
    POLICY,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def repository() -> Iterator[tuple[SqlReversionJobRepository, User, Engine]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:user", Role.USER)
    users.create(owner)
    repo = SqlReversionJobRepository(engine)
    complete_empty_reconciliation(repo)
    try:
        yield repo, owner, engine
    finally:
        engine.dispose()


def _runtime(
    mocker: MockerFixture,
    repository: SqlReversionJobRepository,
    objects: FilesystemObjectStore,
) -> ReversionWorkerRuntime:
    return ReversionWorkerRuntime(
        repository,
        objects,
        mocker.Mock(),
        mocker.Mock(),
        PRINCIPAL,
        BROKER_POLICY,
        CONTENT_LIMITS,
        POLICY,
        "reverse-worker",
        mocker.Mock(return_value=NOW),
        mocker.Mock(return_value=1.0),
        mocker.Mock(return_value=False),
        lambda: False,
        uuid4,
    )


def _claim(
    runtime: ReversionWorkerRuntime,
    repository: SqlReversionJobRepository,
    owner: User,
    source: bytes,
) -> ClaimedReversion:
    submitted = replace(
        submission(owner.id),
        source_sha256=sha256(source).hexdigest(),
        source_size=len(source),
    )
    job, _ = repository.create(submitted)
    runtime.objects.put(
        ObjectKey(ObjectScope.REVERSION_UPLOAD, owner.id, job.source_object_id), source
    )
    repository.activate_source(job.id, NOW)
    claimed = ReversionClaimService(runtime).claim()
    assert claimed is not None
    return claimed


def _queue(
    runtime: ReversionWorkerRuntime,
    repository: SqlReversionJobRepository,
    owner: User,
    source: bytes,
) -> ReversionJob:
    submitted = replace(
        submission(owner.id),
        source_sha256=sha256(source).hexdigest(),
        source_size=len(source),
    )
    job, _ = repository.create(submitted)
    runtime.objects.put(
        ObjectKey(ObjectScope.REVERSION_UPLOAD, owner.id, job.source_object_id), source
    )
    repository.activate_source(job.id, NOW)
    return job


def _proof(attempt_id: UUID, unit_id: UUID) -> TerminationProof:
    evidence = EvidenceDigest("sha256:" + "b" * 64)
    return TerminationProof(
        uuid4(),
        attempt_id,
        unit_id,
        PRINCIPAL,
        BROKER_POLICY.revision,
        evidence,
        evidence,
        evidence,
    )


def _successful_broker(mocker: MockerFixture, runtime: ReversionWorkerRuntime) -> None:
    del mocker
    broker = cast(Any, runtime.broker)
    unit_id = uuid4()
    receipt: WorkspaceStageReceipt | None = None

    def request(message: object) -> object:
        if type(message) is CreateRequest:
            return CreateResponse(
                message.request_id,
                message.attempt_id,
                unit_id,
                ManagedUnitState.CREATED,
            )
        if type(message) is TerminateRequest:
            return TerminateResponse(
                message.request_id, _proof(message.attempt_id, message.unit_id)
            )
        assert type(message) is AcknowledgeRequest
        return AcknowledgeResponse(
            message.request_id,
            message.attempt_id,
            message.unit_id,
            message.proof_id,
            True,
        )

    def stage(message: WorkspaceStageRequest) -> WorkspaceStageReceipt:
        nonlocal receipt
        receipt = WorkspaceStageReceipt(
            message.request_id,
            message.sequence,
            message.attempt_id,
            message.unit_id,
            message.create_sequence,
            uuid4(),
        )
        return receipt

    pending = True

    def collect(message: WorkspaceCollectRequest) -> object:
        nonlocal pending
        assert receipt is not None
        if pending:
            pending = False
            return WorkspacePendingResponse(message.request_id, receipt)
        return WorkspaceSuccessResponse(
            message.request_id,
            receipt,
            ReverseOutputMode.MARKDOWN,
            b"# Result\n",
        )

    broker.request.side_effect = request
    broker.stage_workspace.side_effect = stage
    broker.collect_workspace.side_effect = collect


def test_executor_collects_result_and_persists_proof_before_returning(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    heartbeat = mocker.Mock()

    executed = ReversionAttemptExecutor(runtime).execute(claimed, heartbeat)

    assert executed.result.result == b"# Result\n"
    assert executed.result.mode is ReverseOutputMode.MARKDOWN
    attempt = repo.get_attempt(executed.claimed.attempt_id)
    assert attempt is not None and attempt.termination_proof == executed.proof
    assert heartbeat.progress.call_args_list == [
        mocker.call(ReversionJobStep.CONVERTING),
        mocker.call(ReversionJobStep.VALIDATING),
    ]
    cast(Any, runtime.wait).assert_called_once_with(POLICY.collect_poll_seconds)


def test_executor_terminates_and_records_proof_after_child_failure(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    receipt: WorkspaceStageReceipt | None = None

    def fail_collect(message: WorkspaceCollectRequest) -> WorkspaceFailureResponse:
        assert receipt is not None
        return WorkspaceFailureResponse(
            message.request_id,
            receipt,
            ReverseErrorCategory.MALFORMED,
        )

    def stage(message: WorkspaceStageRequest) -> WorkspaceStageReceipt:
        nonlocal receipt
        receipt = WorkspaceStageReceipt(
            message.request_id,
            message.sequence,
            message.attempt_id,
            message.unit_id,
            message.create_sequence,
            uuid4(),
        )
        return receipt

    broker = cast(Any, runtime.broker)
    broker.stage_workspace.side_effect = stage
    broker.collect_workspace.side_effect = fail_collect

    with pytest.raises(ReverseConversionError) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    assert captured.value.category is ReverseErrorCategory.MALFORMED
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.termination_proof is not None
    assert any(
        type(call.args[0]) is TerminateRequest for call in broker.request.call_args_list
    )


def test_executor_surfaces_termination_failure_over_child_failure(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    broker = cast(Any, runtime.broker)
    broker.collect_workspace.side_effect = BrokerError(
        BrokerErrorCategory.RUNTIME_FAILURE
    )
    original = broker.request.side_effect

    def fail_termination(message: object) -> object:
        if type(message) is TerminateRequest:
            return ErrorResponse(
                message.request_id,
                BrokerOperation.TERMINATE,
                BrokerErrorCategory.RUNTIME_FAILURE,
            )
        return original(message)

    broker.request.side_effect = fail_termination

    with pytest.raises(BrokerError) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    assert captured.value.category is BrokerErrorCategory.RUNTIME_FAILURE
    assert captured.value.__cause__ is not None
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.termination_proof is None


def test_executor_rejects_corrupt_source_before_creating_runtime(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    runtime.objects.put(
        ObjectKey(
            ObjectScope.REVERSION_UPLOAD,
            owner.id,
            claimed.job.source_object_id,
        ),
        b"tampered",
    )

    with pytest.raises(ReverseConversionError) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR
    cast(Any, runtime.broker).request.assert_not_called()


def test_publication_commits_result_then_acknowledges_proof(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    heartbeat = mocker.Mock()
    executed = ReversionAttemptExecutor(runtime).execute(claimed, heartbeat)
    result = validate_reverse_result(
        claimed.job,
        executed.result.mode,
        executed.result.result,
        CONTENT_LIMITS,
    )

    published = ReversionPublicationService(runtime).publish(
        executed, result, heartbeat
    )

    result_id = reversion_result_object_id(claimed.job.id, claimed.job.attempt)
    assert published.state is ReversionJobState.SUCCEEDED
    assert published.result_object_id == result_id
    assert (
        objects.get(ObjectKey(ObjectScope.REVERSION_RESULT, owner.id, result_id))
        == b"# Result\n"
    )
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.proof_acknowledged_at == NOW
    assert (
        type(cast(Any, runtime.broker).request.call_args.args[0]) is AcknowledgeRequest
    )


def test_publication_cancellation_race_deletes_result_but_acks_proof(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    heartbeat = mocker.Mock()
    executed = ReversionAttemptExecutor(runtime).execute(claimed, heartbeat)
    result = validate_reverse_result(
        claimed.job,
        executed.result.mode,
        executed.result.result,
        CONTENT_LIMITS,
    )
    repo.request_cancel(claimed.job.id, owner.id, NOW, RETENTION_END)

    published = ReversionPublicationService(runtime).publish(
        executed, result, heartbeat
    )

    result_id = reversion_result_object_id(claimed.job.id, claimed.job.attempt)
    assert published == type(published)(ReversionJobState.CANCELLED, None)
    assert not objects.exists(
        ObjectKey(ObjectScope.REVERSION_RESULT, owner.id, result_id)
    )
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.proof_acknowledged_at == NOW


def test_publication_compensates_object_when_lease_interrupts_before_commit(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    execution_heartbeat = mocker.Mock()
    executed = ReversionAttemptExecutor(runtime).execute(claimed, execution_heartbeat)
    result = validate_reverse_result(
        claimed.job,
        executed.result.mode,
        executed.result.result,
        CONTENT_LIMITS,
    )
    heartbeat = mocker.Mock()
    heartbeat.raise_if_interrupted.side_effect = ReverseConversionError(
        ReverseErrorCategory.LEASE_LOST
    )

    with pytest.raises(ReverseConversionError):
        ReversionPublicationService(runtime).publish(executed, result, heartbeat)

    result_id = reversion_result_object_id(claimed.job.id, claimed.job.attempt)
    assert not objects.exists(
        ObjectKey(ObjectScope.REVERSION_RESULT, owner.id, result_id)
    )
    retained = repo.get_internal(claimed.job.id)
    assert retained is not None and retained.state is ReversionJobState.RUNNING


def test_worker_reconciles_and_completes_one_queued_job(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    job = _queue(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)

    assert ReversionWorker(runtime).run_once()

    retained = repo.get_internal(job.id)
    assert retained is not None and retained.state is ReversionJobState.SUCCEEDED
    cast(Any, runtime.reconciler).reconcile.assert_called_once()
    assert not ReversionWorker(runtime).run_once()


def test_recovery_replays_create_and_requires_proof_before_requeue(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    repo.reserve_create_intent(
        claimed.job.id,
        claimed.attempt_id,
        runtime.worker_id,
        claimed.lease_token,
        BROKER_POLICY.revision,
        policy_specification_evidence(BROKER_POLICY),
        NOW,
    )
    unit_id = uuid4()
    broker = cast(Any, runtime.broker)

    def request(message: object) -> object:
        if type(message) is CreateRequest:
            return CreateResponse(
                message.request_id,
                message.attempt_id,
                unit_id,
                ManagedUnitState.CREATED,
            )
        if type(message) is TerminateRequest:
            return TerminateResponse(
                message.request_id, _proof(message.attempt_id, message.unit_id)
            )
        assert type(message) is AcknowledgeRequest
        return AcknowledgeResponse(
            message.request_id,
            message.attempt_id,
            message.unit_id,
            message.proof_id,
            True,
        )

    broker.request.side_effect = request
    cast(Any, runtime.reconciler).reconcile.side_effect = [
        ReversionProofRequiredError(),
        None,
    ]
    recovered_at = LEASE_END + timedelta(seconds=1)
    cast(Any, runtime.clock).return_value = recovered_at

    assert ReversionWorker(runtime).recover() == 1

    retained = repo.get_internal(claimed.job.id)
    assert retained is not None and retained.state is ReversionJobState.QUEUED
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.proof_acknowledged_at == recovered_at
    assert [type(call.args[0]) for call in broker.request.call_args_list] == [
        CreateRequest,
        TerminateRequest,
        AcknowledgeRequest,
    ]
    assert cast(Any, runtime.reconciler).reconcile.call_count == 2


def test_cleanup_deletes_reverse_objects_and_expires_job(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    job = _queue(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    worker = ReversionWorker(runtime)
    assert worker.run_once()
    retained = repo.get_internal(job.id)
    assert retained is not None and retained.result_object_id is not None
    source_key = ObjectKey(
        ObjectScope.REVERSION_UPLOAD, owner.id, retained.source_object_id
    )
    result_key = ObjectKey(
        ObjectScope.REVERSION_RESULT, owner.id, retained.result_object_id
    )
    assert objects.exists(source_key) and objects.exists(result_key)
    cast(Any, runtime.clock).return_value = RETENTION_END

    assert worker.cleanup() == 1

    assert not objects.exists(source_key) and not objects.exists(result_key)
    expired = repo.get_internal(job.id)
    assert expired is not None and expired.state is ReversionJobState.EXPIRED


@pytest.mark.parametrize("boundary", ["create", "stage", "collect", "terminate"])
def test_executor_rejects_misbound_broker_responses(
    boundary: str,
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    broker = cast(Any, runtime.broker)
    original_request = broker.request.side_effect
    original_stage = broker.stage_workspace.side_effect
    original_collect = broker.collect_workspace.side_effect

    if boundary == "create":
        broker.request.side_effect = lambda message: (
            replace(original_request(message), request_id=uuid4())
            if type(message) is CreateRequest
            else original_request(message)
        )
    elif boundary == "stage":
        broker.stage_workspace.side_effect = lambda message: replace(
            original_stage(message), request_id=uuid4()
        )
    elif boundary == "collect":
        broker.collect_workspace.side_effect = lambda message: replace(
            original_collect(message), request_id=uuid4()
        )
    else:
        broker.request.side_effect = lambda message: (
            replace(original_request(message), request_id=uuid4())
            if type(message) is TerminateRequest
            else original_request(message)
        )

    with pytest.raises(ReverseConversionError) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None
    if boundary in {"stage", "collect"}:
        assert attempt.termination_proof is not None
    else:
        assert attempt.termination_proof is None


def test_heartbeat_reports_duration_shutdown_cancellation_and_lease_loss(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    cast(Any, runtime.monotonic_clock).return_value = (
        claimed.started_monotonic + POLICY.max_job_duration_seconds
    )
    with pytest.raises(ReverseConversionError) as timed_out:
        ReversionHeartbeat(runtime, claimed).raise_if_interrupted()
    assert timed_out.value.category is ReverseErrorCategory.TIMED_OUT

    cast(Any, runtime.monotonic_clock).return_value = claimed.started_monotonic
    shutdown_runtime = replace(runtime, shutdown_requested=lambda: True)
    with pytest.raises(ReversionWorkerInterruptedError):
        ReversionHeartbeat(shutdown_runtime, claimed).raise_if_interrupted()

    repo.request_cancel(claimed.job.id, owner.id, NOW, RETENTION_END)
    with pytest.raises(ReverseConversionError) as cancelled:
        ReversionHeartbeat(runtime, claimed).raise_if_interrupted()
    assert cancelled.value.category is ReverseErrorCategory.CANCELLED

    mocker.patch.object(repo, "heartbeat", return_value=False)
    with pytest.raises(ReversionJobLeaseLostError):
        ReversionHeartbeat(runtime, claimed).progress(ReversionJobStep.CONVERTING)


def test_worker_maps_child_failure_to_safe_terminal_state_and_acks(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    job = _queue(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    broker = cast(Any, runtime.broker)
    receipt: WorkspaceStageReceipt | None = None

    def stage(message: WorkspaceStageRequest) -> WorkspaceStageReceipt:
        nonlocal receipt
        receipt = WorkspaceStageReceipt(
            message.request_id,
            message.sequence,
            message.attempt_id,
            message.unit_id,
            message.create_sequence,
            uuid4(),
        )
        return receipt

    def collect(message: WorkspaceCollectRequest) -> WorkspaceFailureResponse:
        assert receipt is not None
        return WorkspaceFailureResponse(
            message.request_id, receipt, ReverseErrorCategory.MALFORMED
        )

    broker.stage_workspace.side_effect = stage
    broker.collect_workspace.side_effect = collect

    assert ReversionWorker(runtime).run_once()

    retained = repo.get_internal(job.id)
    assert retained is not None and retained.state is ReversionJobState.FAILED
    assert retained.error_code == ReverseErrorCategory.MALFORMED.value
    attempts = repo.list_attempts(job.id)
    assert len(attempts) == 1 and attempts[0].proof_acknowledged_at == NOW


def test_publication_retains_committed_result_when_ack_fails(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    heartbeat = mocker.Mock()
    executed = ReversionAttemptExecutor(runtime).execute(claimed, heartbeat)
    result = validate_reverse_result(
        claimed.job,
        executed.result.mode,
        executed.result.result,
        CONTENT_LIMITS,
    )
    cast(Any, runtime.broker).request.side_effect = BrokerError(
        BrokerErrorCategory.TRANSPORT_FAILURE
    )

    with pytest.raises(BrokerError):
        ReversionPublicationService(runtime).publish(executed, result, heartbeat)

    result_id = reversion_result_object_id(claimed.job.id, claimed.job.attempt)
    assert objects.exists(ObjectKey(ObjectScope.REVERSION_RESULT, owner.id, result_id))
    retained = repo.get_internal(claimed.job.id)
    assert retained is not None and retained.state is ReversionJobState.SUCCEEDED
    attempt = repo.get_attempt(claimed.attempt_id)
    assert attempt is not None and attempt.proof_acknowledged_at is None


def test_publication_recovers_exact_commit_after_lost_success_response(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    objects = FilesystemObjectStore(tmp_path)
    runtime = _runtime(mocker, repo, objects)
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    heartbeat = mocker.Mock()
    executed = ReversionAttemptExecutor(runtime).execute(claimed, heartbeat)
    result = validate_reverse_result(
        claimed.job,
        executed.result.mode,
        executed.result.result,
        CONTENT_LIMITS,
    )
    succeed = repo.succeed

    def lose_response(*args: Any, **kwargs: Any) -> object:
        succeed(*args, **kwargs)
        raise ReversionJobRepositoryError("response lost after commit")

    mocker.patch.object(repo, "succeed", side_effect=lose_response)

    published = ReversionPublicationService(runtime).publish(
        executed, result, heartbeat
    )

    result_id = reversion_result_object_id(claimed.job.id, claimed.job.attempt)
    assert published.result_object_id == result_id
    assert objects.exists(ObjectKey(ObjectScope.REVERSION_RESULT, owner.id, result_id))
    retained = repo.get_internal(claimed.job.id)
    assert retained is not None and retained.state is ReversionJobState.SUCCEEDED


def test_worker_does_not_reconcile_or_claim_after_shutdown(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    job = _queue(runtime, repo, owner, b"source")
    stopped = replace(runtime, shutdown_requested=lambda: True)

    assert not ReversionWorker(stopped).run_once()

    retained = repo.get_internal(job.id)
    assert retained is not None and retained.state is ReversionJobState.QUEUED
    cast(Any, runtime.reconciler).reconcile.assert_not_called()


def test_claim_identity_and_latched_lease_loss_fail_closed(
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    with pytest.raises(ValueError):
        replace(claimed, attempt_id=cast(Any, "invalid"))
    mocker.patch.object(repo, "heartbeat", return_value=False)
    heartbeat = ReversionHeartbeat(runtime, claimed)
    with pytest.raises(ReversionJobLeaseLostError):
        heartbeat.progress(ReversionJobStep.CONVERTING)
    with pytest.raises(ReversionJobLeaseLostError):
        heartbeat.raise_if_interrupted()


@pytest.mark.parametrize(
    ("boundary", "expected"),
    [
        ("source_limit", ReverseConversionError),
        ("create_error", BrokerError),
        ("stage_error", BrokerError),
        ("collect_error", BrokerError),
    ],
)
def test_executor_maps_bounded_storage_and_broker_errors(
    boundary: str,
    expected: type[BaseException],
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    broker = cast(Any, runtime.broker)
    if boundary == "source_limit":
        mocker.patch.object(
            runtime.objects,
            "get_bounded",
            side_effect=ObjectTooLargeError("bounded"),
        )
    elif boundary == "create_error":
        broker.request.side_effect = lambda message: (
            ErrorResponse(
                message.request_id,
                BrokerOperation.CREATE,
                BrokerErrorCategory.RUNTIME_FAILURE,
            )
            if type(message) is CreateRequest
            else None
        )
    elif boundary == "stage_error":
        broker.stage_workspace.side_effect = lambda message: WorkspaceErrorResponse(
            message.request_id,
            WorkspaceOperation.STAGE,
            BrokerErrorCategory.RUNTIME_FAILURE,
        )
    else:
        broker.collect_workspace.side_effect = lambda message: WorkspaceErrorResponse(
            message.request_id,
            WorkspaceOperation.COLLECT,
            BrokerErrorCategory.RUNTIME_FAILURE,
        )

    with pytest.raises(expected) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    if boundary == "source_limit":
        assert (
            cast(ReverseConversionError, captured.value).category
            is ReverseErrorCategory.RESOURCE_LIMIT
        )
        broker.request.assert_not_called()
    elif boundary in {"stage_error", "collect_error"}:
        attempt = repo.get_attempt(claimed.attempt_id)
        assert attempt is not None and attempt.termination_proof is not None


@pytest.mark.parametrize("boundary", ["control", "workspace"])
def test_executor_rejects_misbound_error_responses(
    boundary: str,
    tmp_path: Path,
    repository: tuple[SqlReversionJobRepository, User, Engine],
    mocker: MockerFixture,
) -> None:
    repo, owner, _engine = repository
    runtime = _runtime(mocker, repo, FilesystemObjectStore(tmp_path))
    claimed = _claim(runtime, repo, owner, b"source")
    _successful_broker(mocker, runtime)
    broker = cast(Any, runtime.broker)
    if boundary == "control":
        broker.request.side_effect = lambda message: ErrorResponse(
            message.request_id,
            BrokerOperation.TERMINATE,
            BrokerErrorCategory.RUNTIME_FAILURE,
        )
    else:
        broker.stage_workspace.side_effect = lambda message: WorkspaceErrorResponse(
            message.request_id,
            WorkspaceOperation.COLLECT,
            BrokerErrorCategory.RUNTIME_FAILURE,
        )

    with pytest.raises(ReverseConversionError) as captured:
        ReversionAttemptExecutor(runtime).execute(claimed, mocker.Mock())

    assert captured.value.category is ReverseErrorCategory.PROTOCOL_ERROR
