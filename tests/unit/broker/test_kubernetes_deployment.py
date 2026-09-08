from __future__ import annotations

import json
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
        .replace("@REQUIRED_BROKER_CLIENT_CERTIFICATE_SHA256@", f"sha256:{'2' * 64}")
        .replace("@REQUIRED_ATTESTER_MAX_REQUEST_BYTES@", "262144")
        .replace("@REQUIRED_ATTESTER_MAX_RESPONSE_BYTES@", "65536")
        .replace("@REQUIRED_ATTESTER_REQUEST_TIMEOUT_SECONDS@", "5")
        .replace("@REQUIRED_ATTESTER_MAX_CONCURRENT_REQUESTS@", "8")
        .replace("@REQUIRED_ATTESTER_READINESS_TIMEOUT_SECONDS@", "30")
        .replace("@REQUIRED_ATTESTER_READINESS_POLL_INTERVAL_SECONDS@", "0.1")
        .replace("@REQUIRED_ATTESTER_SERVER_CERTIFICATE_SHA256@", f"sha256:{'3' * 64}")
        .replace("@REQUIRED_POD_SCHEDULING_TIMEOUT_SECONDS@", "30")
        .replace("@REQUIRED_POD_EXEC_TIMEOUT_SECONDS@", "10")
        .replace("@REQUIRED_POD_POLL_INTERVAL_SECONDS@", "0.1")
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
    assert {
        (tuple(rule["resources"]), tuple(sorted(rule["verbs"]))) for rule in rules
    } == {
        (("pods",), ("create", "delete", "get", "list")),
        (("pods/exec",), ("create",)),
    }
    assert not any(item["kind"] == "ClusterRole" for item in resources)
    assert not any(item["kind"] == "ClusterRoleBinding" for item in resources)
    assert not any(item["kind"] == "Service" for item in resources)

    assert not any(
        item["kind"] in {"DaemonSet", "PodDisruptionBudget"} for item in resources
    )

    config = by_kind_name[("ConfigMap", "markweave-node-attester-config")]
    assert config["immutable"] is True
    json.loads(config["data"]["attester.json"])
    assert '"listen_address": "0.0.0.0:9443"' in config["data"]["attester.json"]
    assert '"require_client_certificate": true' in config["data"]["attester.json"]
    assert (
        f'"expected_client_certificate_sha256": "sha256:{"2" * 64}"'
        in (config["data"]["attester.json"])
    )
    tls = by_kind_name[("Secret", "markweave-node-attester-tls")]
    assert tls["immutable"] is True
    assert set(tls["stringData"]) == {"ca.crt", "tls.crt", "tls.key"}
    broker_tls = by_kind_name[("Secret", "markweave-reverse-broker-attester-tls")]
    assert broker_tls["metadata"]["namespace"] == "markweave-reverse"
    assert broker_tls["immutable"] is True
    assert set(broker_tls["stringData"]) == {"ca.crt", "tls.crt", "tls.key"}
    broker_config = by_kind_name[("ConfigMap", "markweave-reverse-broker-kubernetes")]
    assert broker_config["immutable"] is True
    json.loads(broker_config["data"]["kubernetes.json"])
    assert (
        '"namespace": "markweave-reverse"' in broker_config["data"]["kubernetes.json"]
    )
    assert (
        f'"expected_attester_server_certificate_sha256": "sha256:{"3" * 64}"'
        in (broker_config["data"]["kubernetes.json"])
    )


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
