"""Closed canonical node-attester messages and runtime identity binding."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import cast
from uuid import UUID

from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
)
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    RuntimeChannelLimits,
    RuntimeLimits,
)

_PROTOCOL = "markweave-kubernetes-node-attester"

_VERSION = 1


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):  # fmt: skip
        raise KubernetesRuntimeError("Kubernetes attester message is invalid") from None


def _decode(value: bytes) -> Mapping[str, object]:
    if type(value) is not bytes:
        raise KubernetesRuntimeError("Kubernetes attester message is invalid")
    try:
        result = json.loads(value.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):  # fmt: skip
        raise KubernetesRuntimeError("Kubernetes attester message is invalid") from None
    if not isinstance(result, Mapping) or any(type(key) is not str for key in result):
        raise KubernetesRuntimeError("Kubernetes attester message is invalid")
    return cast(Mapping[str, object], result)


def _pod_mapping(value: KubernetesPodIdentity) -> dict[str, object]:
    return {
        "attempt_id": str(value.attempt_id),
        "name": value.name,
        "namespace": value.namespace,
        "node_name": value.node_name,
        "pod_uid": str(value.pod_uid),
        "policy_revision": value.policy_revision,
        "policy_specification": value.policy_specification.value,
        "principal_id": str(value.principal_id),
        "unit_id": str(value.unit_id),
    }


def _pod(value: object) -> KubernetesPodIdentity:
    mapping = _closed_mapping(
        value,
        {
            "attempt_id",
            "name",
            "namespace",
            "node_name",
            "pod_uid",
            "policy_revision",
            "policy_specification",
            "principal_id",
            "unit_id",
        },
    )
    return KubernetesPodIdentity(
        _required_text(mapping, "namespace"),
        _required_text(mapping, "name"),
        UUID(_required_text(mapping, "pod_uid")),
        _required_text(mapping, "node_name"),
        UUID(_required_text(mapping, "unit_id")),
        UUID(_required_text(mapping, "attempt_id")),
        UUID(_required_text(mapping, "principal_id")),
        _required_text(mapping, "policy_revision"),
        EvidenceDigest(_required_text(mapping, "policy_specification")),
    )


def _sandbox_mapping(value: KubernetesSandboxIdentity) -> dict[str, object]:
    return {
        "cgroup_path": value.cgroup_path,
        "container_ids": list(value.container_ids),
        "node_fence": value.node_fence.value,
        "node_uid": str(value.node_uid),
        "sandbox_id": value.sandbox_id,
    }


def _sandbox(value: object) -> KubernetesSandboxIdentity:
    mapping = _closed_mapping(
        value,
        {"cgroup_path", "container_ids", "node_fence", "node_uid", "sandbox_id"},
    )
    container_ids = mapping["container_ids"]
    if type(container_ids) is not list or any(
        type(item) is not str for item in container_ids
    ):
        raise KubernetesRuntimeError("Kubernetes attester message is invalid")
    return KubernetesSandboxIdentity(
        _required_text(mapping, "sandbox_id"),
        _required_text(mapping, "cgroup_path"),
        UUID(_required_text(mapping, "node_uid")),
        EvidenceDigest(_required_text(mapping, "node_fence")),
        tuple(cast(list[str], container_ids)),
    )


def _contract_mapping(value: KubernetesAttestationContract) -> dict[str, object]:
    return {
        "fence_revision": value.fence_revision,
        "manifest_digest": value.manifest_digest.value,
        "pod_contract": dict(value.pod_contract),
        "policy": asdict(value.policy),
        "pool": value.pool,
    }


def _contract(value: object) -> KubernetesAttestationContract:
    mapping = _closed_mapping(
        value,
        {"fence_revision", "manifest_digest", "pod_contract", "policy", "pool"},
    )
    policy = _closed_mapping(
        mapping["policy"],
        {"channel_limits", "image_digest", "limits", "revision"},
    )
    limits = _closed_mapping(
        policy["limits"],
        {
            "cpu_period_micros",
            "cpu_quota_micros",
            "memory_bytes",
            "pid_limit",
            "wall_time_millis",
            "workspace_bytes",
        },
    )
    channel = _closed_mapping(
        policy["channel_limits"], {"max_input_bytes", "max_output_bytes"}
    )
    pod_contract = mapping["pod_contract"]
    if not isinstance(pod_contract, Mapping):
        raise ValueError
    return KubernetesAttestationContract(
        cast(Mapping[str, object], pod_contract),
        EvidenceDigest(_required_text(mapping, "manifest_digest")),
        BrokerPolicy(
            _required_text(policy, "revision"),
            _required_text(policy, "image_digest"),
            RuntimeLimits(**_integers(limits)),
            RuntimeChannelLimits(**_integers(channel)),
        ),
        _required_text(mapping, "pool"),
        _required_text(mapping, "fence_revision"),
    )


def _integers(value: Mapping[str, object]) -> dict[str, int]:
    if any(type(item) is not int for item in value.values()):
        raise ValueError
    return cast(dict[str, int], dict(value))


def _closed_mapping(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError
    return cast(Mapping[str, object], value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item:
        raise ValueError
    return item


def _same_runtime_identity(
    left: KubernetesRuntimeUnit | None, right: KubernetesRuntimeUnit
) -> bool:
    return (
        left is not None
        and left.unit_id == right.unit_id
        and left.incarnation == right.incarnation
        and left.pod == right.pod
        and left.sandbox == right.sandbox
    )
