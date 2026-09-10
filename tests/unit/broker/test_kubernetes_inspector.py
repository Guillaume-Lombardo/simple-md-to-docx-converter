from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest

from markweave.broker import kubernetes_inspector as target
from markweave.broker.kubernetes_inspector import (
    BoundedCriCgroupInspector,
    InspectorConfig,
    InspectorLimits,
    KubernetesReadApi,
    build_cri_command,
)
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError

POD_UID = UUID("11111111-2222-4333-8444-555555555555")
NODE_UID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
SANDBOX_ID = "6" * 64
CONTAINER_ID = "7" * 64
CGROUP_PARENT = "/kubepods.slice/kubepods-pod11111111_2222_4333_8444_555555555555.slice"
CGROUP = f"/sys/fs/cgroup{CGROUP_PARENT}"


class ApiDouble:
    def __init__(self) -> None:
        self.node_value: object = {
            "metadata": {
                "name": "reverse-node-1",
                "uid": str(NODE_UID),
                "labels": {
                    "reverse.markweave.dev/isolation-pool": "reverse",
                    "reverse.markweave.dev/node-fence": "fence-v1",
                },
            },
            "spec": {
                "taints": [
                    {
                        "key": "reverse.markweave.dev/dedicated",
                        "value": "reverse",
                        "effect": "NoSchedule",
                    }
                ]
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        self.pod_value: object = {
            "metadata": {
                "name": "attempt-1",
                "namespace": "markweave-reverse",
                "uid": str(POD_UID),
            },
            "spec": {
                "nodeName": "reverse-node-1",
                "volumes": [
                    {
                        "name": "work",
                        "emptyDir": {"medium": "Memory", "sizeLimit": "33554432"},
                    }
                ],
            },
        }

    def node(self, name: str) -> Mapping[str, object]:
        assert name == "reverse-node-1"
        return self._mapping(self.node_value)

    def pod(self, namespace: str, name: str) -> Mapping[str, object]:
        assert (namespace, name) == ("markweave-reverse", "attempt-1")
        return self._mapping(self.pod_value)

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        assert isinstance(value, Mapping)
        return value


class CommandDouble:
    def __init__(self) -> None:
        self.sandbox_state = "SANDBOX_READY"
        self.container_state = "CONTAINER_RUNNING"
        self.routes: object = []
        self.interfaces: object = {
            "eth0": {
                "Sandbox": "/var/run/netns/markweave",
                "IPConfigs": [{"IP": "192.0.2.1"}],
            },
            "lo": {
                "Sandbox": "/var/run/netns/markweave",
                "IPConfigs": [{"IP": "127.0.0.1"}, {"IP": "::1"}],
            },
        }
        self.sandboxes: object = [
            {
                "id": SANDBOX_ID,
                "metadata": {
                    "attempt": 0,
                    "name": "attempt-1",
                    "namespace": "markweave-reverse",
                    "uid": str(POD_UID),
                },
                "state": "SANDBOX_READY",
            }
        ]
        self.containers: object = [
            {
                "id": CONTAINER_ID,
                "podSandboxId": SANDBOX_ID,
                "labels": {"io.kubernetes.pod.uid": str(POD_UID)},
                "state": "CONTAINER_RUNNING",
            }
        ]
        self.fail = False
        self.cgroup_parent = CGROUP_PARENT

    def __call__(
        self, arguments: Sequence[str], *, max_output_bytes: int | None = None
    ) -> tuple[int, bytes]:
        assert max_output_bytes == 65536
        assert tuple(arguments[:2]) == (
            "--runtime-endpoint",
            "unix:///run/containerd.sock",
        )
        if self.fail:
            raise RuntimeError("sensitive command failure")
        if "inspectp" in arguments:
            value = {
                "status": {
                    "id": SANDBOX_ID,
                    "metadata": {
                        "name": "attempt-1",
                        "namespace": "markweave-reverse",
                        "uid": str(POD_UID),
                    },
                    "state": self.sandbox_state,
                },
                "info": {
                    "pid": 4321,
                    "cniResult": {
                        "Interfaces": self.interfaces,
                        "Routes": self.routes,
                    },
                    "config": {
                        "linux": {
                            "cgroup_parent": self.cgroup_parent,
                            "resources": {
                                "cpu_quota": 50000,
                                "cpu_period": 100000,
                                "memory_limit_in_bytes": 134217728,
                            },
                        }
                    },
                },
            }
        elif "pods" in arguments:
            value = {"items": self.sandboxes}
        elif "ps" in arguments:
            containers = deepcopy(self.containers)
            assert isinstance(containers, list)
            for item in containers:
                item["state"] = self.container_state
            value = {"containers": containers}
        elif "inspect" in arguments:
            value = {"info": {"pid": 4322}}
        else:
            raise AssertionError(arguments)
        return 0, json.dumps(value).encode("ascii")


@pytest.fixture
def limits() -> InspectorLimits:
    return InspectorLimits(2, 65536, 4, 4, 8, 16)


@pytest.fixture
def inspector(
    tmp_path: Path, limits: InspectorLimits
) -> tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path]:
    kubelet = tmp_path / "kubelet.yaml"
    kubelet.write_text("podPidsLimit: 64\ncpuCFSQuotaPeriod: 100ms\n", encoding="ascii")
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "cgroup.controllers").write_text(
        "cpu memory pids\n", encoding="ascii"
    )
    cgroup = cgroup_root / CGROUP_PARENT.removeprefix("/")
    child = cgroup / "child.scope"
    child.mkdir(parents=True)
    (cgroup / "cpu.max").write_text("50000 100000\n", encoding="ascii")
    (cgroup / "memory.max").write_text("134217728\n", encoding="ascii")
    (cgroup / "pids.max").write_text("64\n", encoding="ascii")
    (cgroup / "cgroup.events").write_text("populated 1\nfrozen 0\n", encoding="ascii")
    (cgroup / "cgroup.procs").write_text("41\n", encoding="ascii")
    (child / "cgroup.procs").write_text("42\n", encoding="ascii")
    proc_root = tmp_path / "proc"
    sandbox = proc_root / "4321"
    (sandbox / "net").mkdir(parents=True)
    (sandbox / "net/dev").write_text(
        "Inter-| Receive | Transmit\n face |bytes |bytes\n"
        " lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
        " eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n",
        encoding="ascii",
    )
    (sandbox / "net/fib_trie").write_text(
        "Main:\n  +-- 0.0.0.0/0\nLocal:\n"
        "  |-- 127.0.0.1\n     /32 host LOCAL\n"
        "  |-- 192.0.2.1\n     /32 host LOCAL\n",
        encoding="ascii",
    )
    (sandbox / "net/route").write_text(
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n",
        encoding="ascii",
    )
    (sandbox / "net/ipv6_route").write_text("", encoding="ascii")
    (sandbox / "net/arp").write_text(
        "IP address HW type Flags HW address Mask Device\n", encoding="ascii"
    )
    process = proc_root / "4322"
    (process / "root" / "work").mkdir(parents=True)
    (process / "mountinfo").write_text(
        "1 2 0:1 / /work rw,nosuid,nodev,noexec - tmpfs tmpfs rw,nosuid,nodev,noexec\n",
        encoding="ascii",
    )
    api = ApiDouble()
    command = CommandDouble()
    cni_config = tmp_path / "00-markweave-isolated.conflist"
    cni_plugin = tmp_path / "markweave-isolated"
    runtime_config = tmp_path / "20-markweave-reverse-runtime.toml"
    runtime_wrapper = tmp_path / "markweave-runc-wrapper"
    cni_config.write_bytes(b"cni-config")
    cni_plugin.write_bytes(b"cni-plugin")
    runtime_config.write_bytes(b"runtime-config")
    runtime_wrapper.write_bytes(b"runtime-wrapper")
    instance = BoundedCriCgroupInspector(
        InspectorConfig(
            "reverse-node-1",
            "unix:///run/containerd.sock",
            kubelet,
            cni_config,
            f"sha256:{hashlib.sha256(b'cni-config').hexdigest()}",
            cni_plugin,
            f"sha256:{hashlib.sha256(b'cni-plugin').hexdigest()}",
            runtime_config,
            f"sha256:{hashlib.sha256(b'runtime-config').hexdigest()}",
            runtime_wrapper,
            f"sha256:{hashlib.sha256(b'runtime-wrapper').hexdigest()}",
            cgroup_root,
            proc_root,
        ),
        limits,
        api,
        command,
        statvfs=lambda _: SimpleNamespace(f_frsize=4096, f_blocks=8192),
    )
    return instance, api, command, cgroup


