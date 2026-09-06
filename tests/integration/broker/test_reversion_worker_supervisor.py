"""Real Unix broker integration for the durable reverse supervisor."""

from __future__ import annotations

import os
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
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
from markweave.persistence.schema import Base
from markweave.persistence.sql import SqlUserRepository
from markweave.reversion_jobs.models import ReversionJob, ReversionJobState
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler
from markweave.reversion_jobs.runtime import ReversionWorkerRuntime
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.reversions.models import ReverseAttemptSuccess, ReverseOutputMode
from markweave.storage import FilesystemObjectStore, ObjectKey, ObjectScope
from tests.reversion_job_repository_contracts import (
    LEASE_END,
    NOW,
    PRINCIPAL,
    submission,
)
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


def _repository() -> tuple[SqlReversionJobRepository, User, Engine]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    users = SqlUserRepository(engine)
    owner = User(uuid4(), "Owner", "owner", "hash:user", Role.USER)
    users.create(owner)
    return SqlReversionJobRepository(engine), owner, engine


def _queue(
    repository: SqlReversionJobRepository,
    objects: FilesystemObjectStore,
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
