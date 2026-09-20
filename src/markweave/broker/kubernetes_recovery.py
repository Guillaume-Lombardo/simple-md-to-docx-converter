"""Canonical restart bindings for prepared and attested Kubernetes attempts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import cast
from uuid import UUID

from markweave.broker.kubernetes_contracts import (
    _MAX_IMAGE_REPOSITORY_BYTES,
    KubernetesPodIdentity,
    KubernetesRuntimeConfig,
    KubernetesRuntimeError,
    KubernetesSandboxIdentity,
)
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    RuntimeChannelLimits,
    RuntimeLimits,
    RuntimeRecoveryBinding,
    policy_specification_evidence,
)


def _canonical_recovery(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None


def _runtime_name(unit_id: UUID) -> str:
    return f"markweave-reverse-{unit_id.hex}"


def _pod_recovery_mapping(pod: KubernetesPodIdentity) -> dict[str, object]:
    return {
        "attempt_id": str(pod.attempt_id),
        "name": pod.name,
        "namespace": pod.namespace,
        "node_name": pod.node_name,
        "pod_uid": str(pod.pod_uid),
        "policy_revision": pod.policy_revision,
        "policy_specification": pod.policy_specification.value,
        "principal_id": str(pod.principal_id),
        "unit_id": str(pod.unit_id),
    }


def _sandbox_recovery_mapping(
    sandbox: KubernetesSandboxIdentity,
) -> dict[str, object]:
    return {
        "cgroup_path": sandbox.cgroup_path,
        "node_fence": sandbox.node_fence.value,
        "node_uid": str(sandbox.node_uid),
        "sandbox_id": sandbox.sandbox_id,
        "container_ids": list(sandbox.container_ids),
    }


def _policy_recovery_mapping(policy: BrokerPolicy) -> dict[str, object]:
    return asdict(policy)


def _prepared_unit_mapping(unit: ManagedUnit) -> dict[str, object]:
    return {
        "attempt_id": str(unit.attempt_id),
        "create_sequence": unit.create_sequence,
        "policy_revision": unit.policy_revision,
        "policy_specification": unit.policy_specification.value,
        "principal_id": str(unit.principal.principal_id),
        "runtime_name": _runtime_name(unit.unit_id),
        "unit_id": str(unit.unit_id),
    }


def _prepared_recovery_binding(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    config: KubernetesRuntimeConfig,
    repository: str,
    contract_digest: EvidenceDigest,
) -> RuntimeRecoveryBinding:
    return RuntimeRecoveryBinding(
        "kubernetes",
        1,
        _canonical_recovery(
            {
                "config": asdict(config),
                "contract_digest": contract_digest.value,
                "image_repository": repository,
                "phase": "prepared",
                "policy": _policy_recovery_mapping(policy),
                "unit": _prepared_unit_mapping(unit),
            }
        ),
    )


def _closed_recovery(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError
    return cast(Mapping[str, object], value)


def _recovery_text(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if type(result) is not str or not result:
        raise ValueError
    return result


def _recovery_int(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int:
        raise ValueError
    return result


def _recovery_ints(value: Mapping[str, object]) -> dict[str, int]:
    if any(type(item) is not int for item in value.values()):
        raise ValueError
    return cast(dict[str, int], dict(value))


def _decode_recovery_policy(
    root: Mapping[str, object],
) -> tuple[BrokerPolicy, KubernetesRuntimeConfig, str, EvidenceDigest]:
    policy_value = _closed_recovery(
        root["policy"], {"channel_limits", "image_digest", "limits", "revision"}
    )
    limits = _closed_recovery(
        policy_value["limits"], set(RuntimeLimits.__dataclass_fields__)
    )
    channel = _closed_recovery(
        policy_value["channel_limits"],
        set(RuntimeChannelLimits.__dataclass_fields__),
    )
    config_value = _closed_recovery(
        root["config"], set(KubernetesRuntimeConfig.__dataclass_fields__)
    )
    policy = BrokerPolicy(
        _recovery_text(policy_value, "revision"),
        _recovery_text(policy_value, "image_digest"),
        RuntimeLimits(**_recovery_ints(limits)),
        RuntimeChannelLimits(**_recovery_ints(channel)),
    )
    config = KubernetesRuntimeConfig(
        namespace=_recovery_text(config_value, "namespace"),
        service_account=_recovery_text(config_value, "service_account"),
        runtime_class=_recovery_text(config_value, "runtime_class"),
        pool_name=_recovery_text(config_value, "pool_name"),
        node_fence_revision=_recovery_text(config_value, "node_fence_revision"),
        run_as_uid=_recovery_int(config_value, "run_as_uid"),
        run_as_gid=_recovery_int(config_value, "run_as_gid"),
        interpreter_memory_margin_bytes=_recovery_int(
            config_value, "interpreter_memory_margin_bytes"
        ),
    )
    repository = _recovery_text(root, "image_repository")
    if (
        len(repository) > _MAX_IMAGE_REPOSITORY_BYTES
        or "@" in repository
        or any(part in {"", ".", ".."} for part in repository.split("/"))
    ):
        raise ValueError
    return (
        policy,
        config,
        repository,
        EvidenceDigest(_recovery_text(root, "contract_digest")),
    )


def _decode_prepared_recovery_binding(
    unit: ManagedUnit, binding: RuntimeRecoveryBinding
) -> tuple[BrokerPolicy, KubernetesRuntimeConfig, str, EvidenceDigest]:
    if binding.backend != "kubernetes" or binding.schema_version != 1:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
    try:
        root = _closed_recovery(
            json.loads(binding.payload.decode("ascii")),
            {
                "config",
                "contract_digest",
                "image_repository",
                "phase",
                "policy",
                "unit",
            },
        )
        unit_value = _closed_recovery(
            root["unit"],
            {
                "attempt_id",
                "create_sequence",
                "policy_revision",
                "policy_specification",
                "principal_id",
                "runtime_name",
                "unit_id",
            },
        )
        policy, config, repository, digest = _decode_recovery_policy(root)
        if (
            _canonical_recovery(root) != binding.payload
            or root["phase"] != "prepared"
            or UUID(_recovery_text(unit_value, "unit_id")) != unit.unit_id
            or UUID(_recovery_text(unit_value, "attempt_id")) != unit.attempt_id
            or _recovery_int(unit_value, "create_sequence") != unit.create_sequence
            or UUID(_recovery_text(unit_value, "principal_id"))
            != unit.principal.principal_id
            or _recovery_text(unit_value, "runtime_name") != _runtime_name(unit.unit_id)
            or _recovery_text(unit_value, "policy_revision") != unit.policy_revision
            or EvidenceDigest(_recovery_text(unit_value, "policy_specification"))
            != unit.policy_specification
            or policy.revision != unit.policy_revision
            or policy_specification_evidence(policy) != unit.policy_specification
        ):
            raise ValueError
    except KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None
    return policy, config, repository, digest


def _decode_recovery_binding(
    binding: RuntimeRecoveryBinding,
) -> tuple[
    KubernetesPodIdentity,
    KubernetesSandboxIdentity,
    BrokerPolicy,
    KubernetesRuntimeConfig,
    str,
    EvidenceDigest,
]:
    if (
        type(binding) is not RuntimeRecoveryBinding
        or binding.backend != "kubernetes"
        or binding.schema_version != 1
    ):
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid")
    try:
        value = json.loads(binding.payload.decode("ascii"))
        root = _closed_recovery(
            value,
            {
                "config",
                "contract_digest",
                "image_repository",
                "phase",
                "pod",
                "policy",
                "sandbox",
            },
        )
        if _canonical_recovery(root) != binding.payload:
            raise ValueError
        if root["phase"] != "bound":
            raise ValueError
        pod_value = _closed_recovery(
            root["pod"],
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
        sandbox_value = _closed_recovery(
            root["sandbox"],
            {
                "cgroup_path",
                "container_ids",
                "node_fence",
                "node_uid",
                "sandbox_id",
            },
        )
        pod = KubernetesPodIdentity(
            _recovery_text(pod_value, "namespace"),
            _recovery_text(pod_value, "name"),
            UUID(_recovery_text(pod_value, "pod_uid")),
            _recovery_text(pod_value, "node_name"),
            UUID(_recovery_text(pod_value, "unit_id")),
            UUID(_recovery_text(pod_value, "attempt_id")),
            UUID(_recovery_text(pod_value, "principal_id")),
            _recovery_text(pod_value, "policy_revision"),
            EvidenceDigest(_recovery_text(pod_value, "policy_specification")),
        )
        sandbox = KubernetesSandboxIdentity(
            _recovery_text(sandbox_value, "sandbox_id"),
            _recovery_text(sandbox_value, "cgroup_path"),
            UUID(_recovery_text(sandbox_value, "node_uid")),
            EvidenceDigest(_recovery_text(sandbox_value, "node_fence")),
            tuple(cast(list[str], sandbox_value["container_ids"])),
        )
        policy, config, repository, contract_digest = _decode_recovery_policy(root)
    except KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes recovery binding is invalid") from None
    return pod, sandbox, policy, config, repository, contract_digest