@pytest.mark.unit
def test_node_fence_reads_exact_api_and_kubelet_facts(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    snapshot = inspector[0].node_fence("reverse-node-1")
    assert snapshot.node_uid == NODE_UID
    assert snapshot.pool_name == "reverse"
    assert snapshot.fence_revision == "fence-v1"
    assert snapshot.dedicated_taint_value == "reverse"
    assert snapshot.ready is True
    assert snapshot.cgroup_version == 2
    assert snapshot.pod_pids_limit == 64
    assert snapshot.cpu_quota_period_micros == 100000
    assert snapshot.runtime_config_digest == (
        f"sha256:{hashlib.sha256(b'runtime-config').hexdigest()}"
    )
    assert snapshot.runtime_wrapper_digest == (
        f"sha256:{hashlib.sha256(b'runtime-wrapper').hexdigest()}"
    )


@pytest.mark.unit
def test_sandbox_reads_positive_cri_network_workspace_and_cgroup_facts(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    snapshot = inspector[0].sandbox(POD_UID)
    assert snapshot.sandbox_id == SANDBOX_ID
    assert snapshot.container_ids == (CONTAINER_ID,)
    assert snapshot.container_states == ("RUNNING",)
    assert snapshot.network_interfaces == ("eth0", "lo")
    assert snapshot.network_addresses == ("127.0.0.1", "192.0.2.1")
    assert snapshot.network_routes == ()
    assert snapshot.network_neighbors == ()
    assert snapshot.cgroup_path == CGROUP
    assert snapshot.cgroup_lookup_complete is True
    assert snapshot.cgroup_present is True
    assert snapshot.cgroup_populated is True
    assert snapshot.descendant_pids == (41, 42)
    assert snapshot.cpu_quota_micros == 50000
    assert snapshot.cpu_period_micros == 100000
    assert snapshot.memory_max_bytes == 134217728
    assert snapshot.pids_max == 64
    assert snapshot.workspace_filesystem == "tmpfs"
    assert snapshot.workspace_size_bytes == 33554432
    assert snapshot.workspace_mount_flags == ("nodev", "noexec", "nosuid", "rw")


@pytest.mark.unit
def test_exact_absent_cgroup_is_bounded_empty_fact_after_cri_exit(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, cgroup = inspector
    command.container_state = "CONTAINER_EXITED"
    for file in cgroup.rglob("*"):
        if file.is_file():
            file.unlink()
    for directory in sorted(
        (item for item in cgroup.rglob("*") if item.is_dir()), reverse=True
    ):
        directory.rmdir()
    cgroup.rmdir()

    snapshot = instance.sandbox(POD_UID, SANDBOX_ID)

    assert snapshot.container_states == ("EXITED",)
    assert snapshot.cgroup_lookup_complete is True
    assert snapshot.cgroup_present is False
    assert snapshot.cgroup_populated is False
    assert snapshot.descendant_pids == ()
    assert snapshot.workspace_size_bytes == 33554432


@pytest.mark.unit
def test_removal_binds_complete_negative_lookups_to_retained_identity(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, cgroup = inspector
    command.sandboxes = []
    for file in cgroup.rglob("*"):
        if file.is_file():
            file.unlink()
    for directory in sorted(
        (item for item in cgroup.rglob("*") if item.is_dir()), reverse=True
    ):
        directory.rmdir()
    cgroup.rmdir()

    snapshot = instance.removal(POD_UID, SANDBOX_ID, CGROUP)

    assert snapshot.sandbox_present is False
    assert snapshot.cgroup_present is False
    assert snapshot.cri_lookup_complete is True
    assert snapshot.cgroup_lookup_complete is True
    assert snapshot.node_uid == NODE_UID


@pytest.mark.unit
def test_sandbox_and_removal_reject_cgroup_traversal(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, _ = inspector
    command.cgroup_parent = "/kubepods.slice/../escaped"

    with pytest.raises(KubernetesRuntimeError, match="cgroup identity"):
        instance.sandbox(POD_UID)
    with pytest.raises(KubernetesRuntimeError, match="cgroup identity"):
        instance.removal(
            POD_UID,
            SANDBOX_ID,
            "/sys/fs/cgroup/kubepods.slice/../escaped",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda api, command: setattr(command, "routes", [{"dst": "0.0.0.0/0"}]),
            "network",
        ),
        (
            lambda api, command: setattr(
                command,
                "interfaces",
                {
                    **cast(dict[str, object], command.interfaces),
                    "veth0": {"Sandbox": "/net"},
                },
            ),
            "network",
        ),
        (
            lambda api, command: setattr(
                command, "container_state", "CONTAINER_UNKNOWN"
            ),
            "state",
        ),
        (lambda api, command: setattr(command, "sandboxes", []), "observable"),
        (lambda api, command: setattr(command, "fail", True), "CRI inspection"),
    ],
)
def test_sandbox_fails_closed_on_incomplete_or_unsafe_observation(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
    mutation: Any,
    message: str,
) -> None:
    instance, api, command, _ = inspector
    mutation(api, command)
    with pytest.raises(KubernetesRuntimeError, match=message):
        instance.sandbox(POD_UID)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        (
            "net/dev",
            "Inter-| Receive | Transmit\n face |bytes |bytes\n"
            " lo: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            " eth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
            " veth0: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n",
        ),
        (
            "net/fib_trie",
            "Main:\n  +-- 0.0.0.0/0\nLocal:\n"
            "  |-- 127.0.0.1\n     /32 host LOCAL\n"
            "  |-- 192.0.2.2\n     /32 host LOCAL\n",
        ),
        (
            "net/route",
            "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
            "eth0 00000000 00000000 0001 0 0 0 00000000 0 0 0\n",
        ),
        (
            "net/ipv6_route",
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00 "
            "00000000000000000000000000000000 00000000 00000000 "
            "00000000 00000001 eth0\n",
        ),
        (
            "net/arp",
            "IP address HW type Flags HW address Mask Device\n"
            "192.0.2.2 0x1 0x2 00:00:00:00:00:01 * eth0\n",
        ),
    ],
)
def test_sandbox_rejects_kernel_network_escape_facts(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
    relative_path: str,
    content: str,
) -> None:
    instance, _, _, cgroup = inspector
    sandbox = cgroup.parents[1].parent / "proc/4321"
    (sandbox / relative_path).write_text(content, encoding="ascii")

    with pytest.raises(KubernetesRuntimeError, match="network"):
        instance.sandbox(POD_UID)


@pytest.mark.unit
@pytest.mark.parametrize(
    "asset",
    [
        "00-markweave-isolated.conflist",
        "markweave-isolated",
        "20-markweave-reverse-runtime.toml",
        "markweave-runc-wrapper",
    ],
)
def test_node_fence_rejects_changed_node_asset(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
    asset: str,
) -> None:
    instance, _, _, cgroup = inspector
    (cgroup.parents[1].parent / asset).write_bytes(b"changed")

    with pytest.raises(KubernetesRuntimeError, match="node fence"):
        instance.node_fence("reverse-node-1")


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["metadata"].update(uid="invalid"),
        lambda value: value["metadata"].update(labels={}),
        lambda value: value["spec"].update(taints=[]),
    ],
)
def test_node_fence_rejects_missing_or_changed_identity(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
    mutation: Any,
) -> None:
    instance, api, _, _ = inspector
    assert isinstance(api.node_value, dict)
    mutation(api.node_value)
    with pytest.raises(KubernetesRuntimeError, match=r"node fence|identity|taint"):
        instance.node_fence("reverse-node-1")


