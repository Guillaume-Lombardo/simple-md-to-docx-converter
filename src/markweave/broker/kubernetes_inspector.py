"""Bounded read-only CRI, cgroup v2, and Kubernetes node inspection."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID

import yaml

from markweave.broker.command_runner import (
    BoundedCommandRunner,
    PodmanCommandLimits,
)
from markweave.broker.kubernetes_attester import (
    NodeFenceSnapshot,
    RemovalSnapshot,
    SandboxSnapshot,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationNotReady,
    KubernetesRuntimeError,
)

_POOL_LABEL = "reverse.markweave.dev/isolation-pool"
_FENCE_LABEL = "reverse.markweave.dev/node-fence"
_DEDICATED_TAINT = "reverse.markweave.dev/dedicated"
_POD_UID_LABEL = "io.kubernetes.pod.uid"
_ID = re.compile(r"[0-9a-f]{32,128}\Z")
_CGROUP_PARENT = re.compile(r"/[-A-Za-z0-9_.]+(?:/[-A-Za-z0-9_.]+)*\Z")
_MAX_FILE_BYTES = 64 * 1024
_MIN_OUTPUT_BYTES = 4096
_MAX_OUTPUT_BYTES = 128 * 1024
_MAX_NODE_NAME_BYTES = 253
_CPU_MAX_FIELDS = 2
_MIN_MOUNTINFO_FIELDS = 10
_NETWORK_DEV_HEADER_LINES = 2
_IPV4_MAX_OCTET = 255
_IPV6_ROUTE_FIELDS = 10
_EXPECTED_WORKSPACE_FLAGS = ("nodev", "noexec", "nosuid", "rw")
_EXPECTED_NETWORK_INTERFACES = ("eth0", "lo")
_EXPECTED_NETWORK_ADDRESSES = ("127.0.0.1", "192.0.2.1")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INTERFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}\Z")


class ReadOnlyKubernetesApi(Protocol):
    """Minimal read-only API held only by the trusted node attester."""

    def node(self, name: str) -> Mapping[str, object]: ...

    def pod(self, namespace: str, name: str) -> Mapping[str, object]: ...


class _CoreReadApi(Protocol):
    def read_node(self, *, name: str, _request_timeout: float) -> object: ...

    def read_namespaced_pod(
        self, *, name: str, namespace: str, _request_timeout: float
    ) -> object: ...


class CriCommand(Protocol):
    """Bounded fixed-command adapter."""

    def __call__(
        self, arguments: Sequence[str], *, max_output_bytes: int | None = None
    ) -> tuple[int, bytes]: ...


class _StatvfsResult(Protocol):
    @property
    def f_frsize(self) -> int: ...

    @property
    def f_blocks(self) -> int: ...


def _statvfs(path: Path) -> _StatvfsResult:
    return os.statvfs(path)


@dataclass(frozen=True, slots=True)
class InspectorLimits:
    """Absolute bounds for local inspection work."""

    operation_seconds: float
    output_bytes: int
    max_sandboxes: int
    max_containers: int
    max_cgroup_directories: int
    max_descendant_pids: int

    def __post_init__(self) -> None:
        if (
            type(self.operation_seconds) not in {int, float}
            or not math.isfinite(self.operation_seconds)
            or self.operation_seconds <= 0
            or type(self.output_bytes) is not int
            or not _MIN_OUTPUT_BYTES <= self.output_bytes <= _MAX_OUTPUT_BYTES
            or any(
                type(value) is not int or value <= 0
                for value in (
                    self.max_sandboxes,
                    self.max_containers,
                    self.max_cgroup_directories,
                    self.max_descendant_pids,
                )
            )
        ):
            raise ValueError("Kubernetes inspector limits are invalid")


@dataclass(frozen=True, slots=True)
class InspectorConfig:
    """Fixed node-local paths and expected identity."""

    node_name: str
    cri_endpoint: str
    kubelet_config: Path
    cni_config: Path
    cni_config_digest: str
    cni_plugin: Path
    cni_plugin_digest: str
    runtime_config: Path
    runtime_config_digest: str
    runtime_wrapper: Path
    runtime_wrapper_digest: str
    cgroup_root: Path
    proc_root: Path

    def __post_init__(self) -> None:
        if (
            type(self.node_name) is not str
            or not self.node_name
            or len(self.node_name.encode("utf-8")) > _MAX_NODE_NAME_BYTES
            or type(self.cri_endpoint) is not str
            or not self.cri_endpoint.startswith("unix:///")
            or any(
                not isinstance(value, Path) or not value.is_absolute()
                for value in (
                    self.kubelet_config,
                    self.cni_config,
                    self.cni_plugin,
                    self.runtime_config,
                    self.runtime_wrapper,
                    self.cgroup_root,
                    self.proc_root,
                )
            )
            or any(
                type(value) is not str or _DIGEST.fullmatch(value) is None
                for value in (
                    self.cni_config_digest,
                    self.cni_plugin_digest,
                    self.runtime_config_digest,
                    self.runtime_wrapper_digest,
                )
            )
        ):
            raise ValueError("Kubernetes inspector configuration is invalid")


class KubernetesReadApi:
    """Official-client adapter exposing only serialized Node and Pod reads."""

    def __init__(
        self,
        core_api: object,
        serializer: Callable[[object], object],
        *,
        operation_seconds: float,
    ) -> None:
        if (
            not callable(getattr(core_api, "read_node", None))
            or not callable(getattr(core_api, "read_namespaced_pod", None))
            or not callable(serializer)
            or type(operation_seconds) not in {int, float}
            or not math.isfinite(operation_seconds)
            or operation_seconds <= 0
        ):
            raise ValueError("Kubernetes read API is invalid")
        self._core = cast(_CoreReadApi, core_api)
        self._serialize = serializer
        self._operation_seconds = float(operation_seconds)

    @classmethod
    def in_cluster(cls, *, operation_seconds: float) -> KubernetesReadApi:
        """Load the attester's read-only in-cluster identity."""

        try:
            client = import_module("kubernetes.client")
            config = import_module("kubernetes.config")
            config.load_incluster_config()
            api_client = client.ApiClient()
            return cls(
                client.CoreV1Api(api_client),
                api_client.sanitize_for_serialization,
                operation_seconds=operation_seconds,
            )
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes read API configuration failed"
            ) from None

    def node(self, name: str) -> Mapping[str, object]:
        return self._read(
            lambda: self._core.read_node(
                name=name, _request_timeout=self._operation_seconds
            )
        )

    def pod(self, namespace: str, name: str) -> Mapping[str, object]:
        return self._read(
            lambda: self._core.read_namespaced_pod(
                name=name,
                namespace=namespace,
                _request_timeout=self._operation_seconds,
            )
        )

    def _read(self, operation: Callable[[], object]) -> Mapping[str, object]:
        try:
            value = self._serialize(operation())
        except Exception:
            raise KubernetesRuntimeError("Kubernetes read API failed") from None
        return _mapping(value, "Kubernetes read API response is invalid")


