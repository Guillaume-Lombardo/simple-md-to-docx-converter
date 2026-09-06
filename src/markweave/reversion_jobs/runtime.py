"""Injected policy and runtime dependencies for reverse-job supervision."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from markweave.broker.models import AuthenticatedPrincipal, BrokerPolicy
from markweave.broker.protocol import BrokerRequest, BrokerResponse
from markweave.broker.reconciliation_protocol import (
    ReconciliationRequest,
    ReconciliationResult,
)
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
)
from markweave.reversion_jobs.models import MAX_WORKER_ID_CHARACTERS
from markweave.reversion_jobs.ports import ReversionWorkerRepository
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler
from markweave.reversions.models import ReverseContentLimits
from markweave.storage import BoundedObjectStore


class ReversionBrokerClient(Protocol):  # pragma: no cover - structural port
    """Common lifecycle, workspace, and reconciliation client surface."""

    def request(self, request: BrokerRequest) -> BrokerResponse: ...

    def stage_workspace(
        self, request: WorkspaceStageRequest
    ) -> WorkspaceStageReceipt | WorkspaceResponse: ...

    def collect_workspace(
        self, request: WorkspaceCollectRequest
    ) -> WorkspaceResponse: ...

    def reconcile(self, request: ReconciliationRequest) -> ReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class ReversionWorkerPolicy:
    """Caller-owned timings and batch bounds with no production defaults."""

    lease_seconds: float
    heartbeat_seconds: float
    max_job_duration_seconds: float
    result_retention_seconds: float
    incomplete_submission_seconds: float
    collect_poll_seconds: float
    recovery_lease_seconds: float
    recovery_batch_size: int
    cleanup_lease_seconds: float
    cleanup_batch_size: int

    def __post_init__(self) -> None:
        durations = (
            self.lease_seconds,
            self.heartbeat_seconds,
            self.max_job_duration_seconds,
            self.result_retention_seconds,
            self.incomplete_submission_seconds,
            self.collect_poll_seconds,
            self.recovery_lease_seconds,
            self.cleanup_lease_seconds,
        )
        if any(type(value) not in {int, float} or value <= 0 for value in durations):
            raise ValueError("Reverse worker durations must be positive")
        if any(
            type(value) is not int or value <= 0
            for value in (self.recovery_batch_size, self.cleanup_batch_size)
        ):
            raise ValueError("Reverse worker batch sizes must be positive integers")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("Reverse heartbeat must be shorter than its lease")
        if self.collect_poll_seconds > self.heartbeat_seconds:
            raise ValueError(
                "Reverse collection polling must not exceed heartbeat timing"
            )


@dataclass(frozen=True, slots=True)
class ReversionWorkerRuntime:
    """Complete injected supervisor boundary independent of HTTP settings."""

    repository: ReversionWorkerRepository
    objects: BoundedObjectStore
    broker: ReversionBrokerClient
    reconciler: ReversionBrokerReconciler
    principal: AuthenticatedPrincipal
    broker_policy: BrokerPolicy
    content_limits: ReverseContentLimits
    policy: ReversionWorkerPolicy
    worker_id: str
    clock: Callable[[], datetime]
    monotonic_clock: Callable[[], float]
    wait: Callable[[float], bool]
    shutdown_requested: Callable[[], bool]
    request_id_factory: Callable[[], UUID]

    def __post_init__(self) -> None:
        if (
            type(self.principal) is not AuthenticatedPrincipal
            or type(self.broker_policy) is not BrokerPolicy
            or type(self.content_limits) is not ReverseContentLimits
            or type(self.policy) is not ReversionWorkerPolicy
            or type(self.worker_id) is not str
            or not self.worker_id.strip()
            or len(self.worker_id) > MAX_WORKER_ID_CHARACTERS
        ):
            raise ValueError("Reverse worker runtime identity is invalid")
        if any(
            not callable(value)
            for value in (
                self.clock,
                self.monotonic_clock,
                self.wait,
                self.shutdown_requested,
                self.request_id_factory,
            )
        ):
            raise ValueError("Reverse worker runtime callables are invalid")
