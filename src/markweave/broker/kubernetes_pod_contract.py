"""Construct and verify the exact bounded Kubernetes Pod contract."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import cast

from markweave.broker.kubernetes_contracts import (
    _ATTEMPT_LABEL,
    _FENCE_LABEL,
    _FIXED_ENTRYPOINT,
    _MANAGED_LABEL,
    _POLICY_LABEL,
    _POOL_LABEL,
    _PRINCIPAL_LABEL,
    _RESOURCE_PATH_DEPTH,
    _SPECIFICATION_ANNOTATION,
    _TAINT_KEY,
    _UNIT_LABEL,
    KubernetesRuntimeConfig,
    KubernetesRuntimeError,
)
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
)


def manifest_digest(manifest: Mapping[str, object]) -> EvidenceDigest:
    """Return canonical content-free evidence for one broker-authored manifest."""

    try:
        encoded = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise KubernetesRuntimeError("Kubernetes manifest is invalid") from error
    return EvidenceDigest(f"sha256:{hashlib.sha256(encoded).hexdigest()}")


def pod_contract_projection(pod: Mapping[str, object]) -> dict[str, object]:
    """Return the canonical broker-owned subset before API-server defaulting."""

    if not isinstance(pod, Mapping):
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid")
    try:
        projected = _project_like(pod, _pod_contract_template(pod), ())
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid") from error
    if type(projected) is not dict or any(type(key) is not str for key in projected):
        raise KubernetesRuntimeError("Kubernetes Pod contract is invalid")
    return cast(dict[str, object], projected)


def project_observed_pod(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    """Project an API-defaulted Pod through an exact expected contract shape."""

    if not isinstance(observed, Mapping) or not isinstance(expected, Mapping):
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid")
    try:
        if _extra_workload_containers(observed):
            raise ValueError
        prepared = _restore_omitted_api_defaults(
            _filter_default_tolerations(observed, expected), expected
        )
        projected = _project_like(prepared, expected, ())
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid") from error
    if type(projected) is not dict or any(type(key) is not str for key in projected):
        raise KubernetesRuntimeError("Kubernetes observed Pod is invalid")
    return cast(dict[str, object], projected)


def pod_contract_digest(pod: Mapping[str, object]) -> EvidenceDigest:
    """Digest one canonical Pod security contract."""

    return manifest_digest(pod_contract_projection(pod))


def _cpu_millicores(policy: BrokerPolicy) -> int:
    scaled = policy.limits.cpu_quota_micros * 1000
    period = policy.limits.cpu_period_micros
    if scaled % period:
        raise KubernetesRuntimeError(
            "Kubernetes CPU policy is not exactly representable"
        )
    result = scaled // period
    if result <= 0:
        raise KubernetesRuntimeError("Kubernetes CPU policy is invalid")
    return result


def _pod_contract_template(pod: Mapping[str, object]) -> dict[str, object]:
    return dict(pod)


def _project_like(value: object, template: object, path: tuple[str, ...]) -> object:
    if isinstance(template, Mapping):
        if not isinstance(value, Mapping):
            raise TypeError
        for key in value.keys() - template.keys():
            if type(key) is not str or not _allowed_api_default(
                path, key, value[key], value
            ):
                raise ValueError
        return {
            key: _project_like(value[key], child, (*path, key))
            for key, child in template.items()
        }

    if type(template) is list:
        if type(value) is not list or len(value) != len(template):
            raise TypeError
        return [
            _project_like(item, template[index], (*path, str(index)))
            for index, item in enumerate(value)
        ]
    if len(path) >= _RESOURCE_PATH_DEPTH and path[-2] in {"limits", "requests"}:
        if path[-1] == "cpu":
            return _cpu_quantity(value)
        if path[-1] in {"memory", "ephemeral-storage"}:
            return _byte_quantity(value)
    if path[-1:] == ("sizeLimit",):
        return _byte_quantity(value)
    if type(value) is not type(template):
        raise TypeError
    return value


def _cpu_quantity(value: object) -> int:
    if type(value) is not str or not value:
        raise ValueError
    amount = Decimal(value[:-1]) if value.endswith("m") else Decimal(value) * 1000
    if amount != amount.to_integral_value() or amount <= 0:
        raise ValueError
    return int(amount)


def _byte_quantity(value: object) -> int:
    if type(value) is not str or not value:
        raise ValueError
    suffixes = {
        "Ki": 1 << 10,
        "Mi": 1 << 20,
        "Gi": 1 << 30,
        "Ti": 1 << 40,
        "Pi": 1 << 50,
        "Ei": 1 << 60,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }
    suffix = next((item for item in suffixes if value.endswith(item)), "")
    number = value[: -len(suffix)] if suffix else value
    amount = Decimal(number) * suffixes.get(suffix, 1)
    if amount != amount.to_integral_value() or amount <= 0:
        raise ValueError
    return int(amount)


def _extra_workload_containers(pod: Mapping[str, object]) -> bool:
    specification = pod.get("spec")
    if not isinstance(specification, Mapping):
        raise TypeError
    values = (
        specification.get("initContainers"),
        specification.get("ephemeralContainers"),
    )
    return any(value is not None and value not in ((), []) for value in values)


def _filter_default_tolerations(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    observed_copy = dict(observed)
    observed_specification = observed.get("spec")
    expected_specification = expected.get("spec")
    if not isinstance(observed_specification, Mapping) or not isinstance(
        expected_specification, Mapping
    ):
        raise TypeError
    expected_tolerations = expected_specification.get("tolerations")
    observed_tolerations = observed_specification.get("tolerations")
    if type(expected_tolerations) is not list or type(observed_tolerations) is not list:
        raise TypeError
    expected_keys = {
        item.get("key") for item in expected_tolerations if isinstance(item, Mapping)
    }
    defaults = (
        {
            "effect": "NoExecute",
            "key": "node.kubernetes.io/not-ready",
            "operator": "Exists",
            "tolerationSeconds": 300,
        },
        {
            "effect": "NoExecute",
            "key": "node.kubernetes.io/unreachable",
            "operator": "Exists",
            "tolerationSeconds": 300,
        },
    )
    for item in observed_tolerations:
        if not isinstance(item, Mapping):
            raise TypeError
        if item.get("key") not in expected_keys and dict(item) not in defaults:
            raise ValueError
    specification_copy = dict(observed_specification)
    specification_copy["tolerations"] = [
        item
        for item in observed_tolerations
        if isinstance(item, Mapping) and item.get("key") in expected_keys
    ]
    observed_copy["spec"] = specification_copy
    return observed_copy


def _restore_omitted_api_defaults(
    observed: Mapping[str, object], expected: Mapping[str, object]
) -> dict[str, object]:
    """Restore secure zero-value fields omitted by Kubernetes serialization."""

    observed_copy = dict(observed)
    observed_specification = observed.get("spec")
    expected_specification = expected.get("spec")
    if not isinstance(observed_specification, Mapping) or not isinstance(
        expected_specification, Mapping
    ):
        raise TypeError
    specification_copy = dict(observed_specification)
    for key in ("hostIPC", "hostNetwork", "hostPID"):
        if key not in specification_copy and expected_specification.get(key) is False:
            specification_copy[key] = False
    observed_containers = specification_copy.get("containers")
    expected_containers = expected_specification.get("containers")
    if type(observed_containers) is not list or type(expected_containers) is not list:
        raise TypeError
    containers_copy = list(observed_containers)
    if len(containers_copy) == 1 and len(expected_containers) == 1:
        observed_container = containers_copy[0]
        expected_container = expected_containers[0]
        if not isinstance(observed_container, Mapping) or not isinstance(
            expected_container, Mapping
        ):
            raise TypeError
        container_copy = dict(observed_container)
        if "args" not in container_copy and expected_container.get("args") == []:
            container_copy["args"] = []
        containers_copy[0] = container_copy
    specification_copy["containers"] = containers_copy
    observed_copy["spec"] = specification_copy
    return observed_copy


def _allowed_api_default(
    path: tuple[str, ...], key: str, value: object, parent: Mapping[object, object]
) -> bool:
    allowed = False
    if path == ():
        allowed = key == "status" and isinstance(value, Mapping)
    elif path == ("metadata",):
        if key in {
            "creationTimestamp",
            "deletionTimestamp",
            "resourceVersion",
            "uid",
        }:
            allowed = type(value) is str and bool(value)
        elif key == "deletionGracePeriodSeconds":
            deletion_timestamp = parent.get("deletionTimestamp")
            allowed = (
                type(value) is int
                and value >= 0
                and type(deletion_timestamp) is str
                and bool(deletion_timestamp)
            )
        elif key == "generation":
            allowed = type(value) is int and value >= 1
        else:
            allowed = key == "managedFields" and type(value) is list
    elif path == ("spec",):
        if key == "nodeName":
            allowed = type(value) is str and bool(value)
        elif key == "serviceAccount":
            allowed = value == parent.get("serviceAccountName")
        else:
            allowed = (key, value) in {
                ("preemptionPolicy", "PreemptLowerPriority"),
                ("priority", 0),
                ("schedulerName", "default-scheduler"),
            }
    elif path == ("spec", "containers", "0"):
        allowed = (key, value) in {
            ("terminationMessagePath", "/dev/termination-log"),
            ("terminationMessagePolicy", "File"),
        }
    elif path == (
        "spec",
        "containers",
        "0",
        "env",
        "0",
        "valueFrom",
        "fieldRef",
    ):
        allowed = (key, value) == ("apiVersion", "v1")
    return allowed


def build_pod_manifest(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    config: KubernetesRuntimeConfig,
    image_repository: str,
) -> dict[str, object]:
    if (
        policy.limits.memory_bytes - policy.limits.workspace_bytes
        < config.interpreter_memory_margin_bytes
    ):
        raise KubernetesRuntimeError("Kubernetes memory policy is invalid")
    cpu_millicores = _cpu_millicores(policy)
    labels = {
        _ATTEMPT_LABEL: str(unit.attempt_id),
        _MANAGED_LABEL: "1",
        _POLICY_LABEL: policy.revision,
        _PRINCIPAL_LABEL: str(unit.principal.principal_id),
        _UNIT_LABEL: str(unit.unit_id),
    }
    name = f"markweave-reverse-{unit.unit_id.hex}"
    seconds = max(1, math.ceil(policy.limits.wall_time_millis / 1000))
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": config.namespace,
            "labels": labels,
            "annotations": {
                _SPECIFICATION_ANNOTATION: unit.policy_specification.value,
            },
        },
        "spec": {
            "activeDeadlineSeconds": seconds,
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "name": "attempt",
                    "image": f"{image_repository}@{policy.image_digest}",
                    "imagePullPolicy": "IfNotPresent",
                    "command": list(_FIXED_ENTRYPOINT),
                    "args": [],
                    "env": [
                        {
                            "name": "MARKWEAVE_POD_UID",
                            "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
                        },
                        {
                            "name": "MARKWEAVE_REVERSE_MAX_INPUT_BYTES",
                            "value": str(policy.channel_limits.max_input_bytes),
                        },
                        {
                            "name": "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES",
                            "value": str(policy.channel_limits.max_output_bytes),
                        },
                    ],
                    "resources": {
                        "limits": {
                            "cpu": f"{cpu_millicores}m",
                            "memory": str(policy.limits.memory_bytes),
                            "ephemeral-storage": str(policy.limits.workspace_bytes),
                        },
                        "requests": {
                            "cpu": f"{cpu_millicores}m",
                            "memory": str(policy.limits.memory_bytes),
                            "ephemeral-storage": str(policy.limits.workspace_bytes),
                        },
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                        "privileged": False,
                        "readOnlyRootFilesystem": True,
                        "runAsGroup": config.run_as_gid,
                        "runAsNonRoot": True,
                        "runAsUser": config.run_as_uid,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                    "workingDir": "/work",
                }
            ],
            "dnsPolicy": "None",
            "dnsConfig": {"nameservers": ["127.0.0.1"]},
            "enableServiceLinks": False,
            "hostIPC": False,
            "hostNetwork": False,
            "hostPID": False,
            "nodeSelector": {
                _POOL_LABEL: config.pool_name,
                _FENCE_LABEL: config.node_fence_revision,
            },
            "restartPolicy": "Never",
            "runtimeClassName": config.runtime_class,
            "securityContext": {
                "fsGroup": config.run_as_gid,
                "fsGroupChangePolicy": "OnRootMismatch",
                "runAsGroup": config.run_as_gid,
                "runAsNonRoot": True,
                "runAsUser": config.run_as_uid,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "serviceAccountName": config.service_account,
            "setHostnameAsFQDN": False,
            "shareProcessNamespace": False,
            "terminationGracePeriodSeconds": 0,
            "tolerations": [
                {
                    "effect": "NoSchedule",
                    "key": _TAINT_KEY,
                    "operator": "Equal",
                    "value": config.pool_name,
                }
            ],
            "volumes": [
                {
                    "name": "work",
                    "emptyDir": {
                        "medium": "Memory",
                        "sizeLimit": str(policy.limits.workspace_bytes),
                    },
                }
            ],
        },
    }
