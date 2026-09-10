from __future__ import annotations

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
