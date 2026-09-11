"""Minimal read-only CRI v1 client for the trusted Kubernetes node attester."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Protocol, cast
from uuid import UUID

import grpc
from google.protobuf.message import Message

from markweave.broker import _cri_runtime_v1_pb2 as cri
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError

_POD_UID_LABEL = "io.kubernetes.pod.uid"
_CRI_VERSION = "v1"


class CriRuntimeApi(Protocol):
    """The four evidence reads consumed by the concrete inspector."""

    def list_pod_sandboxes(self, pod_uid: UUID) -> Mapping[str, object]: ...

    def pod_sandbox_status(self, sandbox_id: str) -> Mapping[str, object]: ...

    def list_containers(
        self, sandbox_id: str, pod_uid: UUID
    ) -> Mapping[str, object]: ...

    def container_status(self, container_id: str) -> Mapping[str, object]: ...


class _UnaryCall(Protocol):
    def __call__(self, request: Message, *, timeout: float) -> Message: ...


class _RuntimeStub(Protocol):
    Version: _UnaryCall
    ListPodSandbox: _UnaryCall
    PodSandboxStatus: _UnaryCall
    ListContainers: _UnaryCall
    ContainerStatus: _UnaryCall


class _FiveMethodRuntimeStub:
    """Generated-message gRPC stub with no mutation or ImageService method."""

    def __init__(self, channel: grpc.Channel) -> None:
        self.Version = _rpc(
            channel,
            "/runtime.v1.RuntimeService/Version",
            cri.VersionRequest,
            cri.VersionResponse,
        )
        self.ListPodSandbox = _rpc(
            channel,
            "/runtime.v1.RuntimeService/ListPodSandbox",
            cri.ListPodSandboxRequest,
            cri.ListPodSandboxResponse,
        )
        self.PodSandboxStatus = _rpc(
            channel,
            "/runtime.v1.RuntimeService/PodSandboxStatus",
            cri.PodSandboxStatusRequest,
            cri.PodSandboxStatusResponse,
        )
        self.ListContainers = _rpc(
            channel,
            "/runtime.v1.RuntimeService/ListContainers",
            cri.ListContainersRequest,
            cri.ListContainersResponse,
        )
        self.ContainerStatus = _rpc(
            channel,
            "/runtime.v1.RuntimeService/ContainerStatus",
            cri.ContainerStatusRequest,
            cri.ContainerStatusResponse,
        )


class GrpcCriRuntimeClient:
    """Bounded CRI client whose generated service contains only approved read RPCs."""

    def __init__(
        self,
        stub: _RuntimeStub,
        *,
        operation_seconds: float,
        output_bytes: int,
        channel: grpc.Channel | None = None,
    ) -> None:
        if (
            not all(
                callable(getattr(stub, name, None))
                for name in (
                    "Version",
                    "ListPodSandbox",
                    "PodSandboxStatus",
                    "ListContainers",
                    "ContainerStatus",
                )
            )
            or type(operation_seconds) not in {int, float}
            or not math.isfinite(operation_seconds)
            or operation_seconds <= 0
            or type(output_bytes) is not int
            or output_bytes <= 0
            or (channel is not None and not callable(getattr(channel, "close", None)))
        ):
            raise ValueError("Kubernetes CRI client is invalid")
        self._stub = stub
        self._timeout = float(operation_seconds)
        self._output_bytes = output_bytes
        self._channel = channel

    @classmethod
    def connect(
        cls,
        endpoint: str,
        *,
        operation_seconds: float,
        output_bytes: int,
    ) -> GrpcCriRuntimeClient:
        """Connect to one Unix endpoint and prove the CRI v1 RuntimeService."""

        if type(endpoint) is not str or not endpoint.startswith("unix:///"):
            raise ValueError("Kubernetes CRI endpoint is invalid")
        channel = grpc.insecure_channel(
            endpoint,
            options=(
                ("grpc.max_receive_message_length", output_bytes),
                ("grpc.max_send_message_length", output_bytes),
            ),
        )
        try:
            grpc.channel_ready_future(channel).result(timeout=operation_seconds)
            client = cls(
                _FiveMethodRuntimeStub(channel),
                operation_seconds=operation_seconds,
                output_bytes=output_bytes,
                channel=channel,
            )
            response = client._invoke(
                client._stub.Version,
                cri.VersionRequest(version=_CRI_VERSION),
            )
            if (
                not isinstance(response, cri.VersionResponse)
                or response.runtime_api_version != _CRI_VERSION
            ):
                raise KubernetesRuntimeError("Kubernetes CRI version is invalid")
            return client
        except Exception:
            channel.close()
            raise KubernetesRuntimeError("Kubernetes CRI connection failed") from None

    def close(self) -> None:
        """Close the owned channel, if this client created one."""

        if self._channel is not None:
            self._channel.close()

    def list_pod_sandboxes(self, pod_uid: UUID) -> Mapping[str, object]:
        response = self._invoke(
            self._stub.ListPodSandbox,
            cri.ListPodSandboxRequest(
                filter=cri.PodSandboxFilter(
                    label_selector={_POD_UID_LABEL: str(pod_uid)}
                )
            ),
        )
        if not isinstance(response, cri.ListPodSandboxResponse):
            raise KubernetesRuntimeError("Kubernetes CRI response is invalid")
        return {
            "items": [
                {
                    "id": item.id,
                    "metadata": _pod_metadata(item.metadata),
                    "state": cri.PodSandboxState.Name(item.state),
                    "labels": dict(item.labels),
                }
                for item in response.items
            ]
        }

    def pod_sandbox_status(self, sandbox_id: str) -> Mapping[str, object]:
        response = self._invoke(
            self._stub.PodSandboxStatus,
            cri.PodSandboxStatusRequest(pod_sandbox_id=sandbox_id, verbose=True),
        )
        if not isinstance(
            response, cri.PodSandboxStatusResponse
        ) or not response.HasField("status"):
            raise KubernetesRuntimeError("Kubernetes CRI response is invalid")
        return {
            "status": {
                "id": response.status.id,
                "metadata": _pod_metadata(response.status.metadata),
                "state": cri.PodSandboxState.Name(response.status.state),
                "labels": dict(response.status.labels),
            },
            "info": _decode_info(response.info),
        }

    def list_containers(self, sandbox_id: str, pod_uid: UUID) -> Mapping[str, object]:
        response = self._invoke(
            self._stub.ListContainers,
            cri.ListContainersRequest(
                filter=cri.ContainerFilter(
                    pod_sandbox_id=sandbox_id,
                    label_selector={_POD_UID_LABEL: str(pod_uid)},
                )
            ),
        )
        if not isinstance(response, cri.ListContainersResponse):
            raise KubernetesRuntimeError("Kubernetes CRI response is invalid")
        return {
            "containers": [
                {
                    "id": item.id,
                    "podSandboxId": item.pod_sandbox_id,
                    "state": cri.ContainerState.Name(item.state),
                    "labels": dict(item.labels),
                }
                for item in response.containers
            ]
        }

    def container_status(self, container_id: str) -> Mapping[str, object]:
        response = self._invoke(
            self._stub.ContainerStatus,
            cri.ContainerStatusRequest(container_id=container_id, verbose=True),
        )
        if not isinstance(
            response, cri.ContainerStatusResponse
        ) or not response.HasField("status"):
            raise KubernetesRuntimeError("Kubernetes CRI response is invalid")
        return {
            "status": {
                "id": response.status.id,
                "state": cri.ContainerState.Name(response.status.state),
                "labels": dict(response.status.labels),
            },
            "info": _decode_info(response.info),
        }

    def _invoke(self, operation: _UnaryCall, request: Message) -> Message:
        try:
            response = operation(request, timeout=self._timeout)
            if (
                not isinstance(response, Message)
                or response.ByteSize() > self._output_bytes
            ):
                raise ValueError
            return response
        except Exception:
            raise KubernetesRuntimeError("Kubernetes CRI inspection failed") from None


def _pod_metadata(value: object) -> Mapping[str, object]:
    if not isinstance(value, cri.PodSandboxMetadata):
        raise KubernetesRuntimeError("Kubernetes CRI response is invalid")
    return {
        "name": value.name,
        "uid": value.uid,
        "namespace": value.namespace,
        "attempt": value.attempt,
    }


def _decode_info(values: Mapping[str, str]) -> Mapping[str, object]:
    result: dict[str, object] = {}
    try:
        for key, raw in values.items():
            value = json.loads(raw)
            if key == "info" and isinstance(value, Mapping):
                if any(type(item) is not str or item in result for item in value):
                    raise ValueError
                result.update(value)
            else:
                if key in result:
                    raise ValueError
                result[key] = value
    except UnicodeError, ValueError, TypeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes CRI response is invalid") from None
    return result


def _rpc(
    channel: grpc.Channel,
    path: str,
    request_type: type[Message],
    response_type: type[Message],
) -> _UnaryCall:
    return cast(
        _UnaryCall,
        channel.unary_unary(
            path,
            request_serializer=request_type.SerializeToString,
            response_deserializer=response_type.FromString,
            _registered_method=True,
        ),
    )
