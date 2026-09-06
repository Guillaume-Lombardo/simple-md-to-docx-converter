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
    WorkspaceFailureResponse,
    WorkspacePendingResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.schema import Base
from markweave.persistence.sql import SqlUserRepository
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
)
from markweave.reversion_jobs.worker_publication import ReversionPublicationService
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.models import ReverseOutputMode
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope
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
        lambda: 1.0,
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
