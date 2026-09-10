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
        .replace("@REQUIRED_CRI_PROXY_IMAGE_REPOSITORY@", "registry.example/envoy")
        .replace("@REQUIRED_CRI_PROXY_IMAGE_DIGEST@", f"sha256:{'4' * 64}")
        .replace("@REQUIRED_CNI_CONFIG_SHA256@", f"sha256:{'5' * 64}")
        .replace("@REQUIRED_CNI_PLUGIN_SHA256@", f"sha256:{'6' * 64}")
        .replace("@REQUIRED_RUNTIME_CONFIG_SHA256@", f"sha256:{'7' * 64}")
        .replace("@REQUIRED_RUNTIME_WRAPPER_SHA256@", f"sha256:{'8' * 64}")
        .replace("@REQUIRED_BROKER_CLIENT_CERTIFICATE_SHA256@", f"sha256:{'2' * 64}")
        .replace("@REQUIRED_ATTESTER_MAX_REQUEST_BYTES@", "262144")
        .replace("@REQUIRED_ATTESTER_MAX_RESPONSE_BYTES@", "65536")
        .replace("@REQUIRED_ATTESTER_REQUEST_TIMEOUT_SECONDS@", "5")
        .replace("@REQUIRED_ATTESTER_MAX_CONCURRENT_REQUESTS@", "8")
        .replace("@REQUIRED_ATTESTER_MAX_RECORDS@", "1024")
        .replace("@REQUIRED_ATTESTER_OPERATION_TIMEOUT_SECONDS@", "3")
        .replace("@REQUIRED_ATTESTER_HARD_SHUTDOWN_TIMEOUT_SECONDS@", "10")
        .replace("@REQUIRED_ATTESTER_TERMINATION_GRACE_PERIOD_SECONDS@", "11")
        .replace("@REQUIRED_ATTESTER_MAX_SANDBOXES@", "64")
        .replace("@REQUIRED_ATTESTER_MAX_CONTAINERS@", "8")
        .replace("@REQUIRED_ATTESTER_MAX_CGROUP_DIRECTORIES@", "256")
        .replace("@REQUIRED_ATTESTER_MAX_DESCENDANT_PIDS@", "512")
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
        (("pods/exec",), ("create", "get")),
    }
    cluster_role = by_kind_name[("ClusterRole", "markweave-node-attester-read-node")]
    assert cluster_role["rules"] == [
        {"apiGroups": [""], "resources": ["nodes"], "verbs": ["get"]}
    ]
    pod_role = by_kind_name[("Role", "markweave-node-attester-read-pod")]
    assert pod_role["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get"]}
    ]
    assert len([item for item in resources if item["kind"] == "ClusterRole"]) == 1
    assert (
        len([item for item in resources if item["kind"] == "ClusterRoleBinding"]) == 1
    )
    assert not any(item["kind"] == "Service" for item in resources)

    config = by_kind_name[("ConfigMap", "markweave-node-attester-config")]
    assert config["immutable"] is True
    attester_settings = json.loads(config["data"]["attester.json"])
    assert {
        key: type(attester_settings[key])
        for key in (
            "max_request_bytes",
            "max_response_bytes",
            "request_timeout_seconds",
            "max_concurrent_requests",
        )
    } == {
        "max_request_bytes": int,
        "max_response_bytes": int,
        "request_timeout_seconds": int,
        "max_concurrent_requests": int,
    }
    assert attester_settings["listen_host"] == "0.0.0.0"  # noqa: S104
    assert attester_settings["listen_port"] == 9443
    assert attester_settings["cri_endpoint"] == "unix:///run/markweave-cri/proxy.sock"
    assert {
        key: attester_settings[key]
        for key in (
            "cni_config_sha256",
            "cni_plugin_sha256",
            "runtime_config_sha256",
            "runtime_wrapper_sha256",
        )
    } == {
        "cni_config_sha256": f"sha256:{'5' * 64}",
        "cni_plugin_sha256": f"sha256:{'6' * 64}",
        "runtime_config_sha256": f"sha256:{'7' * 64}",
        "runtime_wrapper_sha256": f"sha256:{'8' * 64}",
    }
    assert attester_settings["cgroup_root"] == "/host/sys/fs/cgroup"
    assert attester_settings["proc_root"] == "/host/proc"
    assert attester_settings["inventory_max_records"] == 1024
    assert (
        f'"expected_client_certificate_sha256": "sha256:{"2" * 64}"'
        in (config["data"]["attester.json"])
    )
    tls = by_kind_name[("Secret", "markweave-node-attester-tls")]
    assert tls["immutable"] is True
    assert set(tls["stringData"]) == {"ca.crt", "tls.crt", "tls.key"}
    authentication = by_kind_name[("Secret", "markweave-node-attester-auth")]
    assert authentication["immutable"] is True
    assert set(authentication["stringData"]) == {"key"}
    broker_tls = by_kind_name[("Secret", "markweave-reverse-broker-attester-tls")]
    assert broker_tls["metadata"]["namespace"] == "markweave-reverse"
    assert broker_tls["immutable"] is True
    assert set(broker_tls["stringData"]) == {"ca.crt", "tls.crt", "tls.key"}
    broker_config = by_kind_name[("ConfigMap", "markweave-reverse-broker-kubernetes")]
    assert broker_config["immutable"] is True
    broker_settings = json.loads(broker_config["data"]["kubernetes.json"])
    assert {
        key: type(broker_settings[key])
        for key in (
            "pod_scheduling_timeout_seconds",
            "pod_exec_timeout_seconds",
            "pod_poll_interval_seconds",
            "attester_max_request_bytes",
            "attester_max_response_bytes",
            "attester_request_timeout_seconds",
            "attester_readiness_timeout_seconds",
            "attester_readiness_poll_interval_seconds",
        )
    } == {
        "pod_scheduling_timeout_seconds": int,
        "pod_exec_timeout_seconds": int,
        "pod_poll_interval_seconds": float,
        "attester_max_request_bytes": int,
        "attester_max_response_bytes": int,
        "attester_request_timeout_seconds": int,
        "attester_readiness_timeout_seconds": int,
        "attester_readiness_poll_interval_seconds": float,
    }
    assert (
        '"namespace": "markweave-reverse"' in broker_config["data"]["kubernetes.json"]
    )
    assert (
        f'"expected_attester_server_certificate_sha256": "sha256:{"3" * 64}"'
        in (broker_config["data"]["kubernetes.json"])
    )


