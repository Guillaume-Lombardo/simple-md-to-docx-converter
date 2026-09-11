from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).parents[3]
K3S = ROOT / "deploy" / "k3s"


@pytest.mark.unit
def test_dedicated_node_runtime_and_kubelet_guards_are_explicit() -> None:
    runtime = (K3S / "20-markweave-reverse-runtime.toml").read_text()
    assert "runtimes.markweave-reverse" in runtime
    assert 'runtime_type = "io.containerd.runc.v2"' in runtime
    assert (
        'BinaryName = "/var/lib/rancher/k3s/data/markweave/markweave-runc-wrapper"'
        in runtime
    )
    assert "SystemdCgroup = true" in runtime

    node_rendered = (
        (K3S / "reverse-node-config.yaml.example")
        .read_text()
        .replace("@REQUIRED_NODE_FENCE_REVISION@", "fence-v1")
    )
    config = cast(dict[str, Any], yaml.safe_load(node_rendered))
    assert config["node-label"] == [
        "reverse.markweave.dev/isolation-pool=reverse",
        "reverse.markweave.dev/node-fence=fence-v1",
    ]
    assert config["node-taint"] == [
        "reverse.markweave.dev/dedicated=reverse:NoSchedule"
    ]

    kubelet_rendered = (
        (K3S / "10-markweave-reverse-kubelet.conf.example")
        .read_text()
        .replace("@REQUIRED_POD_PIDS_LIMIT@", "64")
        .replace("@REQUIRED_CPU_CFS_QUOTA_PERIOD@", "100ms")
    )
    kubelet = cast(dict[str, Any], yaml.safe_load(kubelet_rendered))
    assert kubelet == {
        "apiVersion": "kubelet.config.k8s.io/v1beta1",
        "kind": "KubeletConfiguration",
        "podPidsLimit": 64,
        "cpuCFSQuotaPeriod": "100ms",
    }


@pytest.mark.unit
def test_runtime_wrapper_closes_workspace_mount_policy_before_runc_create() -> None:
    wrapper_path = K3S / "markweave-runc-wrapper"
    wrapper = wrapper_path.read_text()

    assert stat.S_IMODE(wrapper_path.stat().st_mode) == 0o755
    assert "REAL_RUNC=/usr/bin/runc" in wrapper
    assert "[ -x /usr/bin/jq ]" in wrapper
    assert "io.containerd.runtime.v2.task/k8s.io/" in wrapper
    assert '[ "${#container_id}" -eq 64 ]' in wrapper
    assert "kubernetes\\\\.io~empty-dir/work" in wrapper
    assert '["rbind", "rprivate", "rw"]' in wrapper
    assert '["nodev", "noexec", "nosuid", "rw"]' in wrapper
    assert 'exec "$REAL_RUNC" "$@"' in wrapper
    assert "diagnostic" not in wrapper
    assert " eval " not in f" {wrapper} "


@pytest.mark.unit
def test_isolated_cni_has_one_fixed_dummy_address_and_no_route_contract() -> None:
    network = json.loads((K3S / "00-markweave-isolated.conflist").read_text())
    assert network == {
        "cniVersion": "1.0.0",
        "name": "markweave-isolated",
        "plugins": [{"type": "markweave-isolated"}],
    }

    plugin_path = K3S / "markweave-isolated"
    plugin = plugin_path.read_text()
    assert stat.S_IMODE(plugin_path.stat().st_mode) == 0o755
    assert 'CNI_IFNAME:-}" = "eth0"' in plugin
    assert "ip link add dev eth0 type dummy" in plugin
    assert "192.0.2.1/32" in plugin
    assert "ip route flush table main" in plugin
    assert "ip -6 route flush table main" in plugin
    assert "ip neighbour flush all" in plugin
    assert "disable_ipv6" in plugin
    assert "ip_forward" in plugin
    assert " eval " not in f" {plugin} "
