"""Trusted external isolation-broker domain contract with lazy transports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from markweave.broker.errors import BrokerError, BrokerErrorCategory
    from markweave.broker.models import (
        AuthenticatedPrincipal,
        BrokerPolicy,
        EvidenceDigest,
        ManagedUnit,
        ManagedUnitState,
        ReplayPosition,
        RuntimeChannelLimits,
        RuntimeIncarnation,
        RuntimeLimits,
        TerminationProof,
        is_next_unit_state,
        policy_specification_evidence,
    )
    from markweave.broker.mtls_transport import (
        MTLS_ALPN,
        MTLS_PROTOCOL_NAME,
        MTLS_PROTOCOL_VERSION,
        MtlsBrokerClient,
        MtlsBrokerServer,
        MtlsEndpoint,
        MtlsLocalIdentity,
        MtlsPeerIdentity,
        MtlsServerContext,
        MtlsTransportLimits,
        build_mtls_server_context,
        leaf_certificate_sha256,
    )
    from markweave.broker.podman_runtime import (
        BoundedCommandRunner,
        PodmanCommandLimits,
        PodmanIsolationRuntime,
        PodmanRuntimeError,
        PodmanRuntimeUnit,
        SystemdCgroupRemover,
    )
    from markweave.broker.unix_transport import (
        UnixBrokerClient,
        UnixBrokerServer,
        UnixTransportLimits,
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

__all__ = [
    "MTLS_ALPN",
    "MTLS_PROTOCOL_NAME",
    "MTLS_PROTOCOL_VERSION",
    "AuthenticatedPrincipal",
    "BoundedCommandRunner",
    "BrokerError",
    "BrokerErrorCategory",
    "BrokerPolicy",
    "EvidenceDigest",
    "ManagedUnit",
    "ManagedUnitState",
    "MtlsBrokerClient",
    "MtlsBrokerServer",
    "MtlsEndpoint",
    "MtlsLocalIdentity",
    "MtlsPeerIdentity",
    "MtlsServerContext",
    "MtlsTransportLimits",
    "PodmanCommandLimits",
    "PodmanIsolationRuntime",
    "PodmanRuntimeError",
    "PodmanRuntimeUnit",
    "ReplayPosition",
    "RuntimeChannelLimits",
    "RuntimeIncarnation",
    "RuntimeLimits",
    "SystemdCgroupRemover",
    "TerminationProof",
    "UnixBrokerClient",
    "UnixBrokerServer",
    "UnixTransportLimits",
    "WorkspaceCollectRequest",
    "WorkspaceErrorResponse",
    "WorkspaceFailureResponse",
    "WorkspacePendingResponse",
    "WorkspaceStageReceipt",
    "WorkspaceStageRequest",
    "WorkspaceSuccessResponse",
    "build_mtls_server_context",
    "is_next_unit_state",
    "leaf_certificate_sha256",
    "policy_specification_evidence",
]

_EXPORT_MODULES = {
    "BrokerError": "errors",
    "BrokerErrorCategory": "errors",
    **dict.fromkeys(
        (
            "AuthenticatedPrincipal",
            "BrokerPolicy",
            "EvidenceDigest",
            "ManagedUnit",
            "ManagedUnitState",
            "ReplayPosition",
            "RuntimeChannelLimits",
            "RuntimeIncarnation",
            "RuntimeLimits",
            "TerminationProof",
            "is_next_unit_state",
            "policy_specification_evidence",
        ),
        "models",
    ),
    **dict.fromkeys(
        (
            "MTLS_ALPN",
            "MTLS_PROTOCOL_NAME",
            "MTLS_PROTOCOL_VERSION",
            "MtlsBrokerClient",
            "MtlsBrokerServer",
            "MtlsEndpoint",
            "MtlsLocalIdentity",
            "MtlsPeerIdentity",
            "MtlsServerContext",
            "MtlsTransportLimits",
            "build_mtls_server_context",
            "leaf_certificate_sha256",
        ),
        "mtls_transport",
    ),
    **dict.fromkeys(
        (
            "BoundedCommandRunner",
            "PodmanCommandLimits",
            "PodmanRuntimeError",
        ),
        "command_runner",
    ),
    **dict.fromkeys(
        ("PodmanIsolationRuntime", "PodmanRuntimeUnit", "SystemdCgroupRemover"),
        "podman_runtime",
    ),
    **dict.fromkeys(
        ("UnixBrokerClient", "UnixBrokerServer", "UnixTransportLimits"),
        "unix_transport",
    ),
    **dict.fromkeys(
        (
            "WorkspaceCollectRequest",
            "WorkspaceErrorResponse",
            "WorkspaceFailureResponse",
            "WorkspacePendingResponse",
            "WorkspaceStageReceipt",
            "WorkspaceStageRequest",
            "WorkspaceSuccessResponse",
        ),
        "workspace_protocol",
    ),
}


def __getattr__(name: str) -> Any:
    """Load only the broker component requested by the caller."""

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the stable public surface without importing every transport."""

    return sorted((*globals(), *__all__))
