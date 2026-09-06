"""Real Unix broker integration for the durable reverse supervisor."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine

from markweave.auth.models import Role, User
from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.fake_runtime import FakeIsolationRuntime, FakeRuntimeUnit
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    BrokerPolicy,
    ManagedUnit,
    policy_specification_evidence,
)
from markweave.broker.service import IsolationBrokerService
from markweave.broker.unix_transport import (
    UnixBrokerClient,
    UnixBrokerServer,
    UnixTransportLimits,
)
from markweave.http.components import build_components
from markweave.jobs.models import (
    ConversionJob,
    JobOutput,
    JobProcessResult,
    JobState,
    JobStep,
    JobSubmission,
)
from markweave.jobs.ports import CancellationProbe
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.schema import Base
from markweave.persistence.sql import SqlUserRepository
from markweave.reversion_jobs.models import ReversionJob, ReversionJobState
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.reversions.models import ReverseAttemptSuccess, ReverseOutputMode
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope, ObjectStore
from markweave.templates.models import TemplateVersion
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    PRINCIPAL,
    submission,
)
from tests.unit.reversions.test_reversion_runtime_assembly import _settings
from tests.unit.reversions.test_reversion_worker_runtime import (
    BROKER_POLICY,
    CONTENT_LIMITS,
    POLICY,
)

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


class _SuccessfulRuntime(FakeIsolationRuntime):
    """Publish one deterministic child result immediately after real CREATE."""

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> FakeRuntimeUnit:
        created = super().create(unit, policy)
        self.publish_response(
            created.unit_id,
            ReverseAttemptSuccess(
                unit.attempt_id, ReverseOutputMode.MARKDOWN, b"# Result\n"
            ),
        )
        return created


class _ResultProcessor:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def _result(self) -> JobProcessResult:
        self._events.append("forward")
        return JobProcessResult(b"forward-result")

    def process_without_template(
        self,
        job: ConversionJob,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        assert not cancelled()
        assert deadline_monotonic is not None
        progress(JobStep.DOCX, 70)
        return self._result()

    def process_with_template(  # noqa: PLR0913 - production boundary contract
        self,
        job: ConversionJob,
        template: TemplateVersion,
        template_content: bytes,
        *,
        cancelled: CancellationProbe,
        deadline_monotonic: float | None,
        progress: Callable[[JobStep, int], None],
    ) -> JobProcessResult:
        assert job.state is JobState.RUNNING
        assert template_content
        assert template.id == job.template_version_id
        assert not cancelled()
        assert deadline_monotonic is not None
        progress(JobStep.DOCX, 70)
        return self._result()


class _Until:
    def __init__(self, predicate: Callable[[], bool]) -> None:
        self._predicate = predicate

    def is_set(self) -> bool:
        return bool(self._predicate())

    def wait(self, timeout: float) -> bool:
        del timeout
        return self.is_set()


def _repository() -> tuple[SqlReversionJobRepository, User, Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:user", Role.USER)
    users.create(owner)
    return SqlReversionJobRepository(engine), owner, engine


def _queue(
    repository: SqlReversionJobRepository,
    objects: ObjectStore,
    owner: User,
) -> ReversionJob:
    source = b"source"
    queued, _ = repository.create(
        replace(
            submission(owner.id),
            source_sha256=sha256(source).hexdigest(),
            source_size=len(source),
        )
    )
    objects.put(
        ObjectKey(ObjectScope.REVERSION_UPLOAD, owner.id, queued.source_object_id),
        source,
    )
    return repository.activate_source(queued.id, NOW)


def _server(tmp_path: Path) -> tuple[UnixBrokerServer, UnixBrokerClient]:
    parent = tmp_path / "broker"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    path = parent / "broker.sock"
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3",
        b"integration-inventory-key-32bytes",
        max_records=32,
    )
    service = IsolationBrokerService(
        inventory,
        _SuccessfulRuntime(),
        BROKER_POLICY,
        max_discovered_units=32,
        unit_id_factory=uuid4,
    )
    server = UnixBrokerServer(
        path,
        expected_client_uid=os.geteuid(),
        principal=PRINCIPAL,
        dispatcher=BrokerDispatcher(service),
        limits=UnixTransportLimits(2, 4, 4, 8),
        workspace_limits=BROKER_POLICY.channel_limits,
    )
    client = UnixBrokerClient(
        path,
        expected_server_uid=os.geteuid(),
        expected_principal=PRINCIPAL,
        operation_timeout_seconds=2,
        workspace_limits=BROKER_POLICY.channel_limits,
    )
    return server, client


def _runtime(
    repository: SqlReversionJobRepository,
    objects: FilesystemObjectStore,
    client: UnixBrokerClient,
    clock: list[datetime],
) -> ReversionWorkerRuntime:
    reconciler = ReversionBrokerReconciler(
        repository, client, ack_batch_limit=8, request_id_factory=uuid4
    )
    return ReversionWorkerRuntime(
        repository,
        objects,
        client,
        reconciler,
        PRINCIPAL,
        BROKER_POLICY,
        CONTENT_LIMITS,
        POLICY,
        "integration-worker",
        lambda: clock[0],
        lambda: 1.0,
        lambda _seconds: False,
        lambda: False,
        uuid4,
        require_ready=True,
    )


def test_real_unix_supervisor_publishes_and_retires_exact_proof(
    tmp_path: Path,
) -> None:
    repository, owner, engine = _repository()
    objects = FilesystemObjectStore(tmp_path / "objects")
    server, client = _server(tmp_path)
    clock = [NOW]
    runtime = _runtime(repository, objects, client, clock)
    job = _queue(repository, objects, owner)
    try:
        with server:
            assert ReversionWorker(runtime).run_once()
        retained = repository.get_internal(job.id)
        assert retained is not None and retained.state is ReversionJobState.SUCCEEDED
        attempts = repository.list_attempts(job.id)
        assert len(attempts) == 1 and attempts[0].proof_acknowledged_at == NOW
    finally:
        engine.dispose()


def test_real_reconciler_unblocks_crash_before_create_replay(tmp_path: Path) -> None:
    repository, owner, engine = _repository()
    objects = FilesystemObjectStore(tmp_path / "objects")
    server, client = _server(tmp_path)
    clock = [NOW]
    runtime = _runtime(repository, objects, client, clock)
    job = _queue(repository, objects, owner)
    worker = ReversionWorker(runtime)
    try:
        with server:
            worker.reconcile()
            claimed = repository.claim(runtime.worker_id, PRINCIPAL, NOW, LEASE_END)
            assert claimed is not None
            assert (
                claimed.current_attempt_id is not None
                and claimed.lease_token is not None
            )
            repository.reserve_create_intent(
                claimed.id,
                claimed.current_attempt_id,
                runtime.worker_id,
                claimed.lease_token,
                BROKER_POLICY.revision,
                policy_specification_evidence(BROKER_POLICY),
                NOW,
            )
            clock[0] = LEASE_END + timedelta(seconds=1)
            assert worker.recover() == 1
        retained = repository.get_internal(job.id)
        assert retained is not None and retained.state is ReversionJobState.QUEUED
        attempts = repository.list_attempts(job.id)
        assert len(attempts) == 1 and attempts[0].proof_acknowledged_at == clock[0]
    finally:
        engine.dispose()


def test_production_components_keep_forward_live_across_unix_broker_reconnect(
    tmp_path: Path,
) -> None:
    server, _client = _server(tmp_path)
    settings = _settings(
        tmp_path,
        reversion_broker_socket_path=(tmp_path / "broker" / "broker.sock").resolve(),
        reversion_broker_principal_id=PRINCIPAL.principal_id,
        reversion_broker_policy_revision=BROKER_POLICY.revision,
        reversion_broker_image_digest=BROKER_POLICY.image_digest,
        reversion_cpu_quota_micros=BROKER_POLICY.limits.cpu_quota_micros,
        reversion_cpu_period_micros=BROKER_POLICY.limits.cpu_period_micros,
        reversion_memory_bytes=BROKER_POLICY.limits.memory_bytes,
        reversion_pid_limit=BROKER_POLICY.limits.pid_limit,
        reversion_workspace_bytes=BROKER_POLICY.limits.workspace_bytes,
        reversion_wall_time_millis=BROKER_POLICY.limits.wall_time_millis,
        reversion_running_limit=1,
    )
    components = build_components(settings)
    engine = components.owned_engines[0]
    owner = User(uuid4(), "Production", "production", "hash:user", Role.USER)
    SqlUserRepository(engine).create(owner)
    reverse_repository = components.reversion_repository
    forward_repository = components.job_repository
    queue_observer = components.queue_observer
    assert (
        reverse_repository is not None
        and forward_repository is not None
        and queue_observer is not None
    )
    durable_reverse_repository: SqlReversionJobRepository = reverse_repository
    reverse_jobs = tuple(
        _queue(durable_reverse_repository, components.object_store, owner)
        for _ in range(2)
    )
    source = b"forward-source"
    source_object_id = uuid4()
    forward, _ = forward_repository.create(
        JobSubmission(
            uuid4(),
            owner.id,
            source_object_id,
            None,
            None,
            JobOutput.DOCX,
            (("markweave", "0.6.1"),),
            sha256(source).hexdigest(),
            None,
            NOW,
        )
    )
    components.object_store.put(
        ObjectKey(ObjectScope.UPLOAD, owner.id, source_object_id), source
    )
    forward_repository.activate_source(forward.id, NOW)
    events: list[str] = []

    def forward_finished_with_broker_fault() -> bool:
        current = forward_repository.get(forward.id)
        return (
            current is not None
            and current.state is JobState.SUCCEEDED
            and "md_converter_reversion_broker_fault 1"
            in components.metrics.render(queue_observer.observe_queue(NOW))
        )

    def reverse_finished() -> bool:
        current = tuple(
            durable_reverse_repository.get_internal(job.id) for job in reverse_jobs
        )
        return all(
            job is not None and job.state is ReversionJobState.SUCCEEDED
            for job in current
        )

    try:
        unavailable_loop = components.build_external_worker_loop(
            worker_id="production-worker",
            processor=_ResultProcessor(events),
            clock=lambda: NOW,
        )
        unavailable_loop.run(_Until(forward_finished_with_broker_fault))
        assert events == ["forward"]

        with server:
            connected_loop = components.build_external_worker_loop(
                worker_id="production-worker",
                processor=_ResultProcessor(events),
                clock=lambda: NOW,
            )
            connected_loop.run(_Until(reverse_finished))
        assert reverse_finished()
        assert "md_converter_reversion_reconciliation_ready 1" in (
            components.metrics.render(queue_observer.observe_queue(NOW))
        )
    finally:
        components.close()
