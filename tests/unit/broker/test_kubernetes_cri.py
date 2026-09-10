from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

import grpc
import pytest
from google.protobuf.message import Message

from markweave.broker import _cri_runtime_v1_pb2 as cri
from markweave.broker import kubernetes_cri as target
from markweave.broker.kubernetes_cri import GrpcCriRuntimeClient
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError

POD_UID = UUID("11111111-2222-4333-8444-555555555555")
SANDBOX_ID = "6" * 64
CONTAINER_ID = "7" * 64
UID_LABEL = "io.kubernetes.pod.uid"


class Stub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Message, float]] = []
        metadata = cri.PodSandboxMetadata(
            name="attempt-1",
            uid=str(POD_UID),
            namespace="markweave-reverse",
            attempt=0,
        )
        self.responses: dict[str, Message] = {
            "Version": cri.VersionResponse(runtime_api_version="v1"),
            "ListPodSandbox": cri.ListPodSandboxResponse(
                items=[
                    cri.PodSandbox(
                        id=SANDBOX_ID,
                        metadata=metadata,
                        state=cri.SANDBOX_READY,
                        labels={UID_LABEL: str(POD_UID)},
                    )
                ]
            ),
            "PodSandboxStatus": cri.PodSandboxStatusResponse(
                status=cri.PodSandboxStatus(
                    id=SANDBOX_ID,
                    metadata=metadata,
                    state=cri.SANDBOX_READY,
                    labels={UID_LABEL: str(POD_UID)},
                ),
                info={"info": json.dumps({"pid": 4321, "config": {}})},
            ),
            "ListContainers": cri.ListContainersResponse(
                containers=[
                    cri.Container(
                        id=CONTAINER_ID,
                        pod_sandbox_id=SANDBOX_ID,
                        state=cri.CONTAINER_RUNNING,
                        labels={UID_LABEL: str(POD_UID)},
                    )
                ]
            ),
            "ContainerStatus": cri.ContainerStatusResponse(
                status=cri.ContainerStatus(
                    id=CONTAINER_ID,
                    state=cri.CONTAINER_RUNNING,
                    labels={UID_LABEL: str(POD_UID)},
                ),
                info={"info": json.dumps({"pid": 4322})},
            ),
        }

    def _call(self, name: str, request: Message, *, timeout: float) -> Message:
        self.calls.append((name, request, timeout))
        return self.responses[name]

    def Version(self, request: Message, *, timeout: float) -> Message:
        return self._call("Version", request, timeout=timeout)

    def ListPodSandbox(self, request: Message, *, timeout: float) -> Message:
        return self._call("ListPodSandbox", request, timeout=timeout)

    def PodSandboxStatus(self, request: Message, *, timeout: float) -> Message:
        return self._call("PodSandboxStatus", request, timeout=timeout)

    def ListContainers(self, request: Message, *, timeout: float) -> Message:
        return self._call("ListContainers", request, timeout=timeout)

    def ContainerStatus(self, request: Message, *, timeout: float) -> Message:
        return self._call("ContainerStatus", request, timeout=timeout)


@pytest.mark.unit
def test_client_uses_only_bounded_runtime_service_evidence_reads() -> None:
    stub = Stub()
    client = GrpcCriRuntimeClient(
        cast(Any, stub), operation_seconds=2, output_bytes=4096
    )

    sandboxes = client.list_pod_sandboxes(POD_UID)
    sandbox = client.pod_sandbox_status(SANDBOX_ID)
    containers = client.list_containers(SANDBOX_ID, POD_UID)
    container = client.container_status(CONTAINER_ID)

    assert sandboxes == {
        "items": [
            {
                "id": SANDBOX_ID,
                "metadata": {
                    "name": "attempt-1",
                    "uid": str(POD_UID),
                    "namespace": "markweave-reverse",
                    "attempt": 0,
                },
                "state": "SANDBOX_READY",
                "labels": {UID_LABEL: str(POD_UID)},
            }
        ]
    }
    assert sandbox["info"] == {"pid": 4321, "config": {}}
    assert containers == {
        "containers": [
            {
                "id": CONTAINER_ID,
                "podSandboxId": SANDBOX_ID,
                "state": "CONTAINER_RUNNING",
                "labels": {UID_LABEL: str(POD_UID)},
            }
        ]
    }
    assert container["info"] == {"pid": 4322}
    assert [name for name, _, _ in stub.calls] == [
        "ListPodSandbox",
        "PodSandboxStatus",
        "ListContainers",
        "ContainerStatus",
    ]
    assert all(timeout == 2.0 for _, _, timeout in stub.calls)
    pod_filter = cast(cri.ListPodSandboxRequest, stub.calls[0][1]).filter
    container_filter = cast(cri.ListContainersRequest, stub.calls[2][1]).filter
    assert dict(pod_filter.label_selector) == {UID_LABEL: str(POD_UID)}
    assert container_filter.pod_sandbox_id == SANDBOX_ID
    assert dict(container_filter.label_selector) == {UID_LABEL: str(POD_UID)}
    assert cast(cri.PodSandboxStatusRequest, stub.calls[1][1]).verbose is True
    assert cast(cri.ContainerStatusRequest, stub.calls[3][1]).verbose is True