@pytest.mark.unit
def test_node_fence_reports_not_ready_without_treating_it_as_ready(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, api, _, _ = inspector
    assert isinstance(api.node_value, dict)
    api.node_value["status"] = {"conditions": []}
    assert instance.node_fence("reverse-node-1").ready is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda path: InspectorLimits(0, 65536, 1, 1, 1, 1),
        lambda path: InspectorLimits(1, 1, 1, 1, 1, 1),
        lambda path: InspectorConfig(
            "",
            "unix:///run/c.sock",
            path,
            path,
            f"sha256:{'1' * 64}",
            path,
            f"sha256:{'2' * 64}",
            path,
            f"sha256:{'3' * 64}",
            path,
            f"sha256:{'4' * 64}",
            path,
            path,
        ),
        lambda path: InspectorConfig(
            "node",
            "tcp://runtime",
            path,
            path,
            f"sha256:{'1' * 64}",
            path,
            f"sha256:{'2' * 64}",
            path,
            f"sha256:{'3' * 64}",
            path,
            f"sha256:{'4' * 64}",
            path,
            path,
        ),
        lambda path: InspectorConfig(
            "node",
            "unix:///run/c.sock",
            Path("x"),
            path,
            f"sha256:{'1' * 64}",
            path,
            f"sha256:{'2' * 64}",
            path,
            f"sha256:{'3' * 64}",
            path,
            f"sha256:{'4' * 64}",
            path,
            path,
        ),
    ],
)
def test_inspector_configuration_rejects_unbounded_or_ambiguous_values(
    tmp_path: Path, factory: Any
) -> None:
    with pytest.raises(ValueError):
        factory(tmp_path)


