from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).parents[3]
DEPLOYMENT = ROOT / "deploy" / "reverse-kubernetes.yaml.example"
GUIDE = ROOT / "docs" / "kubernetes-reverse-isolation.md"


def _resources() -> list[dict[str, Any]]:
    rendered = (
        DEPLOYMENT.read_text(encoding="utf-8")
        .replace("@REQUIRED_NODE_FENCE_REVISION@", "fence-v1")
        .replace("@REQUIRED_ATTESTER_IMAGE_REPOSITORY@", "registry.example/attester")
        .replace("@REQUIRED_ATTESTER_IMAGE_DIGEST@", f"sha256:{'1' * 64}")
    )
    return cast(list[dict[str, Any]], list(yaml.safe_load_all(rendered)))


@pytest.mark.unit
def test_reference_deployment_separates_credentials_and_node_authority() -> None:
    resources = _resources()
    by_kind_name = {
        (item["kind"], item["metadata"]["name"]): item for item in resources
    }

    attempt = by_kind_name[("ServiceAccount", "markweave-reverse-attempt")]
    attester = by_kind_name[("ServiceAccount", "markweave-node-attester")]
    assert attempt["automountServiceAccountToken"] is False
    assert attester["automountServiceAccountToken"] is False
    assert attempt["metadata"]["namespace"] == "markweave-reverse"
    assert attester["metadata"]["namespace"] == "markweave-attestation"

    namespaces = {
        item["metadata"]["name"]: item
        for item in resources
        if item["kind"] == "Namespace"
    }
    assert (
        namespaces["markweave-reverse"]["metadata"]["labels"][
            "pod-security.kubernetes.io/enforce"
        ]
        == "restricted"
    )
    assert (
        namespaces["markweave-attestation"]["metadata"]["labels"][
            "pod-security.kubernetes.io/enforce"
        ]
        == "privileged"
    )

    role = by_kind_name[("Role", "markweave-reverse-broker")]
    rules = role["rules"]
    assert {tuple(rule["resources"]) for rule in rules} == {
        ("pods",),
        ("pods/exec",),
    }
    assert all("secrets" not in rule["resources"] for rule in rules)
    assert not any(item["kind"] == "ClusterRole" for item in resources)
    assert not any(item["kind"] == "ClusterRoleBinding" for item in resources)

    daemon = by_kind_name[("DaemonSet", "markweave-node-attester")]
    assert daemon["metadata"]["namespace"] == "markweave-attestation"
    specification = daemon["spec"]["template"]["spec"]
    assert specification["automountServiceAccountToken"] is False
    assert specification["nodeSelector"] == {
        "reverse.markweave.dev/isolation-pool": "reverse",
        "reverse.markweave.dev/node-fence": "fence-v1",
    }
    mounts = {volume["name"]: volume for volume in specification["volumes"]}
    assert mounts["cri"]["hostPath"]["type"] == "Socket"
    assert mounts["cgroup"]["hostPath"]["path"] == "/sys/fs/cgroup"


@pytest.mark.unit
def test_runtime_class_is_pinned_to_the_dedicated_fenced_pool() -> None:
    runtime_class = next(
        item for item in _resources() if item["kind"] == "RuntimeClass"
    )
    scheduling = runtime_class["scheduling"]
    assert scheduling["nodeSelector"] == {
        "reverse.markweave.dev/isolation-pool": "reverse",
        "reverse.markweave.dev/node-fence": "fence-v1",
    }
    assert scheduling["tolerations"] == [
        {
            "effect": "NoSchedule",
            "key": "reverse.markweave.dev/dedicated",
            "operator": "Equal",
            "value": "reverse",
        }
    ]


@pytest.mark.unit
def test_guide_rejects_weak_kubernetes_termination_evidence() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert "NetworkPolicy` alone is not accepted as proof" in guide
    assert "force deletion" in guide
    assert "zero descendants" in guide
    assert "exact broker, attester, and reverse-attempt image digests" in guide
    assert "remain required but unexecuted" in guide
