"""Injected policy and runtime dependencies for reverse-job supervision."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerClient,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
)
from markweave.broker.protocol import BrokerRequest, BrokerResponse
from markweave.broker.reconciliation_protocol import (
    ReconciliationRequest,
    ReconciliationResult,
)
from markweave.broker.unix_transport import UnixBrokerClient
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceResponse,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
)
from markweave.config import ReversionBrokerTransport, Settings
from markweave.observability import OperationalMetrics
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
    running_limit: int = 1

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
        if any(
            type(value) not in {int, float} or not isfinite(value) or value <= 0
            for value in durations
        ):
            raise ValueError("Reverse worker durations must be positive")
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.recovery_batch_size,
                self.cleanup_batch_size,
                self.running_limit,
            )
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
    require_ready: bool = False
    metrics: OperationalMetrics | None = None

    def __post_init__(self) -> None:
        if (
            type(self.principal) is not AuthenticatedPrincipal
            or type(self.broker_policy) is not BrokerPolicy
            or type(self.content_limits) is not ReverseContentLimits
            or type(self.policy) is not ReversionWorkerPolicy
            or type(self.worker_id) is not str
            or not self.worker_id.strip()
            or len(self.worker_id) > MAX_WORKER_ID_CHARACTERS
            or type(self.require_ready) is not bool
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


@dataclass(frozen=True, slots=True)
class ReversionExecutionPolicies:
    """Validated production reverse policies assembled from explicit settings."""

    principal: AuthenticatedPrincipal
    broker_policy: BrokerPolicy
    content_limits: ReverseContentLimits
    worker: ReversionWorkerPolicy
    reconciliation_ack_batch_size: int
    cleanup_interval_seconds: float
    error_backoff_seconds: float


def _required[T](value: T | None) -> T:
    if value is None:
        raise ValueError("Reverse worker configuration is incomplete")
    return value


def build_reversion_execution_policies(
    settings: Settings,
) -> ReversionExecutionPolicies:
    """Translate the complete optional settings group without choosing values."""

    if not settings.reversion_execution_configured:
        raise ValueError("Reverse worker configuration is incomplete")
    channel = RuntimeChannelLimits(
        _required(settings.reversion_upload_max_bytes),
        _required(settings.reversion_output_max_bytes),
    )
    content = ReverseContentLimits(
        channel.max_input_bytes,
        channel.max_output_bytes,
        _required(settings.reversion_image_max_source_bytes),
        _required(settings.reversion_image_max_width_pixels),
        _required(settings.reversion_image_max_height_pixels),
        _required(settings.reversion_image_max_pixels),
        _required(settings.reversion_image_max_svg_elements),
        _required(settings.reversion_image_max_svg_depth),
        _required(settings.reversion_asset_max_count),
        _required(settings.reversion_asset_max_total_source_bytes),
        _required(settings.reversion_asset_max_total_output_bytes),
        _required(settings.reversion_markdown_max_bytes),
        _required(settings.reversion_package_max_bytes),
    )
    broker_policy = BrokerPolicy(
        _required(settings.reversion_broker_policy_revision),
        _required(settings.reversion_broker_image_digest),
        RuntimeLimits(
            _required(settings.reversion_cpu_quota_micros),
            _required(settings.reversion_cpu_period_micros),
            _required(settings.reversion_memory_bytes),
            _required(settings.reversion_pid_limit),
            _required(settings.reversion_workspace_bytes),
            _required(settings.reversion_wall_time_millis),
        ),
        channel,
    )
    worker = ReversionWorkerPolicy(
        _required(settings.reversion_worker_lease_seconds),
        _required(settings.reversion_worker_heartbeat_seconds),
        _required(settings.reversion_worker_max_duration_seconds),
        _required(settings.reversion_result_retention_seconds),
        _required(settings.reversion_worker_incomplete_submission_seconds),
        _required(settings.reversion_worker_collect_poll_seconds),
        _required(settings.reversion_worker_recovery_lease_seconds),
        _required(settings.reversion_worker_recovery_batch_size),
        _required(settings.reversion_worker_cleanup_lease_seconds),
        _required(settings.reversion_worker_cleanup_batch_size),
        _required(settings.reversion_running_limit),
    )
    return ReversionExecutionPolicies(
        AuthenticatedPrincipal(_required(settings.reversion_broker_principal_id)),
        broker_policy,
        content,
        worker,
        _required(settings.reversion_worker_reconciliation_ack_batch_size),
        _required(settings.reversion_worker_cleanup_interval_seconds),
        _required(settings.reversion_worker_error_backoff_seconds),
    )


def build_reversion_broker_client(
    settings: Settings, policies: ReversionExecutionPolicies
) -> ReversionBrokerClient:
    """Construct the selected authenticated broker client."""

    timeout = _required(settings.reversion_broker_operation_timeout_seconds)
    if settings.reversion_broker_transport is ReversionBrokerTransport.UNIX:
        return UnixBrokerClient(
            _required(settings.reversion_broker_socket_path),
            expected_server_uid=os.geteuid(),
            expected_principal=policies.principal,
            operation_timeout_seconds=timeout,
            workspace_limits=policies.broker_policy.channel_limits,
        )
    local = MtlsLocalIdentity(
        cast(Path, settings.reversion_broker_ca_certificate_path),
        cast(Path, settings.reversion_broker_certificate_chain_path),
        cast(Path, settings.reversion_broker_private_key_path),
        _required(settings.reversion_broker_worker_uri_san),
        policies.principal,
    )
    server = MtlsPeerIdentity(
        _required(settings.reversion_broker_server_uri_san),
        _required(settings.reversion_broker_server_leaf_sha256),
        AuthenticatedPrincipal(
            _required(settings.reversion_broker_server_principal_id)
        ),
    )
    return MtlsBrokerClient(
        MtlsEndpoint(
            _required(settings.reversion_broker_endpoint_host),
            _required(settings.reversion_broker_endpoint_port),
        ),
        local_identity=local,
        server_identity=server,
        operation_timeout_seconds=timeout,
        workspace_limits=policies.broker_policy.channel_limits,
    )