def build_cri_command(
    executable: Path, limits: InspectorLimits
) -> BoundedCommandRunner:
    """Build a no-shell command runner with fixed environment and ceilings."""

    if not executable.is_absolute():
        raise ValueError("Kubernetes CRI executable is invalid")
    return BoundedCommandRunner(
        executable,
        PodmanCommandLimits(limits.operation_seconds, limits.output_bytes),
        environment={"HOME": "/", "PATH": "/usr/bin:/bin"},
    )


class BoundedCriCgroupInspector:
    """Concrete inspector whose authority is limited to reads and fixed CRI argv."""

    def __init__(
        self,
        config: InspectorConfig,
        limits: InspectorLimits,
        api: ReadOnlyKubernetesApi,
        command: CriCommand,
        *,
        statvfs: Callable[[Path], _StatvfsResult] = _statvfs,
    ) -> None:
        if (
            type(config) is not InspectorConfig
            or type(limits) is not InspectorLimits
            or not all(callable(getattr(api, name, None)) for name in ("node", "pod"))
            or not callable(command)
            or not callable(statvfs)
        ):
            raise ValueError("Kubernetes inspector is invalid")
        self._config = config
        self._limits = limits
        self._api = api
        self._command = command
        self._statvfs = statvfs

    def node_fence(self, node_name: str) -> NodeFenceSnapshot:
        """Read exact scheduling fence and explicit kubelet containment settings."""

        if node_name != self._config.node_name:
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")
        node = self._api.node(node_name)
        metadata = _mapping(node.get("metadata"), "Kubernetes node fence is invalid")
        specification = _mapping(node.get("spec"), "Kubernetes node fence is invalid")
        status = _mapping(node.get("status"), "Kubernetes node fence is invalid")
        labels = _string_mapping(metadata.get("labels"))
        node_uid = _uuid(metadata.get("uid"), "Kubernetes node fence is invalid")
        ready = _node_ready(status.get("conditions"))
        taint = _dedicated_taint(specification.get("taints"))
        kubelet = _yaml_mapping(self._config.kubelet_config)
        pids = _positive_int(kubelet.get("podPidsLimit"))
        period = _duration_micros(kubelet.get("cpuCFSQuotaPeriod"))
        cni_config_digest = _file_digest(self._config.cni_config)
        cni_plugin_digest = _file_digest(self._config.cni_plugin)
        runtime_config_digest = _file_digest(self._config.runtime_config)
        runtime_wrapper_digest = _file_digest(self._config.runtime_wrapper)
        if (
            cni_config_digest != self._config.cni_config_digest
            or cni_plugin_digest != self._config.cni_plugin_digest
            or runtime_config_digest != self._config.runtime_config_digest
            or runtime_wrapper_digest != self._config.runtime_wrapper_digest
        ):
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")
        if not (self._config.cgroup_root / "cgroup.controllers").is_file():
            raise KubernetesRuntimeError("Kubernetes node fence is invalid")
        try:
            return NodeFenceSnapshot(
                node_name,
                node_uid,
                ready,
                labels[_POOL_LABEL],
                labels[_FENCE_LABEL],
                taint,
                2,
                pids,
                period,
                cni_config_digest,
                cni_plugin_digest,
                runtime_config_digest,
                runtime_wrapper_digest,
            )
        except KeyError, TypeError, ValueError:
            raise KubernetesRuntimeError("Kubernetes node fence is invalid") from None

    def sandbox(self, pod_uid: UUID, sandbox_id: str | None = None) -> SandboxSnapshot:
        """Collect a complete exact CRI sandbox, workload, network, and cgroup view."""

        if type(pod_uid) is not UUID or (
            sandbox_id is not None
            and (type(sandbox_id) is not str or _ID.fullmatch(sandbox_id) is None)
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox lookup failed")
        item = self._select_sandbox(pod_uid, sandbox_id)
        selected_id = _identifier(item.get("id"))
        inspection = self._cri_json(("inspectp", selected_id))
        sandbox_status = _mapping(
            inspection.get("status"), "Kubernetes sandbox lookup failed"
        )
        info = _mapping(inspection.get("info"), "Kubernetes sandbox lookup failed")
        metadata = _mapping(
            sandbox_status.get("metadata"), "Kubernetes sandbox lookup failed"
        )
        if (
            metadata.get("uid") != str(pod_uid)
            or sandbox_status.get("id") != selected_id
        ):
            raise KubernetesRuntimeError("Kubernetes sandbox lookup failed")
        namespace = _text(metadata.get("namespace"))
        name = _text(metadata.get("name"))
        observed_pod = dict(self._api.pod(namespace, name))
        node_name = _text(
            _mapping(observed_pod.get("spec"), "Kubernetes sandbox lookup failed").get(
                "nodeName"
            )
        )
        node_uid = self.node_fence(node_name).node_uid
        containers = self._containers(selected_id, pod_uid)
        container_ids = tuple(
            sorted(_identifier(item.get("id")) for item in containers)
        )
        container_states = tuple(
            _container_state(item.get("state"))
            for item in sorted(
                containers, key=lambda value: _identifier(value.get("id"))
            )
        )
        cgroup_path = _cgroup_path(info, pod_uid)
        cgroup = self._host_cgroup(cgroup_path)
        present = _directory_present(cgroup)
        resources = _cri_resources(info)
        quota, period, memory, pids, populated, descendants = self._cgroup_facts(
            cgroup, present, resources, self.node_fence(node_name).pod_pids_limit
        )
        workspace = self._workspace(
            containers,
            observed_pod,
            present=present and "RUNNING" in container_states,
        )
        interfaces, addresses, routes, neighbors = self._network(info)
        state = sandbox_status.get("state")
        if state not in {"SANDBOX_READY", "SANDBOX_NOTREADY"}:
            raise KubernetesRuntimeError("Kubernetes sandbox lookup failed")
        return SandboxSnapshot(
            pod_uid,
            node_uid,
            selected_id,
            cgroup_path,
            observed_pod,
            container_ids,
            container_states,
            state == "SANDBOX_READY",
            interfaces,
            addresses,
            routes,
            neighbors,
            quota,
            period,
            memory,
            pids,
            workspace[0],
            "/work",
            workspace[1],
            workspace[2],
            True,
            present,
            populated,
            descendants,
        )

    def removal(
        self, pod_uid: UUID, sandbox_id: str, cgroup_path: str
    ) -> RemovalSnapshot:
        """Complete both negative lookups for the previously bound identities."""

        if (
            type(pod_uid) is not UUID
            or type(sandbox_id) is not str
            or _ID.fullmatch(sandbox_id) is None
        ):
            raise KubernetesRuntimeError("Kubernetes removal is unconfirmed")
        host_cgroup = self._host_cgroup(cgroup_path)
        sandboxes = self._sandboxes(pod_uid)
        present = any(_identifier(item.get("id")) == sandbox_id for item in sandboxes)
        node_uid = self.node_fence(self._config.node_name).node_uid
        return RemovalSnapshot(
            pod_uid,
            node_uid,
            sandbox_id,
            cgroup_path,
            True,
            present,
            True,
            _directory_present(host_cgroup),
        )

    def _select_sandbox(
        self, pod_uid: UUID, expected: str | None
    ) -> Mapping[str, object]:
        items = self._sandboxes(pod_uid)
        if expected is not None:
            matches = [item for item in items if item.get("id") == expected]
        else:
            ready = [item for item in items if item.get("state") == "SANDBOX_READY"]
            if len(ready) == 1:
                return ready[0]
            if ready:
                raise KubernetesRuntimeError("Kubernetes sandbox lookup failed")
            attempts = [
                (
                    _nonnegative_int(
                        _mapping(
                            item.get("metadata"), "Kubernetes sandbox lookup failed"
                        ).get("attempt")
                    ),
                    item,
                )
                for item in items
            ]
            highest = max((value for value, _ in attempts), default=-1)
            matches = [item for value, item in attempts if value == highest]
        if len(matches) != 1:
            raise KubernetesAttestationNotReady("Kubernetes sandbox is not observable")
        return matches[0]

    def _sandboxes(self, pod_uid: UUID) -> tuple[Mapping[str, object], ...]:
        payload = self._cri_json(
            ("pods", "--label", f"{_POD_UID_LABEL}={pod_uid}", "--output", "json")
        )
        items = _mapping_items(payload.get("items"), self._limits.max_sandboxes)
        for item in items:
            metadata = _mapping(
                item.get("metadata"), "Kubernetes sandbox lookup failed"
            )
            if metadata.get("uid") != str(pod_uid):
                raise KubernetesRuntimeError("Kubernetes sandbox lookup failed")
            _identifier(item.get("id"))
        return items

    def _containers(
        self, sandbox_id: str, pod_uid: UUID
    ) -> tuple[Mapping[str, object], ...]:
        payload = self._cri_json(
            ("ps", "--all", "--pod", sandbox_id, "--output", "json")
        )
        items = _mapping_items(payload.get("containers"), self._limits.max_containers)
        if not items:
            raise KubernetesAttestationNotReady(
                "Kubernetes containers are not observable"
            )
        for item in items:
            labels = _string_mapping(item.get("labels"))
            if item.get("podSandboxId") != sandbox_id or labels.get(
                _POD_UID_LABEL
            ) != str(pod_uid):
                raise KubernetesRuntimeError("Kubernetes container lookup failed")
        return items

    def _cri_json(self, arguments: Sequence[str]) -> Mapping[str, object]:
        argv = (
            "--runtime-endpoint",
            self._config.cri_endpoint,
            "--image-endpoint",
            self._config.cri_endpoint,
            "--timeout",
            f"{self._limits.operation_seconds}s",
            *arguments,
        )
        try:
            _, output = self._command(argv, max_output_bytes=self._limits.output_bytes)
            value = json.loads(output.decode("ascii"))
        except Exception:
            raise KubernetesRuntimeError("Kubernetes CRI inspection failed") from None
        return _mapping(value, "Kubernetes CRI response is invalid")

    def _host_cgroup(self, canonical: str) -> Path:
        if type(canonical) is not str or not canonical.startswith("/sys/fs/cgroup/"):
            raise KubernetesRuntimeError("Kubernetes cgroup identity is invalid")
        relative = canonical.removeprefix("/sys/fs/cgroup/")
        components = relative.split("/")
        if (
            not relative
            or _CGROUP_PARENT.fullmatch(f"/{relative}") is None
            or any(component in {"", ".", ".."} for component in components)
        ):
            raise KubernetesRuntimeError("Kubernetes cgroup identity is invalid")
        return self._config.cgroup_root / relative

    def _cgroup_facts(
        self,
        path: Path,
        present: bool,
        fallback: tuple[int, int, int],
        pids_fallback: int,
    ) -> tuple[int, int, int, int, bool, tuple[int, ...]]:
        if not present:
            return (*fallback, pids_fallback, False, ())
        quota, period = _cpu_max(_read_text(path / "cpu.max"))
        memory = _limit(_read_text(path / "memory.max"))
        pids = _limit(_read_text(path / "pids.max"))
        populated = _populated(_read_text(path / "cgroup.events"))
        descendants = self._descendant_pids(path)
        return quota, period, memory, pids, populated, descendants

    def _descendant_pids(self, root: Path) -> tuple[int, ...]:
        directories = [root]
        result: set[int] = set()
        for directory in root.rglob("*"):
            if directory.is_dir():
                directories.append(directory)
                if len(directories) > self._limits.max_cgroup_directories:
                    raise KubernetesRuntimeError(
                        "Kubernetes cgroup lookup exceeded its limit"
                    )
        for directory in directories:
            for line in _read_text(directory / "cgroup.procs").splitlines():
                pid = _positive_int(line)
                result.add(pid)
                if len(result) > self._limits.max_descendant_pids:
                    raise KubernetesRuntimeError(
                        "Kubernetes cgroup lookup exceeded its limit"
                    )
        return tuple(sorted(result))

    def _network(
        self, info: Mapping[str, object]
    ) -> tuple[
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
    ]:
        cni = _mapping(info.get("cniResult"), "Kubernetes network inspection failed")
        raw_interfaces = _mapping(
            cni.get("Interfaces"), "Kubernetes network inspection failed"
        )
        cni_interfaces = tuple(
            sorted(
                name
                for name, value in raw_interfaces.items()
                if isinstance(value, Mapping) and bool(value.get("Sandbox"))
            )
        )
        if cni_interfaces != _EXPECTED_NETWORK_INTERFACES or cni.get("Routes") not in (
            None,
            [],
        ):
            raise KubernetesRuntimeError("Kubernetes network isolation is invalid")
        pid = _positive_int(info.get("pid"))
        network = self._config.proc_root / str(pid) / "net"
        interfaces = _network_interfaces(_read_text(network / "dev"))
        addresses = _network_addresses(_read_text(network / "fib_trie"))
        routes = _network_routes(
            _read_text(network / "route"), _read_text(network / "ipv6_route")
        )
        neighbors = _network_neighbors(_read_text(network / "arp"))
        if (
            interfaces != _EXPECTED_NETWORK_INTERFACES
            or addresses != _EXPECTED_NETWORK_ADDRESSES
            or routes
            or neighbors
        ):
            raise KubernetesRuntimeError("Kubernetes network isolation is invalid")
        return interfaces, addresses, routes, neighbors

    def _workspace(
        self,
        containers: tuple[Mapping[str, object], ...],
        pod: Mapping[str, object],
        *,
        present: bool,
    ) -> tuple[str, int, tuple[str, ...]]:
        size = _workspace_size(pod)
        if not present:
            return "tmpfs", size, _EXPECTED_WORKSPACE_FLAGS
        if len(containers) != 1:
            raise KubernetesRuntimeError("Kubernetes workspace inspection failed")
        container_id = _identifier(containers[0].get("id"))
        inspection = self._cri_json(("inspect", container_id))
        info = _mapping(
            inspection.get("info"), "Kubernetes workspace inspection failed"
        )
        pid = _positive_int(info.get("pid"))
        mountinfo = _read_text(self._config.proc_root / str(pid) / "mountinfo")
        filesystem, flags = _mountinfo(mountinfo, "/work")
        try:
            stats = self._statvfs(self._config.proc_root / str(pid) / "root" / "work")
            actual_size = stats.f_frsize * stats.f_blocks
        except OSError:
            raise KubernetesRuntimeError(
                "Kubernetes workspace inspection failed"
            ) from None
        return filesystem, actual_size, flags


def _mapping(value: object, message: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise KubernetesRuntimeError(message)
    return cast(Mapping[str, object], value)


def _mapping_items(value: object, limit: int) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise KubernetesRuntimeError("Kubernetes CRI enumeration is invalid")
    return tuple(
        _mapping(item, "Kubernetes CRI enumeration is invalid") for item in value
    )


def _string_mapping(value: object) -> Mapping[str, str]:
    mapping = _mapping(value, "Kubernetes metadata is invalid")
    if any(type(item) is not str for item in mapping.values()):
        raise KubernetesRuntimeError("Kubernetes metadata is invalid")
    return cast(Mapping[str, str], mapping)


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise KubernetesRuntimeError("Kubernetes identity is invalid")
    return value


def _identifier(value: object) -> str:
    text = _text(value)
    if _ID.fullmatch(text) is None:
        raise KubernetesRuntimeError("Kubernetes runtime identity is invalid")
    return text


def _uuid(value: object, message: str) -> UUID:
    try:
        return UUID(_text(value))
    except ValueError, TypeError:
        raise KubernetesRuntimeError(message) from None


def _positive_int(value: object) -> int:
    if type(value) is int:
        result = value
    elif type(value) is str and value.isascii() and value.isdecimal():
        result = int(value)
    else:
        raise KubernetesRuntimeError("Kubernetes numeric value is invalid")
    if result <= 0:
        raise KubernetesRuntimeError("Kubernetes numeric value is invalid")
    return result


def _nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise KubernetesRuntimeError("Kubernetes numeric value is invalid")
    return value


def _yaml_mapping(path: Path) -> Mapping[str, object]:
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_FILE_BYTES:
            raise ValueError
        value = yaml.safe_load(raw)
    except OSError, ValueError, yaml.YAMLError:
        raise KubernetesRuntimeError(
            "Kubernetes kubelet configuration is invalid"
        ) from None
    return _mapping(value, "Kubernetes kubelet configuration is invalid")


def _node_ready(value: object) -> bool:
    if not isinstance(value, list):
        raise KubernetesRuntimeError("Kubernetes node status is invalid")
    ready = [
        item
        for item in value
        if isinstance(item, Mapping) and item.get("type") == "Ready"
    ]
    return len(ready) == 1 and ready[0].get("status") == "True"


def _dedicated_taint(value: object) -> str:
    if not isinstance(value, list):
        raise KubernetesRuntimeError("Kubernetes node taint is invalid")
    matches = [
        item
        for item in value
        if isinstance(item, Mapping)
        and item.get("key") == _DEDICATED_TAINT
        and item.get("effect") == "NoSchedule"
        and type(item.get("value")) is str
    ]
    if len(matches) != 1:
        raise KubernetesRuntimeError("Kubernetes node taint is invalid")
    return cast(str, matches[0]["value"])


def _duration_micros(value: object) -> int:
    if type(value) is not str:
        raise KubernetesRuntimeError("Kubernetes kubelet configuration is invalid")
    match = re.fullmatch(r"([1-9][0-9]*)(us|ms|s)", value)
    if match is None:
        raise KubernetesRuntimeError("Kubernetes kubelet configuration is invalid")
    factors = {"us": 1, "ms": 1000, "s": 1_000_000}
    return int(match.group(1)) * factors[match.group(2)]


def _container_state(value: object) -> str:
    states = {"CONTAINER_RUNNING": "RUNNING", "CONTAINER_EXITED": "EXITED"}
    try:
        return states[cast(str, value)]
    except KeyError, TypeError:
        raise KubernetesRuntimeError("Kubernetes container state is invalid") from None


def _cgroup_path(info: Mapping[str, object], pod_uid: UUID) -> str:
    config = _mapping(info.get("config"), "Kubernetes cgroup identity is invalid")
    linux = _mapping(config.get("linux"), "Kubernetes cgroup identity is invalid")
    parent = _text(linux.get("cgroup_parent"))
    if (
        _CGROUP_PARENT.fullmatch(parent) is None
        or f"pod{str(pod_uid).replace('-', '_')}" not in parent
    ):
        raise KubernetesRuntimeError("Kubernetes cgroup identity is invalid")
    return f"/sys/fs/cgroup{parent}"


def _cri_resources(info: Mapping[str, object]) -> tuple[int, int, int]:
    config = _mapping(info.get("config"), "Kubernetes CRI resources are invalid")
    linux = _mapping(config.get("linux"), "Kubernetes CRI resources are invalid")
    resources = _mapping(linux.get("resources"), "Kubernetes CRI resources are invalid")
    return (
        _positive_int(resources.get("cpu_quota")),
        _positive_int(resources.get("cpu_period")),
        _positive_int(resources.get("memory_limit_in_bytes")),
    )


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError
        return data.decode("ascii")
    except OSError, UnicodeError, ValueError:
        raise KubernetesRuntimeError("Kubernetes host inspection failed") from None


def _file_digest(path: Path) -> str:
    try:
        data = path.read_bytes()
        if not data or len(data) > _MAX_FILE_BYTES:
            raise ValueError
    except OSError, ValueError:
        raise KubernetesRuntimeError("Kubernetes node asset is invalid") from None
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _network_interfaces(value: str) -> tuple[str, ...]:
    lines = value.splitlines()
    if len(lines) < _NETWORK_DEV_HEADER_LINES:
        raise KubernetesRuntimeError("Kubernetes network inspection failed")
    interfaces: list[str] = []
    for line in lines[2:]:
        if not line.strip():
            continue
        name, separator, _ = line.partition(":")
        interface = name.strip()
        if not separator or _INTERFACE.fullmatch(interface) is None:
            raise KubernetesRuntimeError("Kubernetes network inspection failed")
        interfaces.append(interface)
    if len(interfaces) != len(set(interfaces)):
        raise KubernetesRuntimeError("Kubernetes network inspection failed")
    return tuple(sorted(interfaces))


def _network_addresses(value: str) -> tuple[str, ...]:
    lines = value.splitlines()
    try:
        local = lines.index("Local:")
    except ValueError:
        raise KubernetesRuntimeError("Kubernetes network inspection failed") from None
    addresses: set[str] = set()
    for index, line in enumerate(lines[local + 1 :], start=local + 1):
        match = re.fullmatch(r"\s*[|+]-- ([0-9]{1,3}(?:\.[0-9]{1,3}){3})", line)
        if match is None or index + 1 >= len(lines):
            continue
        if lines[index + 1].strip() == "/32 host LOCAL":
            octets = match.group(1).split(".")
            if any(int(octet) > _IPV4_MAX_OCTET for octet in octets):
                raise KubernetesRuntimeError("Kubernetes network inspection failed")
            addresses.add(match.group(1))
    return tuple(sorted(addresses))


def _network_routes(ipv4: str, ipv6: str) -> tuple[str, ...]:
    ipv4_lines = [line for line in ipv4.splitlines() if line.strip()]
    if len(ipv4_lines) != 1 or not ipv4_lines[0].startswith("Iface"):
        raise KubernetesRuntimeError("Kubernetes network inspection failed")
    routes: list[str] = []
    for line in ipv6.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if (
            len(fields) != _IPV6_ROUTE_FIELDS
            or _INTERFACE.fullmatch(fields[-1]) is None
        ):
            raise KubernetesRuntimeError("Kubernetes network inspection failed")
        if fields[-1] != "lo":
            routes.append(line)
    return tuple(routes)


def _network_neighbors(value: str) -> tuple[str, ...]:
    lines = [line for line in value.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("IP address"):
        raise KubernetesRuntimeError("Kubernetes network inspection failed")
    return ()


def _directory_present(path: Path) -> bool:
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise KubernetesRuntimeError("Kubernetes host inspection failed")
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise KubernetesRuntimeError("Kubernetes host inspection failed") from None


def _cpu_max(value: str) -> tuple[int, int]:
    fields = value.strip().split()
    if len(fields) != _CPU_MAX_FIELDS or fields[0] == "max":
        raise KubernetesRuntimeError("Kubernetes CPU cgroup is invalid")
    return _positive_int(fields[0]), _positive_int(fields[1])


def _limit(value: str) -> int:
    text = value.strip()
    if text == "max":
        raise KubernetesRuntimeError("Kubernetes cgroup limit is unbounded")
    return _positive_int(text)


def _populated(value: str) -> bool:
    fields = dict(line.split() for line in value.splitlines())
    if fields.get("populated") not in {"0", "1"}:
        raise KubernetesRuntimeError("Kubernetes cgroup state is invalid")
    return fields["populated"] == "1"


def _workspace_size(pod: Mapping[str, object]) -> int:
    spec = _mapping(pod.get("spec"), "Kubernetes workspace specification is invalid")
    volumes = spec.get("volumes")
    if not isinstance(volumes, list):
        raise KubernetesRuntimeError("Kubernetes workspace specification is invalid")
    matches = [
        item
        for item in volumes
        if isinstance(item, Mapping) and item.get("name") == "work"
    ]
    if len(matches) != 1:
        raise KubernetesRuntimeError("Kubernetes workspace specification is invalid")
    empty = _mapping(
        matches[0].get("emptyDir"), "Kubernetes workspace specification is invalid"
    )
    if empty.get("medium") != "Memory":
        raise KubernetesRuntimeError("Kubernetes workspace specification is invalid")
    return _positive_int(empty.get("sizeLimit"))


def _mountinfo(value: str, target: str) -> tuple[str, tuple[str, ...]]:
    matches: list[tuple[str, tuple[str, ...]]] = []
    for line in value.splitlines():
        fields = line.split()
        if (
            "-" not in fields
            or len(fields) < _MIN_MOUNTINFO_FIELDS
            or fields[4] != target
        ):
            continue
        separator = fields.index("-")
        if separator + 3 >= len(fields):
            raise KubernetesRuntimeError("Kubernetes workspace mount is invalid")
        flags = set(fields[5].split(",")) | set(fields[separator + 3].split(","))
        matches.append(
            (
                fields[separator + 1],
                tuple(sorted(flags & set(_EXPECTED_WORKSPACE_FLAGS))),
            )
        )
    if len(matches) != 1:
        raise KubernetesRuntimeError("Kubernetes workspace mount is invalid")
    return matches[0]