@pytest.mark.unit
def test_attester_daemonset_is_pinned_and_read_only_except_for_its_ledger() -> None:
    resources = _resources()
    daemonset = next(item for item in resources if item["kind"] == "DaemonSet")
    config = next(
        item
        for item in resources
        if item["kind"] == "ConfigMap"
        and item["metadata"]["name"] == "markweave-node-attester-config"
    )
    attester_settings = json.loads(config["data"]["attester.json"])
    pod = daemonset["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "markweave-node-attester"
    assert pod["automountServiceAccountToken"] is False
    assert pod["hostNetwork"] is True
    assert pod["hostPID"] is False
    assert pod["hostIPC"] is False
    assert pod["nodeSelector"] == {
        "reverse.markweave.dev/isolation-pool": "reverse",
        "reverse.markweave.dev/node-fence": "fence-v1",
    }

    containers = {container["name"]: container for container in pod["containers"]}
    container = containers["attester"]
    assert (
        pod["terminationGracePeriodSeconds"]
        > attester_settings["hard_shutdown_timeout_seconds"]
    )
    assert container["image"] == f"registry.example/attester@sha256:{'1' * 64}"
    assert container["ports"] == [
        {"name": "mtls", "containerPort": 9443, "hostPort": 9443, "protocol": "TCP"}
    ]
    security = container["securityContext"]
    assert security == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsGroup": 0,
        "runAsNonRoot": False,
        "runAsUser": 0,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert mounts["state"].get("readOnly", False) is False
    assert "cri-socket" not in mounts
    assert all(
        mount["readOnly"] is True
        for name, mount in mounts.items()
        if name not in {"state", "cri-proxy-socket"}
    )
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["state"]["hostPath"] == {
        "path": "/var/lib/markweave-attester",
        "type": "Directory",
    }
    assert volumes["cri-socket"]["hostPath"]["type"] == "Socket"
    proxy = containers["cri-read-proxy"]
    assert proxy["image"] == f"registry.example/envoy@sha256:{'4' * 64}"
    proxy_mounts = {mount["name"]: mount for mount in proxy["volumeMounts"]}
    assert "cri-socket" in proxy_mounts
    assert "service-account" not in proxy_mounts
    assert proxy["securityContext"] == security


@pytest.mark.unit
def test_cri_proxy_allows_only_exact_read_only_runtime_methods() -> None:
    resources = _resources()
    config = next(
        item
        for item in resources
        if item["kind"] == "ConfigMap"
        and item["metadata"]["name"] == "markweave-cri-read-proxy-config"
    )
    envoy = cast(dict[str, Any], yaml.safe_load(config["data"]["envoy.yaml"]))
    listener = envoy["static_resources"]["listeners"][0]
    manager = listener["filter_chains"][0]["filters"][0]["typed_config"]
    routes = manager["route_config"]["virtual_hosts"][0]["routes"]
    allowed = {route["match"]["path"] for route in routes if "path" in route["match"]}
    assert allowed == {
        "/runtime.v1.RuntimeService/Version",
        "/runtime.v1.RuntimeService/ListPodSandbox",
        "/runtime.v1.RuntimeService/PodSandboxStatus",
        "/runtime.v1.RuntimeService/ListContainers",
        "/runtime.v1.RuntimeService/ContainerStatus",
    }
    assert routes[-1] == {
        "match": {"prefix": "/"},
        "direct_response": {"status": 403},
    }
    source = config["data"]["envoy.yaml"]
    assert not any(
        method in source
        for method in (
            "RunPodSandbox",
            "CreateContainer",
            "StartContainer",
            "StopContainer",
            "RemoveContainer",
            "RemovePodSandbox",
            "Exec",
        )
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
    assert (
        "complete restart/recovery and negative exact-image acceptance matrix" in guide
    )
    assert "physical pool isolation" in guide