@pytest.mark.unit
def test_kubernetes_read_api_serializes_and_closes_failures() -> None:
    class Core:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def read_node(self, *, name: str, _request_timeout: float) -> object:
            self.timeouts.append(_request_timeout)
            return {"metadata": {"name": name}}

        def read_namespaced_pod(
            self, *, name: str, namespace: str, _request_timeout: float
        ) -> object:
            self.timeouts.append(_request_timeout)
            return {"metadata": {"name": name, "namespace": namespace}}

    core = Core()
    api = KubernetesReadApi(core, lambda value: value, operation_seconds=2)
    assert api.node("node")["metadata"] == {"name": "node"}
    assert api.pod("ns", "pod")["metadata"] == {"name": "pod", "namespace": "ns"}
    assert core.timeouts == [2.0, 2.0]
    failed = KubernetesReadApi(Core(), lambda _: [], operation_seconds=2)
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        failed.node("node")


@pytest.mark.unit
def test_cri_command_builder_requires_absolute_binary(
    limits: InspectorLimits, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="executable"):
        build_cri_command(Path("crictl"), limits)
    runner = build_cri_command(tmp_path / "crictl", limits)
    assert callable(runner)


@pytest.mark.unit
def test_read_api_rejects_invalid_adapter_and_closes_client_failure() -> None:
    with pytest.raises(ValueError, match="read API"):
        KubernetesReadApi(object(), lambda value: value, operation_seconds=1)

    class FailedCore:
        def read_node(self, *, name: str, _request_timeout: float) -> object:
            raise RuntimeError(name)

        def read_namespaced_pod(
            self, *, name: str, namespace: str, _request_timeout: float
        ) -> object:
            raise RuntimeError(f"{namespace}/{name}")

    api = KubernetesReadApi(FailedCore(), lambda value: value, operation_seconds=1)
    with pytest.raises(KubernetesRuntimeError, match="read API failed"):
        api.node("sensitive-node")


