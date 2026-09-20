"""Podman identity, immutable policy labels and inspected-value validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from markweave.broker import command_runner as _command_runner
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    RuntimeRecoveryBinding,
)

PodmanRuntimeError = _command_runner.PodmanRuntimeError

_INCARNATION_NAMESPACE: Final = UUID("9448db2f-5c64-48eb-a960-d520fac4fb5f")

_NAME_PREFIX: Final = "markweave-reverse-"

_LABEL_PREFIX: Final = "io.markweave.reverse-broker."

_MANAGED_LABEL: Final = f"{_LABEL_PREFIX}managed"

_UNIT_LABEL: Final = f"{_LABEL_PREFIX}unit-id"

_ATTEMPT_LABEL: Final = f"{_LABEL_PREFIX}attempt-id"

_PRINCIPAL_LABEL: Final = f"{_LABEL_PREFIX}principal-id"

_POLICY_LABEL: Final = f"{_LABEL_PREFIX}policy-revision"

_SPECIFICATION_LABEL: Final = f"{_LABEL_PREFIX}specification"

_DEADLINE_LABEL: Final = f"{_LABEL_PREFIX}deadline-seconds"

_IMAGE_DIGEST_LABEL: Final = f"{_LABEL_PREFIX}image-digest"

_IMAGE_REPOSITORY_LABEL: Final = f"{_LABEL_PREFIX}image-repository"

_RUN_AS_UID_LABEL: Final = f"{_LABEL_PREFIX}run-as-uid"

_CPU_QUOTA_LABEL: Final = f"{_LABEL_PREFIX}cpu-quota-micros"

_CPU_PERIOD_LABEL: Final = f"{_LABEL_PREFIX}cpu-period-micros"

_MEMORY_LABEL: Final = f"{_LABEL_PREFIX}memory-bytes"

_PID_LIMIT_LABEL: Final = f"{_LABEL_PREFIX}pid-limit"

_WORKSPACE_LABEL: Final = f"{_LABEL_PREFIX}workspace-bytes"

_WALL_TIME_LABEL: Final = f"{_LABEL_PREFIX}wall-time-millis"

_MAX_INPUT_LABEL: Final = f"{_LABEL_PREFIX}max-input-bytes"

_MAX_OUTPUT_LABEL: Final = f"{_LABEL_PREFIX}max-output-bytes"

_MANAGED_LABEL_KEYS: Final = frozenset(
    {
        _ATTEMPT_LABEL,
        _CPU_PERIOD_LABEL,
        _CPU_QUOTA_LABEL,
        _DEADLINE_LABEL,
        _IMAGE_DIGEST_LABEL,
        _IMAGE_REPOSITORY_LABEL,
        _MANAGED_LABEL,
        _MEMORY_LABEL,
        _PID_LIMIT_LABEL,
        _POLICY_LABEL,
        _PRINCIPAL_LABEL,
        _RUN_AS_UID_LABEL,
        _SPECIFICATION_LABEL,
        _UNIT_LABEL,
        _WORKSPACE_LABEL,
        _WALL_TIME_LABEL,
        _MAX_INPUT_LABEL,
        _MAX_OUTPUT_LABEL,
    }
)

_IMAGE_REPOSITORY_PATTERN = re.compile(
    r"(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?"
    r"/[a-z0-9][a-z0-9._/-]*\Z"
)

_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

_CONTAINER_ID_LENGTH: Final = 64

_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+(?:Z|[+-][0-9:]+)?\Z"
)

_FIXED_ENTRYPOINT: Final = (
    "python",
    "-m",
    "markweave.reversions.attempt_main",
)

_FIXED_ENVIRONMENT: Final = (
    "HOME=/work/home",
    "PATH=/opt/markweave/venv/bin:/usr/local/bin:/usr/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
    "RAYON_NUM_THREADS=1",
    "TMPDIR=/work/tmp",
    "XDG_CACHE_HOME=/work/xdg/cache",
    "XDG_CONFIG_HOME=/work/xdg/config",
    "XDG_DATA_HOME=/work/xdg/data",
    "XDG_RUNTIME_DIR=/work/xdg/runtime",
)

_INSPECT_MAX_BYTES: Final = 64 * 1024

_MAX_COMMAND_OUTPUT_BYTES: Final = 128 * 1024

_MIN_OUTPUT_BYTES: Final = 1024

_MAX_LABEL_BYTES: Final = 128


@dataclass(frozen=True, slots=True)
class PodmanRuntimeUnit:
    """Verified opaque identity of one exact Podman container incarnation."""

    unit_id: UUID
    attempt_id: UUID
    principal_id: UUID
    incarnation: RuntimeIncarnation
    container_id: str
    name: str
    recovery_binding: RuntimeRecoveryBinding | None = None

    def __post_init__(self) -> None:
        if (
            type(self.unit_id) is not UUID
            or type(self.attempt_id) is not UUID
            or type(self.principal_id) is not UUID
            or type(self.incarnation) is not RuntimeIncarnation
            or type(self.container_id) is not str
            or _CONTAINER_ID_PATTERN.fullmatch(self.container_id) is None
            or self.name != _container_name(self.unit_id)
        ):
            raise ValueError("Podman runtime unit identity is invalid")


type Command = Callable[..., tuple[int, bytes]]


def _container_name(unit_id: UUID) -> str:
    return f"{_NAME_PREFIX}{unit_id.hex}"


def _cgroup_parent(container_name: str) -> str:
    if not container_name.startswith(_NAME_PREFIX):
        raise PodmanRuntimeError("Podman cgroup identity is invalid")
    return f"markweavet70{container_name.removeprefix(_NAME_PREFIX)}.slice"


def _labels(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    image_repository: str,
    run_as_uid: int,
) -> dict[str, str]:
    seconds = math.ceil(policy.limits.wall_time_millis / 1000)
    return {
        _ATTEMPT_LABEL: str(unit.attempt_id),
        _DEADLINE_LABEL: str(seconds),
        _CPU_PERIOD_LABEL: str(policy.limits.cpu_period_micros),
        _CPU_QUOTA_LABEL: str(policy.limits.cpu_quota_micros),
        _IMAGE_DIGEST_LABEL: policy.image_digest,
        _IMAGE_REPOSITORY_LABEL: image_repository,
        _MANAGED_LABEL: "1",
        _MEMORY_LABEL: str(policy.limits.memory_bytes),
        _PID_LIMIT_LABEL: str(policy.limits.pid_limit),
        _POLICY_LABEL: unit.policy_revision,
        _PRINCIPAL_LABEL: str(unit.principal.principal_id),
        _SPECIFICATION_LABEL: unit.policy_specification.value,
        _UNIT_LABEL: str(unit.unit_id),
        _RUN_AS_UID_LABEL: str(run_as_uid),
        _WORKSPACE_LABEL: str(policy.limits.workspace_bytes),
        _WALL_TIME_LABEL: str(policy.limits.wall_time_millis),
        _MAX_INPUT_LABEL: str(policy.channel_limits.max_input_bytes),
        _MAX_OUTPUT_LABEL: str(policy.channel_limits.max_output_bytes),
    }


def _policy_from_labels(labels: Mapping[str, Any]) -> BrokerPolicy:
    try:
        return BrokerPolicy(
            _label(labels, _POLICY_LABEL),
            _label(labels, _IMAGE_DIGEST_LABEL),
            RuntimeLimits(
                _positive_integer_label(labels, _CPU_QUOTA_LABEL),
                _positive_integer_label(labels, _CPU_PERIOD_LABEL),
                _positive_integer_label(labels, _MEMORY_LABEL),
                _positive_integer_label(labels, _PID_LIMIT_LABEL),
                _positive_integer_label(labels, _WORKSPACE_LABEL),
                _positive_integer_label(labels, _WALL_TIME_LABEL),
            ),
            RuntimeChannelLimits(
                _positive_integer_label(labels, _MAX_INPUT_LABEL),
                _positive_integer_label(labels, _MAX_OUTPUT_LABEL),
            ),
        )
    except ValueError as error:
        raise PodmanRuntimeError("Podman policy labels are invalid") from error


def _json(output: bytes) -> Any:
    try:
        return json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PodmanRuntimeError("Podman JSON evidence is invalid") from error


def _matches_create_command(value: Any, arguments: tuple[str, ...]) -> bool:
    return (
        type(value) is list
        and len(value) == len(arguments) + 1
        and all(type(item) is str for item in value)
        and Path(value[0]).name == "podman"
        and value[1:] == list(arguments)
    )


def _matches_entrypoint(value: Any) -> bool:
    """Accept the exact Podman 4.9 string or 5.x argument-vector projection."""

    return value == list(_FIXED_ENTRYPOINT) or (
        type(value) is str and value == " ".join(_FIXED_ENTRYPOINT)
    )


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return nested


def _create_response_identity(output: bytes) -> str | None:
    """Return only a canonical ID emitted by a successful local create."""

    raw = (
        output[:-1]
        if len(output) == _CONTAINER_ID_LENGTH + 1 and output.endswith(b"\n")
        else output
    )
    if len(raw) != _CONTAINER_ID_LENGTH:
        return None
    try:
        identity = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None
    if _CONTAINER_ID_PATTERN.fullmatch(identity) is None:
        return None
    return identity


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if type(item) is not str:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _boolean(value: Mapping[str, Any], key: str) -> bool:
    item = value.get(key)
    if type(item) is not bool:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _integer(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _timestamp(value: Mapping[str, Any], key: str) -> str:
    item = _string(value, key)
    if _TIMESTAMP_PATTERN.fullmatch(item) is None:
        raise PodmanRuntimeError("Podman timestamp evidence is invalid")
    return item


def _label(labels: Mapping[str, Any], key: str) -> str:
    value = labels.get(key)
    if type(value) is not str or len(value) > _MAX_LABEL_BYTES:
        raise PodmanRuntimeError("Podman label evidence is invalid")
    return value


def _positive_integer_label(labels: Mapping[str, Any], key: str) -> int:
    value = _label(labels, key)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise PodmanRuntimeError("Podman numeric label evidence is invalid")
    return int(value)


def _evidence(kind: str, fields: Mapping[str, object]) -> EvidenceDigest:
    payload = json.dumps(
        {"fields": fields, "kind": kind, "schema_version": 1},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return EvidenceDigest(f"sha256:{hashlib.sha256(payload).hexdigest()}")


def build_create_arguments(
    image_repository: str,
    run_as_uid: int,
    name: str,
    labels: Mapping[str, str],
    policy: BrokerPolicy,
) -> tuple[str, ...]:
    seconds = math.ceil(policy.limits.wall_time_millis / 1000)
    arguments = [
        "create",
        "--pull=never",
        "--name",
        name,
        "--hostname",
        name,
        "--network=none",
        "--read-only",
        "--read-only-tmpfs=false",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--user",
        f"{run_as_uid}:0",
        "--cgroups=enabled",
        "--cgroup-parent",
        _cgroup_parent(name),
        "--ipc=none",
        "--pid=private",
        "--uts=private",
        "--restart=no",
        "--no-healthcheck",
        "--log-driver=none",
        "--pids-limit",
        str(policy.limits.pid_limit),
        "--memory",
        f"{policy.limits.memory_bytes}b",
        "--memory-swap",
        f"{policy.limits.memory_bytes}b",
        "--cpu-period",
        str(policy.limits.cpu_period_micros),
        "--cpu-quota",
        str(policy.limits.cpu_quota_micros),
        "--timeout",
        str(seconds),
        "--stop-timeout",
        "0",
        "--unsetenv-all",
        "--mount",
        "type=tmpfs,destination=/work,tmpfs-mode=0770,"
        f"tmpfs-size={policy.limits.workspace_bytes}",
        "--workdir=/work",
        "--entrypoint",
        json.dumps(_FIXED_ENTRYPOINT, separators=(",", ":")),
    ]
    environment_values = (
        *_FIXED_ENVIRONMENT,
        f"MARKWEAVE_REVERSE_MAX_INPUT_BYTES={policy.channel_limits.max_input_bytes}",
        f"MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES={policy.channel_limits.max_output_bytes}",
    )
    for environment in environment_values:
        arguments.extend(("--env", environment))
    for key in sorted(labels):
        arguments.extend(("--label", f"{key}={labels[key]}"))
    arguments.append(f"{image_repository}@{policy.image_digest}")
    return tuple(arguments)


def verify_realized_specification(
    run_as_uid: int,
    runtime_capabilities: tuple[str, ...],
    inspected: Mapping[str, Any],
    policy: BrokerPolicy,
) -> None:
    config = _mapping(inspected, "Config")
    host = _mapping(inspected, "HostConfig")
    expected_config: Mapping[str, object] = {
        "Cmd": None,
        "StopTimeout": 0,
        "Timeout": math.ceil(policy.limits.wall_time_millis / 1000),
        "User": f"{run_as_uid}:0",
        "WorkingDir": "/work",
    }
    expected_host: Mapping[str, object] = {
        "AutoRemove": False,
        "Binds": [],
        "CapAdd": [],
        "CapDrop": list(runtime_capabilities),
        "CgroupParent": _cgroup_parent(_string(inspected, "Name").lstrip("/")),
        "Cgroups": "default",
        "CpuPeriod": policy.limits.cpu_period_micros,
        "CpuQuota": policy.limits.cpu_quota_micros,
        "Devices": [],
        "Dns": [],
        "DnsOptions": [],
        "DnsSearch": [],
        "ExtraHosts": [],
        "GroupAdd": [],
        "IpcMode": "none",
        "LogConfig": {
            "Config": None,
            "Path": "",
            "Size": "0B",
            "Tag": "",
            "Type": "none",
        },
        "Memory": policy.limits.memory_bytes,
        "MemorySwap": policy.limits.memory_bytes,
        "NetworkMode": "none",
        "PidMode": "private",
        "PidsLimit": policy.limits.pid_limit,
        "PortBindings": {},
        "Privileged": False,
        "PublishAllPorts": False,
        "ReadonlyRootfs": True,
        "RestartPolicy": {"MaximumRetryCount": 0, "Name": "no"},
        "SecurityOpt": ["no-new-privileges"],
        "Tmpfs": {
            "/work": (
                f"mode=0770,size={policy.limits.workspace_bytes},"
                "rw,rprivate,nosuid,nodev,tmpcopyup"
            )
        },
        "UTSMode": "private",
        "VolumesFrom": None,
    }
    if (
        type(config.get("Env")) is not list
        or sorted(config["Env"])
        != sorted(
            (
                *_FIXED_ENVIRONMENT,
                "MARKWEAVE_REVERSE_MAX_INPUT_BYTES="
                f"{policy.channel_limits.max_input_bytes}",
                "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES="
                f"{policy.channel_limits.max_output_bytes}",
                f"HOSTNAME={_string(inspected, 'Name').lstrip('/')}",
            )
        )
        or not _matches_entrypoint(config.get("Entrypoint"))
        or any(config.get(key) != value for key, value in expected_config.items())
        or any(host.get(key) != value for key, value in expected_host.items())
        or inspected.get("Mounts") != []
    ):
        raise PodmanRuntimeError("Podman realized specification is invalid")
