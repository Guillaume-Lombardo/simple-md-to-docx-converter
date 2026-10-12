"""Qualify the generated SDK and its serializers without claiming cluster support."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.client.rest import RESTResponse
from pytest_mock import MockerFixture
from urllib3.response import HTTPResponse

from markweave.broker import kubernetes_api
from markweave.broker.kubernetes_api import KubernetesApiControlPlane
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError
from tests.unit.broker.test_kubernetes_api import CONFIG, POD_UID, _manifest

pytestmark = pytest.mark.integration


def test_generated_create_preserves_broker_manifest_and_timeout(
    mocker: MockerFixture,
) -> None:
    manifest = _manifest()
    manifest["spec"] = {
        "nodeName": "reverse-node-1",
        "containers": [{"name": "attempt", "image": "example@sha256:" + "a" * 64}],
    }
    with client.ApiClient() as api_client:
        response = RESTResponse(
            HTTPResponse(
                body=json.dumps(manifest).encode(),
                status=201,
                headers={"Content-Type": "application/json"},
            )
        )
        request = mocker.patch.object(
            api_client.rest_client, "request", return_value=response
        )
        control = KubernetesApiControlPlane(client.CoreV1Api(api_client), CONFIG)
        pod = control.create(manifest)
        assert pod.pod_uid == POD_UID
        assert pod.node_name == "reverse-node-1"
        assert request.call_args.args[0] == "POST"
        assert request.call_args.kwargs["body"] == manifest
        assert (
            request.call_args.kwargs["_request_timeout"]
            == CONFIG.scheduling_timeout_seconds
        )


def test_generated_exec_uses_bounded_transport_and_restores_http_client(
    mocker: MockerFixture,
) -> None:
    with client.ApiClient() as api_client:
        original = api_client.call_api
        sock = SimpleNamespace(
            subprotocol="v5.channel.k8s.io", connect=mocker.Mock(), close=mocker.Mock()
        )
        mocker.patch.object(kubernetes_api.ws_client, "WebSocket", return_value=sock)
        session = kubernetes_api._BOUNDED_STREAM(
            client.CoreV1Api(api_client).connect_get_namespaced_pod_exec,
            "attempt",
            CONFIG.namespace,
            command=["python", "-c", "pass"],
            container="attempt",
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
            _request_timeout=CONFIG.exec_timeout_seconds,
        )
        assert isinstance(session, kubernetes_api._BoundedWsClient)
        assert sock.connect.call_args.kwargs["timeout"] == CONFIG.exec_timeout_seconds
        assert sock.connect.call_args.kwargs["subprotocols"] == ["v5.channel.k8s.io"]
        assert "command=python" in sock.connect.call_args.args[0]
        assert api_client.call_api == original
        session.close()


def test_generated_validation_fails_without_transport_or_content_disclosure(
    mocker: MockerFixture,
) -> None:
    manifest = _manifest()
    manifest["spec"] = {"containers": "private invalid manifest"}
    with client.ApiClient() as api_client:
        request = mocker.patch.object(api_client.rest_client, "request")
        control = KubernetesApiControlPlane(client.CoreV1Api(api_client), CONFIG)
        with pytest.raises(
            KubernetesRuntimeError, match=r"^Kubernetes Pod creation failed$"
        ):
            control.create(manifest)
        request.assert_not_called()


def test_generated_exec_restores_transport_after_upgrade_failure(
    mocker: MockerFixture,
) -> None:
    with client.ApiClient() as api_client:
        original = api_client.call_api
        sock = SimpleNamespace(
            connect=mocker.Mock(side_effect=TimeoutError("stalled upgrade"))
        )
        mocker.patch.object(kubernetes_api.ws_client, "WebSocket", return_value=sock)
        with pytest.raises(ApiException):
            kubernetes_api._BOUNDED_STREAM(
                client.CoreV1Api(api_client).connect_get_namespaced_pod_exec,
                "attempt",
                CONFIG.namespace,
                command=["python", "-c", "pass"],
                _preload_content=False,
                _request_timeout=CONFIG.exec_timeout_seconds,
            )
        assert sock.connect.call_args.kwargs["timeout"] == CONFIG.exec_timeout_seconds
        assert api_client.call_api == original