@pytest.mark.unit
def test_in_cluster_api_assembly_uses_official_client(mocker: Any) -> None:
    core = object()
    api_client = SimpleNamespace(sanitize_for_serialization=lambda value: value)
    client = SimpleNamespace(ApiClient=lambda: api_client, CoreV1Api=lambda value: core)
    config = SimpleNamespace(load_incluster_config=lambda: None)
    importer = mocker.patch.object(
        target, "import_module", side_effect=[client, config]
    )
    mocker.patch.object(KubernetesReadApi, "__init__", return_value=None)

    assert isinstance(
        KubernetesReadApi.in_cluster(operation_seconds=2), KubernetesReadApi
    )
    assert importer.call_count == 2

    mocker.patch.object(target, "import_module", side_effect=RuntimeError("secret"))
    with pytest.raises(KubernetesRuntimeError, match="configuration failed"):
        KubernetesReadApi.in_cluster(operation_seconds=2)


@pytest.mark.unit
def test_inspector_constructor_and_public_identity_inputs_fail_closed(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, api, command, _ = inspector
    with pytest.raises(ValueError, match="inspector is invalid"):
        BoundedCriCgroupInspector(
            cast(InspectorConfig, object()),
            InspectorLimits(1, 4096, 1, 1, 1, 1),
            api,
            command,
        )
    with pytest.raises(KubernetesRuntimeError, match="node fence"):
        instance.node_fence("another-node")
    with pytest.raises(KubernetesRuntimeError, match="sandbox lookup"):
        instance.sandbox(cast(UUID, object()))
    with pytest.raises(KubernetesRuntimeError, match="sandbox lookup"):
        instance.sandbox(POD_UID, "invalid")
    with pytest.raises(KubernetesRuntimeError, match="removal"):
        instance.removal(POD_UID, "invalid", CGROUP)


@pytest.mark.unit
def test_sandbox_rejects_ambiguous_and_substituted_cri_results(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, _ = inspector
    assert isinstance(command.sandboxes, list)
    command.sandboxes.append(deepcopy(command.sandboxes[0]))
    with pytest.raises(KubernetesRuntimeError, match="sandbox lookup"):
        instance.sandbox(POD_UID)
    command.sandboxes.pop()
    command.sandboxes[0]["metadata"]["uid"] = str(UUID(int=2))
    with pytest.raises(KubernetesRuntimeError, match="sandbox lookup"):
        instance.sandbox(POD_UID)


@pytest.mark.unit
def test_sandbox_selects_unique_latest_nonready_attempt(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, _ = inspector
    command.sandbox_state = "SANDBOX_NOTREADY"
    command.container_state = "CONTAINER_EXITED"
    assert isinstance(command.sandboxes, list)
    command.sandboxes[0]["state"] = "SANDBOX_NOTREADY"
    older = deepcopy(command.sandboxes[0])
    older["id"] = "8" * 64
    older["metadata"]["attempt"] = 0
    command.sandboxes[0]["metadata"]["attempt"] = 1
    command.sandboxes.append(older)

    assert instance.sandbox(POD_UID).sandbox_id == SANDBOX_ID


@pytest.mark.unit
def test_container_enumeration_rejects_empty_and_wrong_sandbox(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, _ = inspector
    command.containers = []
    with pytest.raises(KubernetesRuntimeError, match="observable"):
        instance.sandbox(POD_UID)
    command.containers = [
        {
            "id": CONTAINER_ID,
            "podSandboxId": "9" * 64,
            "labels": {"io.kubernetes.pod.uid": str(POD_UID)},
            "state": "CONTAINER_RUNNING",
        }
    ]
    with pytest.raises(KubernetesRuntimeError, match="container lookup"):
        instance.sandbox(POD_UID)


@pytest.mark.unit
def test_cgroup_and_workspace_limits_fail_closed(
    inspector: tuple[BoundedCriCgroupInspector, ApiDouble, CommandDouble, Path],
) -> None:
    instance, _, command, cgroup = inspector
    (cgroup / "pids.max").write_text("max\n", encoding="ascii")
    with pytest.raises(KubernetesRuntimeError, match="unbounded"):
        instance.sandbox(POD_UID)
    (cgroup / "pids.max").write_text("64\n", encoding="ascii")
    (cgroup / "cpu.max").write_text("max 100000\n", encoding="ascii")
    with pytest.raises(KubernetesRuntimeError, match="CPU cgroup"):
        instance.sandbox(POD_UID)
    (cgroup / "cpu.max").write_text("50000 100000\n", encoding="ascii")
    assert isinstance(command.containers, list)
    command.containers.append(deepcopy(command.containers[0]))
    command.containers[1]["id"] = "8" * 64
    with pytest.raises(KubernetesRuntimeError, match="workspace inspection"):
        instance.sandbox(POD_UID)


@pytest.mark.unit
def test_helper_parsers_reject_unbounded_malformed_or_ambiguous_input(
    tmp_path: Path,
) -> None:
    cases = [
        lambda: target._mapping([], "mapping failed"),
        lambda: target._mapping({1: "bad"}, "mapping failed"),
        lambda: target._mapping_items({}, 1),
        lambda: target._mapping_items([{}, {}], 1),
        lambda: target._string_mapping({"key": 1}),
        lambda: target._text(None),
        lambda: target._identifier("not-an-id"),
        lambda: target._uuid("bad", "uuid failed"),
        lambda: target._positive_int(0),
        lambda: target._positive_int("-1"),
        lambda: target._nonnegative_int(-1),
        lambda: target._node_ready({}),
        lambda: target._dedicated_taint({}),
        lambda: target._duration_micros(None),
        lambda: target._duration_micros("1minute"),
        lambda: target._container_state("CONTAINER_UNKNOWN"),
        lambda: target._cpu_max("max 100000"),
        lambda: target._limit("max"),
        lambda: target._populated("frozen 0\n"),
        lambda: target._workspace_size({"spec": {"volumes": []}}),
        lambda: target._mountinfo("unrelated", "/work"),
    ]
    for operation in cases:
        with pytest.raises(KubernetesRuntimeError):
            operation()

    assert target._positive_int("4") == 4
    assert target._nonnegative_int(0) == 0
    assert target._duration_micros("2us") == 2
    assert target._duration_micros("2s") == 2_000_000
    assert target._populated("populated 0\n") is False
    assert target._directory_present(tmp_path / "absent") is False
    regular = tmp_path / "regular"
    regular.write_text("x", encoding="ascii")
    with pytest.raises(KubernetesRuntimeError, match="host inspection"):
        target._directory_present(regular)


@pytest.mark.unit
def test_bounded_file_and_yaml_reads_reject_invalid_material(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(KubernetesRuntimeError, match="host inspection"):
        target._read_text(invalid_utf8)
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text("[", encoding="ascii")
    with pytest.raises(KubernetesRuntimeError, match="kubelet configuration"):
        target._yaml_mapping(invalid_yaml)
    assert target._statvfs(tmp_path).f_blocks > 0