@pytest.mark.unit
def test_client_rejects_invalid_info_wrong_response_and_oversized_output() -> None:
    stub = Stub()
    client = GrpcCriRuntimeClient(
        cast(Any, stub), operation_seconds=1, output_bytes=4096
    )
    cast(cri.PodSandboxStatusResponse, stub.responses["PodSandboxStatus"]).info[
        "info"
    ] = "not-json"
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.pod_sandbox_status(SANDBOX_ID)

    stub.responses["ContainerStatus"] = cri.VersionResponse(runtime_api_version="v1")
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.container_status(CONTAINER_ID)

    stub.responses["ListContainers"] = cri.VersionResponse(runtime_api_version="v1")
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.list_containers(SANDBOX_ID, POD_UID)

    stub.responses["ListContainers"] = cri.ListContainersResponse(
        containers=[cri.Container(id="8" * 4096)]
    )
    with pytest.raises(KubernetesRuntimeError, match="inspection failed"):
        client.list_containers(SANDBOX_ID, POD_UID)

    stub.responses["ListPodSandbox"] = cri.VersionResponse(runtime_api_version="v1")
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.list_pod_sandboxes(POD_UID)


@pytest.mark.unit
def test_client_rejects_missing_status_and_malformed_decoded_info() -> None:
    stub = Stub()
    client = GrpcCriRuntimeClient(
        cast(Any, stub), operation_seconds=1, output_bytes=4096
    )
    stub.responses["PodSandboxStatus"] = cri.PodSandboxStatusResponse()
    stub.responses["ContainerStatus"] = cri.ContainerStatusResponse()
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.pod_sandbox_status(SANDBOX_ID)
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        client.container_status(CONTAINER_ID)
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        target._pod_metadata(object())
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        target._decode_info({"info": '{"pid": 1}', "pid": "2"})
    assert target._decode_info({"pid": "4321"}) == {"pid": 4321}


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments",
    [
        (object(), 1, 4096),
        (Stub(), 0, 4096),
        (Stub(), float("inf"), 4096),
        (Stub(), 1, 0),
    ],
)
def test_client_rejects_invalid_construction(
    arguments: tuple[object, object, object],
) -> None:
    with pytest.raises(ValueError, match="client is invalid"):
        GrpcCriRuntimeClient(
            cast(Any, arguments[0]),
            operation_seconds=cast(Any, arguments[1]),
            output_bytes=cast(Any, arguments[2]),
        )
    with pytest.raises(ValueError, match="endpoint is invalid"):
        GrpcCriRuntimeClient.connect(
            "tcp://containerd", operation_seconds=1, output_bytes=4096
        )

    GrpcCriRuntimeClient(
        cast(Any, Stub()), operation_seconds=1, output_bytes=4096
    ).close()


@pytest.mark.unit
def test_connect_constructs_exact_five_method_stub_and_closes_invalid_version(
    mocker: Any,
) -> None:
    class Channel:
        def __init__(self) -> None:
            self.paths: list[str] = []
            self.closed = False

        def unary_unary(
            self,
            path: str,
            **_: object,
        ) -> Any:
            self.paths.append(path)

            def call(request: Message, *, timeout: float) -> Message:
                assert isinstance(request, cri.VersionRequest)
                assert timeout == 2.0
                return cri.VersionResponse(runtime_api_version="v1")

            return call

        def close(self) -> None:
            self.closed = True

    channel = Channel()
    factory = mocker.patch.object(grpc, "insecure_channel", return_value=channel)
    ready = mocker.patch.object(grpc, "channel_ready_future").return_value
    client = GrpcCriRuntimeClient.connect(
        "unix:///run/markweave-cri/proxy.sock",
        operation_seconds=2,
        output_bytes=4096,
    )
    assert channel.paths == [
        "/runtime.v1.RuntimeService/Version",
        "/runtime.v1.RuntimeService/ListPodSandbox",
        "/runtime.v1.RuntimeService/PodSandboxStatus",
        "/runtime.v1.RuntimeService/ListContainers",
        "/runtime.v1.RuntimeService/ContainerStatus",
    ]
    assert all("ImageService" not in path for path in channel.paths)
    factory.assert_called_once()
    ready.result.assert_called_once_with(timeout=2)
    client.close()
    assert channel.closed is True

    channel = Channel()

    def invalid_call(*_: object, **__: object) -> cri.VersionResponse:
        return cri.VersionResponse(runtime_api_version="v1alpha2")

    mocker.patch.object(channel, "unary_unary", return_value=invalid_call)
    mocker.patch.object(grpc, "insecure_channel", return_value=channel)
    mocker.patch.object(grpc, "channel_ready_future")
    with pytest.raises(KubernetesRuntimeError, match="connection failed"):
        GrpcCriRuntimeClient.connect(
            "unix:///run/markweave-cri/proxy.sock",
            operation_seconds=2,
            output_bytes=4096,
        )
    assert channel.closed is True


@pytest.mark.unit
def test_minimal_proto_exposes_no_image_or_mutation_service() -> None:
    services = cri.DESCRIPTOR.services_by_name
    assert set(services) == {"RuntimeService"}
    assert [method.name for method in services["RuntimeService"].methods] == [
        "Version",
        "ListPodSandbox",
        "PodSandboxStatus",
        "ListContainers",
        "ContainerStatus",
    ]
